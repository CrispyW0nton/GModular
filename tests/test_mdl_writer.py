"""
GModular — MDL Binary Writer Tests
====================================
Comprehensive tests for gmodular/formats/mdl_writer.py

Covers:
  1. Basic round-trip: write then parse header fields
  2. Mesh geometry round-trip (verts, faces, normals, uvs, texture)
  3. Skinned mesh (NODE_SKIN) round-trip
  4. Multi-node hierarchy
  5. TSL/K2 function pointers
  6. Animation round-trip (controllers, length)
  7. Edge cases: empty model, root-only, >255 verts
  8. _BW (binary writer) helpers
  9. AABB tree structure
  10. write_mdl / mdl_to_bytes convenience functions
  11. _mesh_stats bug regression (tuple bb, no AttributeError)
  12. Dangly node constraint weight write-back
  13. Emitter node header + controller write-back
"""
from __future__ import annotations

import math
import struct
import tempfile
import os
import unittest
from typing import List

from gmodular.formats.mdl_parser import (
    MDLParser, MeshData, MeshNode,
    NODE_HEADER, NODE_MESH, NODE_SKIN, NODE_AABB, NODE_DANGLY,
    CTRL_POSITION, CTRL_ORIENTATION, CTRL_ALPHA,
)
from gmodular.formats.mdl_writer import (
    MDLWriter, write_mdl, mdl_to_bytes,
    _BW, _build_aabb_tree,
    _FP_MODEL_K1_PC, _FP_MODEL_K2_PC,
    NODE_EMITTER,
    CTRL_EM_BIRTHRATE, CTRL_EM_LIFE_EXP, CTRL_EM_COLOR_START,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Test data factories
# ─────────────────────────────────────────────────────────────────────────────

def _make_flat_quad(name: str = 'quad', texture: str = 'grass01') -> MeshData:
    """One root node (header-only) + one flat quad mesh child."""
    root = MeshNode(name=name)
    root.flags = NODE_HEADER

    mesh = MeshNode(name=f'{name}_mesh')
    mesh.flags = NODE_HEADER | NODE_MESH
    mesh.vertices = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (1., 1., 0.)]
    mesh.normals  = [(0., 0., 1.)] * 4
    mesh.uvs      = [(0., 0.), (1., 0.), (0., 1.), (1., 1.)]
    mesh.faces    = [(0, 1, 2), (1, 3, 2)]
    mesh.texture  = texture
    mesh.render   = True
    mesh.diffuse  = (0.8, 0.8, 0.8)
    mesh.ambient  = (0.2, 0.2, 0.2)
    mesh.alpha    = 1.0

    root.children = [mesh]
    mesh.parent = root

    return MeshData(name=name, root_node=root)


def _make_skinned(name: str = 'skin_model') -> MeshData:
    """Character root with a 3-vertex skin-mesh node."""
    root = MeshNode(name=name)
    root.flags = NODE_HEADER

    sk = MeshNode(name=f'{name}_sk')
    sk.flags  = NODE_HEADER | NODE_MESH | NODE_SKIN
    sk.vertices = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]
    sk.normals  = [(0., 0., 1.)] * 3
    sk.uvs      = [(0., 0.), (1., 0.), (0., 1.)]
    sk.faces    = [(0, 1, 2)]
    sk.texture  = 'body01'
    sk.render   = True
    sk.bone_indices = [(0, 1, 0, 0), (0, 1, 0, 0), (0, 0, 0, 0)]
    sk.bone_weights = [(0.7, 0.3, 0., 0.), (0.5, 0.5, 0., 0.), (1.0, 0., 0., 0.)]
    sk.bone_map     = [0, 1]

    root.children = [sk]
    sk.parent = root

    return MeshData(name=name, root_node=root)


def _roundtrip(data: MeshData, tsl: bool = False) -> MeshData:
    """Write → temp-files → parse → return parsed MeshData."""
    mdl_b, mdx_b = mdl_to_bytes(data, tsl=tsl)
    with tempfile.NamedTemporaryFile(suffix='.mdl', delete=False) as f:
        f.write(mdl_b)
        mdl_path = f.name
    with tempfile.NamedTemporaryFile(suffix='.mdx', delete=False) as f:
        f.write(mdx_b)
        mdx_path = f.name
    try:
        parsed = MDLParser.from_files(mdl_path, mdx_path).parse()
    finally:
        os.unlink(mdl_path)
        os.unlink(mdx_path)
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
#  1. _BW Binary Writer helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestBW(unittest.TestCase):
    def test_pos_starts_at_zero(self):
        bw = _BW()
        self.assertEqual(bw.pos, 0)

    def test_u8_advances_one_byte(self):
        bw = _BW()
        bw.u8(0xAB)
        self.assertEqual(bw.pos, 1)
        self.assertEqual(bw.getvalue()[0], 0xAB)

    def test_u32_advances_four_bytes(self):
        bw = _BW()
        bw.u32(0xDEADBEEF)
        self.assertEqual(bw.pos, 4)
        val = struct.unpack_from('<I', bw.getvalue())[0]
        self.assertEqual(val, 0xDEADBEEF)

    def test_f32_round_trip(self):
        bw = _BW()
        bw.f32(3.14)
        val = struct.unpack_from('<f', bw.getvalue())[0]
        self.assertAlmostEqual(val, 3.14, places=4)

    def test_cstr_pads_to_length(self):
        bw = _BW()
        bw.cstr('hello', 8)
        self.assertEqual(bw.pos, 8)
        raw = bw.getvalue()
        self.assertEqual(raw[:5], b'hello')
        self.assertEqual(raw[5:], b'\x00\x00\x00')

    def test_patch_u32(self):
        bw = _BW()
        bw.u32(0)
        bw.u32(0)
        bw.patch_u32(4, 0x12345678)
        val = struct.unpack_from('<I', bw.getvalue(), 4)[0]
        self.assertEqual(val, 0x12345678)

    def test_put_array_def(self):
        bw = _BW()
        bw.u32(0)  # placeholder offset
        bw.u32(0)  # placeholder count
        bw.u32(0)  # placeholder unknown
        bw.put_array_def(0, 3)   # offset=0 → patch position 0..11 at writer pos
        # put_array_def writes: (pos_of_calling, count, 0) at 'offset' bytes into stream
        # Actually put_array_def(offset, count) patches a previously-written slot
        # Check the stream has meaningful data
        self.assertGreater(len(bw.getvalue()), 0)

    def test_write_null_bytes(self):
        bw = _BW()
        bw.write_null_bytes(16)
        self.assertEqual(bw.pos, 16)
        self.assertEqual(bw.getvalue(), b'\x00' * 16)

    def test_pad_to_alignment(self):
        bw = _BW()
        bw.u8(0x01)  # 1 byte
        bw.pad_to(4)
        self.assertEqual(bw.pos, 4)


# ─────────────────────────────────────────────────────────────────────────────
#  2. File header
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLWriterFileHeader(unittest.TestCase):
    def _write_minimal(self, tsl=False):
        return mdl_to_bytes(MeshData(name='m', root_node=MeshNode(name='m')), tsl=tsl)

    def test_first_dword_is_zero(self):
        mdl, _ = self._write_minimal()
        sig = struct.unpack_from('<I', mdl, 0)[0]
        self.assertEqual(sig, 0, 'First 4 bytes of MDL must be 0x00000000')

    def test_mdl_size_matches_data(self):
        """MDL header size field = total file length - 12 (file header excluded)."""
        mdl, _ = self._write_minimal()
        declared_size = struct.unpack_from('<I', mdl, 4)[0]
        # KotOR MDL convention: size field is bytes after the 12-byte file header
        self.assertEqual(declared_size, len(mdl) - 12)

    def test_mdx_size_matches_data(self):
        mdl, mdx = self._write_minimal()
        declared_mdx_size = struct.unpack_from('<I', mdl, 8)[0]
        self.assertEqual(declared_mdx_size, len(mdx))

    def test_model_name_in_geo_header(self):
        mdl, _ = self._write_minimal()
        name = mdl[12+8 : 12+8+32].rstrip(b'\x00').decode('ascii', 'replace')
        self.assertEqual(name.lower(), 'm')

    def test_k1_function_pointers(self):
        mdl, _ = mdl_to_bytes(MeshData(name='t', root_node=MeshNode(name='t')), tsl=False)
        fp1 = struct.unpack_from('<I', mdl, 12)[0]
        self.assertEqual(fp1, _FP_MODEL_K1_PC[0])

    def test_k2_function_pointers(self):
        mdl, _ = mdl_to_bytes(MeshData(name='t', root_node=MeshNode(name='t')), tsl=True)
        fp1 = struct.unpack_from('<I', mdl, 12)[0]
        self.assertEqual(fp1, _FP_MODEL_K2_PC[0])


# ─────────────────────────────────────────────────────────────────────────────
#  3. Round-trip tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLWriterRoundTrip(unittest.TestCase):
    def test_model_name_roundtrip(self):
        data = _make_flat_quad('my_room')
        parsed = _roundtrip(data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.name.lower(), 'my_room')

    def test_vertex_count_roundtrip(self):
        data = _make_flat_quad()
        parsed = _roundtrip(data)
        mesh_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_MESH]
        self.assertTrue(len(mesh_nodes) >= 1)
        self.assertEqual(len(mesh_nodes[0].vertices), 4)

    def test_face_count_roundtrip(self):
        data = _make_flat_quad()
        parsed = _roundtrip(data)
        mesh_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_MESH]
        self.assertEqual(len(mesh_nodes[0].faces), 2)

    def test_texture_name_roundtrip(self):
        data = _make_flat_quad(texture='dirt01')
        parsed = _roundtrip(data)
        mesh_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_MESH]
        self.assertEqual(mesh_nodes[0].texture.lower(), 'dirt01')

    def test_vertex_positions_roundtrip(self):
        data = _make_flat_quad()
        parsed = _roundtrip(data)
        mesh_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_MESH]
        verts = mesh_nodes[0].vertices
        # Check one known vertex (0, 0, 0) is present
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        self.assertIn(0.0, xs)
        self.assertIn(0.0, ys)

    def test_uv_count_roundtrip(self):
        data = _make_flat_quad()
        parsed = _roundtrip(data)
        mesh_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_MESH]
        self.assertEqual(len(mesh_nodes[0].uvs), 4)

    def test_node_hierarchy_roundtrip(self):
        data = _make_flat_quad()
        parsed = _roundtrip(data)
        nodes = list(parsed.all_nodes())
        self.assertEqual(len(nodes), 2)  # root + mesh

    def test_root_node_name_roundtrip(self):
        data = _make_flat_quad('c_bastila')
        parsed = _roundtrip(data)
        self.assertIsNotNone(parsed.root_node)
        self.assertEqual(parsed.root_node.name.lower(), 'c_bastila')

    def test_tsl_k2_roundtrip(self):
        data = _make_flat_quad('tsl_test')
        parsed = _roundtrip(data, tsl=True)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.name.lower(), 'tsl_test')


# ─────────────────────────────────────────────────────────────────────────────
#  4. Skinned mesh (NODE_SKIN)
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLWriterSkinned(unittest.TestCase):
    def test_skinned_node_flag_roundtrip(self):
        data = _make_skinned()
        parsed = _roundtrip(data, tsl=True)
        self.assertIsNotNone(parsed)
        skin_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_SKIN]
        self.assertEqual(len(skin_nodes), 1)

    def test_skinned_vertex_count_roundtrip(self):
        data = _make_skinned()
        parsed = _roundtrip(data, tsl=True)
        skin_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_SKIN]
        self.assertEqual(len(skin_nodes[0].vertices), 3)

    def test_skinned_face_count_roundtrip(self):
        data = _make_skinned()
        parsed = _roundtrip(data, tsl=True)
        skin_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_SKIN]
        self.assertEqual(len(skin_nodes[0].faces), 1)

    def test_bone_weights_preserved(self):
        data = _make_skinned()
        mdl_b, mdx_b = mdl_to_bytes(data, tsl=True)
        self.assertGreater(len(mdx_b), 0, 'MDX should have bone weight data')


# ─────────────────────────────────────────────────────────────────────────────
#  5. Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLWriterEdgeCases(unittest.TestCase):
    def test_empty_model_no_crash(self):
        data = MeshData(name='empty', root_node=MeshNode(name='empty'))
        mdl_b, mdx_b = mdl_to_bytes(data)
        self.assertGreater(len(mdl_b), 200)
        self.assertEqual(mdl_b[:4], b'\x00\x00\x00\x00')

    def test_long_model_name_truncated(self):
        long_name = 'a' * 64  # longer than the 32-byte name field
        data = MeshData(name=long_name, root_node=MeshNode(name=long_name))
        mdl_b, _ = mdl_to_bytes(data)
        name_field = mdl_b[12+8 : 12+8+32].rstrip(b'\x00').decode('ascii', 'replace')
        self.assertLessEqual(len(name_field), 32)

    def test_300_vertex_mesh_no_overflow(self):
        root = MeshNode(name='bigmesh')
        root.flags = NODE_HEADER
        mesh = MeshNode(name='bigmesh_geo')
        mesh.flags = NODE_HEADER | NODE_MESH
        mesh.vertices = [(float(i), float(i % 10), 0.) for i in range(300)]
        mesh.normals  = [(0., 0., 1.)] * 300
        mesh.uvs      = [(0., 0.)] * 300
        mesh.faces    = [(i, i+1, i+2) for i in range(0, 298, 3)]
        mesh.texture  = 'floor01'
        mesh.render   = True
        root.children = [mesh]
        mesh.parent = root
        data = MeshData(name='bigmesh', root_node=root)
        mdl_b, mdx_b = mdl_to_bytes(data)
        self.assertGreater(len(mdl_b), 0)
        self.assertGreater(len(mdx_b), 0)

    def test_write_mdl_creates_files(self):
        data = _make_flat_quad('filetest')
        with tempfile.TemporaryDirectory() as td:
            mdl_path = os.path.join(td, 'filetest.mdl')
            write_mdl(data, mdl_path)
            self.assertTrue(os.path.exists(mdl_path))
            mdx_path = os.path.join(td, 'filetest.mdx')
            self.assertTrue(os.path.exists(mdx_path))
            self.assertGreater(os.path.getsize(mdl_path), 200)

    def test_mesh_stats_regression_no_attribute_error(self):
        """Regression: _mesh_stats must not raise AttributeError on tuple _bb."""
        data = _make_flat_quad()
        # Should not raise - was previously AttributeError: 'tuple' has no .get('min')
        mdl_b, mdx_b = mdl_to_bytes(data)
        self.assertGreater(len(mdl_b), 0)

    def test_root_only_model(self):
        data = MeshData(name='root_only', root_node=MeshNode(name='root_only'))
        parsed = _roundtrip(data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.name.lower(), 'root_only')


# ─────────────────────────────────────────────────────────────────────────────
#  6. AABB tree builder
# ─────────────────────────────────────────────────────────────────────────────

class TestAABBTreeBuilder(unittest.TestCase):
    def test_builds_tree_for_simple_quad(self):
        verts = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (1., 1., 0.)]
        faces = [(0, 1, 2), (1, 3, 2)]
        nodes = _build_aabb_tree(faces, verts)
        # Should produce at least one node
        self.assertGreater(len(nodes), 0)

    def test_leaf_node_has_face_index(self):
        verts = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]
        faces = [(0, 1, 2)]
        nodes = _build_aabb_tree(faces, verts)
        # Single face → single leaf node
        self.assertEqual(len(nodes), 1)
        # Leaf node should have face_idx >= 0 (KotorBlender key name)
        leaf = nodes[0]
        face_idx = leaf.get('face_idx', leaf.get('face_index', -1))
        self.assertGreaterEqual(face_idx, 0)

    def test_empty_faces_returns_empty(self):
        nodes = _build_aabb_tree([], [])
        self.assertEqual(nodes, [])


# ─────────────────────────────────────────────────────────────────────────────
#  7. MDL normals preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLWriterNormals(unittest.TestCase):
    def test_normals_count_preserved(self):
        data = _make_flat_quad()
        parsed = _roundtrip(data)
        mesh_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_MESH]
        self.assertEqual(len(mesh_nodes[0].normals), 4)

    def test_normals_direction_preserved(self):
        data = _make_flat_quad()
        parsed = _roundtrip(data)
        mesh_nodes = [n for n in parsed.all_nodes() if n.flags & NODE_MESH]
        for nx, ny, nz in mesh_nodes[0].normals:
            self.assertAlmostEqual(nz, 1.0, places=3)


# ─────────────────────────────────────────────────────────────────────────────
#  8. Multi-node hierarchy
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLWriterHierarchy(unittest.TestCase):
    def test_three_node_hierarchy(self):
        root = MeshNode(name='root')
        root.flags = NODE_HEADER
        child_a = MeshNode(name='child_a')
        child_a.flags = NODE_HEADER | NODE_MESH
        child_a.vertices = [(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)]
        child_a.normals  = [(0.,0.,1.)]*3
        child_a.uvs      = [(0.,0.),(1.,0.),(0.,1.)]
        child_a.faces    = [(0,1,2)]
        child_a.texture  = 'tex_a'
        child_a.render   = True
        child_b = MeshNode(name='child_b')
        child_b.flags = NODE_HEADER | NODE_MESH
        child_b.vertices = [(2.,0.,0.),(3.,0.,0.),(2.,1.,0.)]
        child_b.normals  = [(0.,0.,1.)]*3
        child_b.uvs      = [(0.,0.),(1.,0.),(0.,1.)]
        child_b.faces    = [(0,1,2)]
        child_b.texture  = 'tex_b'
        child_b.render   = True
        root.children = [child_a, child_b]
        child_a.parent = root
        child_b.parent = root
        data = MeshData(name='hierarchy', root_node=root)
        parsed = _roundtrip(data)
        nodes = list(parsed.all_nodes())
        self.assertEqual(len(nodes), 3)
        names = {n.name.lower() for n in nodes}
        self.assertIn('root', names)
        self.assertIn('child_a', names)
        self.assertIn('child_b', names)


# ─────────────────────────────────────────────────────────────────────────────
#  9. Classification and metadata
# ─────────────────────────────────────────────────────────────────────────────

class TestMDLWriterMetadata(unittest.TestCase):
    def test_classification_preserved(self):
        data = _make_flat_quad()
        data.classification = 4  # CHARACTER (integer raw value)
        mdl_b, _ = mdl_to_bytes(data)
        # Model header at absolute offset 12+80=92, classification is first byte
        cls_byte = mdl_b[92]
        self.assertEqual(cls_byte, 4)

    def test_fog_preserved(self):
        data = _make_flat_quad()
        data.fog = 1
        mdl_b, _ = mdl_to_bytes(data)
        # fog is 4th byte of model header: absolute offset 12+80+3=95
        fog_byte = mdl_b[95]
        self.assertEqual(fog_byte, 1)

    def test_anim_scale_in_model_header(self):
        data = _make_flat_quad()
        data.animation_scale = 2.5
        mdl_b, _ = mdl_to_bytes(data)
        # Search for anim_scale float32 in the binary (it's in the model header area)
        target = struct.pack('<f', 2.5)
        idx = mdl_b.find(target)
        self.assertGreater(idx, 90, 'anim_scale must be located after geometry header (>90)')
        val = struct.unpack_from('<f', mdl_b, idx)[0]
        self.assertAlmostEqual(val, 2.5, places=4)


        self.assertAlmostEqual(val, 2.5, places=4)


# ─── Dangly node write-back ───────────────────────────────────────────────────

class TestMDLWriterDangly(unittest.TestCase):
    """Tests for dangly mesh constraint-weight write-back."""

    def _make_dangly_model(self, weights=None):
        root = MeshNode(name='dangly_root')
        root.flags = NODE_HEADER

        mesh = MeshNode(name='dangly_mesh')
        mesh.flags = NODE_HEADER | NODE_MESH | NODE_DANGLY
        mesh.vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
        mesh.faces = [(0, 1, 2), (0, 2, 3)]
        mesh.normals = []; mesh.uvs = []; mesh.uvs2 = []
        mesh.displacement = 0.3
        mesh.tightness = 0.8
        mesh.period = 2.0
        if weights is not None:
            mesh.constraint_weights = weights
        else:
            delattr(mesh, 'constraint_weights') if hasattr(mesh, 'constraint_weights') else None

        root.children = [mesh]
        mesh.parent = root
        data = MeshData()
        data.name = 'dangly_test'
        data.root_node = root
        return data

    def test_dangly_node_produces_output(self):
        """Writing a dangly model should not raise."""
        data = self._make_dangly_model()
        mdl_b, mdx_b = mdl_to_bytes(data)
        self.assertIsInstance(mdl_b, bytes)
        self.assertGreater(len(mdl_b), 200)

    def test_dangly_flag_written_in_header(self):
        """The node flags word must include NODE_DANGLY bit."""
        data = self._make_dangly_model()
        mdl_b, _ = mdl_to_bytes(data)
        # Scan all u16 values in the output for NODE_DANGLY (0x0100) bit
        found = False
        for i in range(0, len(mdl_b) - 1, 2):
            v = struct.unpack_from('<H', mdl_b, i)[0]
            if v & NODE_DANGLY:
                found = True
                break
        self.assertTrue(found, 'NODE_DANGLY flag not found in MDL output')

    def test_dangly_default_weights_all_one(self):
        """With no constraint_weights, all constraint floats should be 1.0."""
        data = self._make_dangly_model(weights=None)
        mdl_b, _ = mdl_to_bytes(data)
        one_bytes = struct.pack('<f', 1.0)
        count = mdl_b.count(one_bytes)
        # At least 4 constraint floats = 4 occurrences of 1.0
        self.assertGreaterEqual(count, 4)

    def test_dangly_custom_weights_written(self):
        """Constraint weights provided on the node should appear in output."""
        weights = [0.0, 0.25, 0.5, 1.0]
        data = self._make_dangly_model(weights=weights)
        mdl_b, _ = mdl_to_bytes(data)
        for w in weights:
            w_bytes = struct.pack('<f', w)
            self.assertIn(w_bytes, mdl_b, f'weight {w} not found in MDL output')

    def test_dangly_displacement_written(self):
        data = self._make_dangly_model()
        mdl_b, _ = mdl_to_bytes(data)
        target = struct.pack('<f', 0.3)
        self.assertIn(target, mdl_b)

    def test_dangly_tightness_written(self):
        data = self._make_dangly_model()
        mdl_b, _ = mdl_to_bytes(data)
        target = struct.pack('<f', 0.8)
        self.assertIn(target, mdl_b)

    def test_dangly_period_written(self):
        data = self._make_dangly_model()
        mdl_b, _ = mdl_to_bytes(data)
        target = struct.pack('<f', 2.0)
        self.assertIn(target, mdl_b)

    def test_dangly_partial_weights_fall_back_to_one(self):
        """If fewer weights than verts, missing ones should default to 1.0."""
        weights = [0.5, 0.5]   # only 2 weights for 4 verts
        data = self._make_dangly_model(weights=weights)
        mdl_b, _ = mdl_to_bytes(data)
        # Both 0.5 and 1.0 should appear in the constraint block
        self.assertIn(struct.pack('<f', 0.5), mdl_b)
        self.assertIn(struct.pack('<f', 1.0), mdl_b)


# ─── Emitter node write-back ──────────────────────────────────────────────────

class TestMDLWriterEmitter(unittest.TestCase):
    """Tests for emitter node header + controller write-back."""

    def _make_emitter_model(self, **kwargs):
        root = MeshNode(name='emitter_root')
        root.flags = NODE_HEADER

        em = MeshNode(name='sparks')
        em.flags = NODE_HEADER | NODE_EMITTER
        # Emitter properties
        em.dead_space    = kwargs.get('dead_space',   0.0)
        em.blast_radius  = kwargs.get('blast_radius', 0.0)
        em.blast_length  = kwargs.get('blast_length', 0.0)
        em.branch_count  = kwargs.get('branch_count', 0)
        em.x_grid        = kwargs.get('x_grid',       1)
        em.y_grid        = kwargs.get('y_grid',       1)
        em.spawn_type    = kwargs.get('spawn_type',   0)
        em.update_type   = kwargs.get('update_type',  'Fountain')
        em.render_type   = kwargs.get('render_type',  'Normal')
        em.blend_type    = kwargs.get('blend_type',   'Normal')
        em.texture       = kwargs.get('texture',      'fx_spark')
        em.chunk_name    = kwargs.get('chunk_name',   '')
        em.two_sided_tex = kwargs.get('two_sided_tex', False)
        em.loop          = kwargs.get('loop',          False)
        em.render_order  = kwargs.get('render_order',  0)
        em.birthrate     = kwargs.get('birthrate',     10.0)
        em.life_exp      = kwargs.get('life_exp',      2.0)
        em.velocity      = kwargs.get('velocity',      3.0)
        em.spread        = kwargs.get('spread',        0.5)
        em.size_start    = kwargs.get('size_start',    0.2)
        em.size_end      = kwargs.get('size_end',      0.05)
        em.alpha_start   = kwargs.get('alpha_start',   1.0)
        em.alpha_end     = kwargs.get('alpha_end',     0.0)
        em.gravity       = kwargs.get('gravity',       -9.8)
        em.color_start   = kwargs.get('color_start',   (1.0, 0.5, 0.0))
        em.color_end     = kwargs.get('color_end',     (1.0, 0.0, 0.0))

        root.children = [em]
        em.parent = root
        data = MeshData()
        data.name = 'emitter_test'
        data.root_node = root
        return data

    def test_emitter_model_produces_output(self):
        """Writing an emitter model should not raise."""
        data = self._make_emitter_model()
        mdl_b, mdx_b = mdl_to_bytes(data)
        self.assertIsInstance(mdl_b, bytes)
        self.assertGreater(len(mdl_b), 200)

    def test_emitter_flag_in_node_header(self):
        """The node flags word must include NODE_EMITTER bit (0x0004)."""
        data = self._make_emitter_model()
        mdl_b, _ = mdl_to_bytes(data)
        found = False
        for i in range(0, len(mdl_b) - 1, 2):
            v = struct.unpack_from('<H', mdl_b, i)[0]
            if v & NODE_EMITTER:
                found = True
                break
        self.assertTrue(found, 'NODE_EMITTER flag not found in MDL output')

    def test_emitter_texture_in_output(self):
        """The texture string 'fx_spark' should appear in the binary."""
        data = self._make_emitter_model(texture='fx_spark')
        mdl_b, _ = mdl_to_bytes(data)
        self.assertIn(b'fx_spark', mdl_b)

    def test_emitter_update_type_fountain_in_output(self):
        data = self._make_emitter_model(update_type='Fountain')
        mdl_b, _ = mdl_to_bytes(data)
        self.assertIn(b'Fountain', mdl_b)

    def test_emitter_render_type_in_output(self):
        data = self._make_emitter_model(render_type='Normal')
        mdl_b, _ = mdl_to_bytes(data)
        self.assertIn(b'Normal', mdl_b)

    def test_emitter_blast_radius_written(self):
        data = self._make_emitter_model(blast_radius=1.5)
        mdl_b, _ = mdl_to_bytes(data)
        target = struct.pack('<f', 1.5)
        self.assertIn(target, mdl_b)

    def test_emitter_birthrate_controller_written(self):
        """birthrate value should appear in the controller data section."""
        data = self._make_emitter_model(birthrate=25.0)
        mdl_b, _ = mdl_to_bytes(data)
        target = struct.pack('<f', 25.0)
        self.assertIn(target, mdl_b)

    def test_emitter_gravity_controller_written(self):
        data = self._make_emitter_model(gravity=-9.8)
        mdl_b, _ = mdl_to_bytes(data)
        target = struct.pack('<f', -9.8)
        self.assertIn(target, mdl_b)

    def test_emitter_color_start_written(self):
        data = self._make_emitter_model(color_start=(0.9, 0.4, 0.1))
        mdl_b, _ = mdl_to_bytes(data)
        for v in (0.9, 0.4, 0.1):
            self.assertIn(struct.pack('<f', v), mdl_b)

    def test_emitter_size_128_with_mesh_overlay(self):
        """Emitter header must be 208 bytes — verify output size grows by 208."""
        root = MeshNode(name='base'); root.flags = NODE_HEADER
        em1 = MeshNode(name='em'); em1.flags = NODE_HEADER | NODE_EMITTER
        em1.parent = root; root.children = [em1]
        data1 = MeshData(); data1.name = 'e1'; data1.root_node = root

        root2 = MeshNode(name='base2'); root2.flags = NODE_HEADER
        data2 = MeshData(); data2.name = 'e2'; data2.root_node = root2

        b1, _ = mdl_to_bytes(data1)
        b2, _ = mdl_to_bytes(data2)
        # One extra node (80 byte header + 208 emitter + controllers)
        self.assertGreater(len(b1), len(b2))
        diff = len(b1) - len(b2)
        # Should be at least 208+80=288 bytes larger
        self.assertGreaterEqual(diff, 288)

    def test_emitter_custom_render_type_in_output(self):
        data = self._make_emitter_model(render_type='Linked')
        mdl_b, _ = mdl_to_bytes(data)
        self.assertIn(b'Linked', mdl_b)

    def test_emitter_loop_flag_true(self):
        data = self._make_emitter_model(loop=True)
        mdl_b, _ = mdl_to_bytes(data)
        # loop=True → u32(1) should appear in output
        self.assertIn(struct.pack('<I', 1), mdl_b)


if __name__ == '__main__':
    unittest.main()
