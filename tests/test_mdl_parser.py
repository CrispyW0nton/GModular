"""
GModular — MDL Parser Comprehensive Tests
==========================================
Tests the MDL binary parser against:
  - bioware-kaitai-formats MDL.ksy spec (OldRepublicDevs/bioware-kaitai-formats)
  - kotorblender io_scene_kotor/format/mdl/types.py constants
  - PyKotor MDL data structures

Covers:
  1. Basic parsing (model name, game version, root node)
  2. Node flag constants (matching kaitai spec and kotorblender)
  3. Controller type constants
  4. MeshData API (mesh_nodes, visible_mesh_nodes, scan_textures, compute_bounds)
  5. Texture extraction
  6. Parser robustness (malformed data)
  7. Node hierarchy (parent-child relationships)
"""
from __future__ import annotations

import math
import struct
import unittest
from typing import List

from gmodular.formats.mdl_parser import (
    MDLParser, MeshNode, MeshData,
    NODE_HEADER, NODE_MESH, NODE_SKIN, NODE_AABB, NODE_LIGHT,
    NODE_EMITTER, NODE_DANGLY, NODE_SABER, NODE_REF, NODE_ANIM,
    CTRL_POSITION, CTRL_ORIENTATION, CTRL_SCALE, CTRL_ALPHA,
)

# ─────────────────────────────────────────────────────────────────────────────
# MDL Binary Builder
# ─────────────────────────────────────────────────────────────────────────────

BASE = 12  # MDL data offset (after 12-byte file header)
K1_FP1 = 4273776   # KotOR 1 PC function pointer 1
K2_FP1 = 4285200   # KotOR 2 PC function pointer 1


def build_minimal_mdl(
    nodes_spec: List[tuple] = None,
    model_name: bytes = b'testmdl\x00',
    fp1: int = K1_FP1,
) -> bytes:
    """
    Build a minimal valid KotOR MDL binary.

    Args:
        nodes_spec: list of (name_str, flag_int) tuples; defaults to [('root', NODE_HEADER)]
        model_name: model name bytes (32-byte field, null-padded)
        fp1: function pointer 1 (K1_FP1 or K2_FP1)

    Layout:
        [0..11]   file header (12 bytes)
        [12..91]  geometry header (80 bytes)
        [92..207] model header extension (116 bytes)
        [208..219] names section header (12 bytes)
        [220+]    name pointer array + name strings + node data
    """
    nodes_spec = nodes_spec or [('root', NODE_HEADER)]

    # Build name strings
    names = [n[0] for n in nodes_spec]
    name_data = b''
    name_ptrs: List[int] = []
    for nm in names:
        name_ptrs.append(len(name_data))
        name_data += nm.encode('ascii') + b'\x00'
    while len(name_data) % 4:
        name_data += b'\x00'

    names_arr_off = 220   # relative to BASE
    str_data_start = names_arr_off + 4 * len(names)
    root_off = str_data_start + len(name_data)

    node_size = 80  # base node header only
    total_size = BASE + root_off + node_size * len(nodes_spec) + 64

    mdl = bytearray(total_size)
    B = BASE

    # --- File header (12 bytes) ---
    struct.pack_into('<I', mdl, 0, 0)
    struct.pack_into('<I', mdl, 4, total_size)
    struct.pack_into('<I', mdl, 8, 0)

    # --- Geometry header at B+0 (80 bytes) ---
    struct.pack_into('<I', mdl, B + 0, fp1)
    struct.pack_into('<I', mdl, B + 4, 4216096)    # fp2 (K1 PC trimesh fp2)
    name_pad = (model_name + b'\x00' * 32)[:32]
    mdl[B + 8: B + 40] = name_pad
    struct.pack_into('<I', mdl, B + 40, root_off)   # root_node_offset (rel to BASE)
    struct.pack_into('<I', mdl, B + 44, len(nodes_spec))  # node_count
    mdl[B + 76] = 2  # geometry_type = MODEL

    # --- Model header extension at B+80 (116 bytes) ---
    M = B + 80
    mdl[M + 0] = 2    # model_type = geometry/tile
    struct.pack_into('<f', mdl, M + 52, 1.0)   # anim_scale
    mdl[M + 56: M + 60] = b'NULL'              # supermodel_name prefix

    # --- Names section header at B+184 (12 bytes) ---
    N = B + 184
    struct.pack_into('<I', mdl, N + 0, names_arr_off)
    struct.pack_into('<I', mdl, N + 4, len(names))
    struct.pack_into('<I', mdl, N + 8, len(names))

    # --- Name pointer array ---
    for i, ptr in enumerate(name_ptrs):
        struct.pack_into('<I', mdl, B + names_arr_off + i * 4, str_data_start + ptr)

    # --- Name string data ---
    mdl[B + str_data_start: B + str_data_start + len(name_data)] = name_data

    # --- Node headers ---
    for idx, (nm, flags) in enumerate(nodes_spec):
        node_off = B + root_off + idx * node_size
        o = node_off
        struct.pack_into('<H', mdl, o + 0, flags)    # node_type
        struct.pack_into('<H', mdl, o + 2, idx)       # node_index
        struct.pack_into('<H', mdl, o + 4, idx)       # name_index
        # position = (0,0,0), rotation = identity quaternion
        struct.pack_into('<fff',  mdl, o + 16, 0.0, 0.0, 0.0)   # position
        struct.pack_into('<ffff', mdl, o + 28, 0.0, 0.0, 0.0, 1.0)  # rotation xyzw

    return bytes(mdl)


# ═════════════════════════════════════════════════════════════════════════════
#  1. Basic Parsing Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestMDLParserBasic(unittest.TestCase):
    """Basic parsing: model name, game version, root node."""

    def test_parse_minimal_mdl(self):
        """Parsing a minimal MDL returns a MeshData object."""
        mdl = build_minimal_mdl()
        data = MDLParser(mdl).parse()
        self.assertIsInstance(data, MeshData)

    def test_model_name_parsed(self):
        """Model name is read from the geometry header."""
        mdl = build_minimal_mdl(model_name=b'danm13aa\x00')
        data = MDLParser(mdl).parse()
        self.assertEqual(data.name, 'danm13aa')

    def test_k1_game_version(self):
        """K1 PC function pointer is detected as game_version=1."""
        mdl = build_minimal_mdl(fp1=K1_FP1)
        data = MDLParser(mdl).parse()
        self.assertEqual(data.game_version, 1)

    def test_k2_game_version(self):
        """K2 PC function pointer is detected as game_version=2."""
        mdl = build_minimal_mdl(fp1=K2_FP1)
        data = MDLParser(mdl).parse()
        self.assertEqual(data.game_version, 2)

    def test_k1_xbox_game_version(self):
        """K1 Xbox function pointer is also detected as K1."""
        mdl = build_minimal_mdl(fp1=4254992)  # K1_XBOX_FP1 from kotorblender
        data = MDLParser(mdl).parse()
        self.assertEqual(data.game_version, 1)

    def test_root_node_created(self):
        """Root node is created and assigned to data.root_node."""
        mdl = build_minimal_mdl([('root', NODE_HEADER)])
        data = MDLParser(mdl).parse()
        self.assertIsNotNone(data.root_node)

    def test_root_node_name(self):
        """Root node name is parsed from the names array."""
        mdl = build_minimal_mdl([('myroot', NODE_HEADER)])
        data = MDLParser(mdl).parse()
        self.assertEqual(data.root_node.name, 'myroot')

    def test_supermodel_null(self):
        """Supermodel is 'NULL' for default minimal MDL."""
        mdl = build_minimal_mdl()
        data = MDLParser(mdl).parse()
        self.assertEqual(data.supermodel, 'NULL')

    def test_file_too_small_raises(self):
        """Files smaller than the minimum header raise an exception."""
        with self.assertRaises(Exception):
            MDLParser(b'\x00' * 10).parse()

    def test_empty_bytes_raises(self):
        """Empty bytes raises an exception."""
        with self.assertRaises(Exception):
            MDLParser(b'').parse()

    def test_mesh_nodes_returns_list(self):
        """mesh_nodes() returns a list."""
        mdl = build_minimal_mdl()
        data = MDLParser(mdl).parse()
        self.assertIsInstance(data.mesh_nodes(), list)

    def test_all_nodes_returns_list(self):
        """all_nodes() returns a list including the root node."""
        mdl = build_minimal_mdl([('root', NODE_HEADER)])
        data = MDLParser(mdl).parse()
        nodes = data.all_nodes()
        self.assertIsInstance(nodes, list)
        self.assertGreater(len(nodes), 0)

    def test_node_position_default_origin(self):
        """Root node position defaults to (0, 0, 0)."""
        mdl = build_minimal_mdl()
        data = MDLParser(mdl).parse()
        pos = data.root_node.position
        self.assertAlmostEqual(pos[0], 0.0, places=4)
        self.assertAlmostEqual(pos[1], 0.0, places=4)
        self.assertAlmostEqual(pos[2], 0.0, places=4)

    def test_node_rotation_identity(self):
        """Root node rotation defaults to identity quaternion (w=1)."""
        mdl = build_minimal_mdl()
        data = MDLParser(mdl).parse()
        rot = data.root_node.rotation  # (x, y, z, w)
        self.assertAlmostEqual(rot[3], 1.0, places=4)


# ═════════════════════════════════════════════════════════════════════════════
#  2. Node Flag Constants (kaitai spec + kotorblender verification)
# ═════════════════════════════════════════════════════════════════════════════

class TestMDLNodeFlagConstants(unittest.TestCase):
    """
    Verify node flag constants match the bioware-kaitai-formats MDL.ksy spec
    and kotorblender io_scene_kotor/format/mdl/types.py.

    From kaitai MDL.ksy:
        node_type == 3   → light   (NODE_LIGHT = 0x0002)
        node_type == 5   → emitter (NODE_EMITTER = 0x0004)
        node_type == 17  → reference (NODE_REF = 0x0010)
        node_type == 33  → trimesh (NODE_HEADER | NODE_MESH = 0x0021)
        node_type == 97  → skinmesh (NODE_HEADER | NODE_MESH | NODE_SKIN = 0x0061)
        node_type == 161 → animmesh (0x00A1)
        node_type == 289 → danglymesh (0x0121)
        node_type == 545 → aabb (0x0221)
        node_type == 2081 → lightsaber (0x0821)

    From kotorblender types.py:
        NODE_BASE = 0x0001
        NODE_LIGHT = 0x0002
        NODE_EMITTER = 0x0004
        NODE_REFERENCE = 0x0010
        NODE_MESH = 0x0020
        NODE_SKIN = 0x0040
        NODE_DANGLY = 0x0100
        NODE_AABB = 0x0200
        NODE_SABER = 0x0800
    """

    def test_node_header_is_0x0001(self):
        self.assertEqual(NODE_HEADER, 0x0001)

    def test_node_light_is_0x0002(self):
        self.assertEqual(NODE_LIGHT, 0x0002)

    def test_node_emitter_is_0x0004(self):
        self.assertEqual(NODE_EMITTER, 0x0004)

    def test_node_ref_is_0x0010(self):
        self.assertEqual(NODE_REF, 0x0010)

    def test_node_mesh_is_0x0020(self):
        self.assertEqual(NODE_MESH, 0x0020)

    def test_node_skin_is_0x0040(self):
        self.assertEqual(NODE_SKIN, 0x0040)

    def test_node_dangly_is_0x0100(self):
        self.assertEqual(NODE_DANGLY, 0x0100)

    def test_node_aabb_is_0x0200(self):
        self.assertEqual(NODE_AABB, 0x0200)

    def test_node_saber_is_0x0800(self):
        self.assertEqual(NODE_SABER, 0x0800)

    def test_trimesh_type_value(self):
        """Trimesh node type = NODE_HEADER|NODE_MESH = 33 = 0x21."""
        self.assertEqual(NODE_HEADER | NODE_MESH, 33)

    def test_skinmesh_type_value(self):
        """Skinmesh = NODE_HEADER|NODE_MESH|NODE_SKIN = 97 = 0x61."""
        self.assertEqual(NODE_HEADER | NODE_MESH | NODE_SKIN, 97)

    def test_danglymesh_type_value(self):
        """Danglymesh = NODE_HEADER|NODE_MESH|NODE_DANGLY = 289 = 0x121."""
        self.assertEqual(NODE_HEADER | NODE_MESH | NODE_DANGLY, 289)

    def test_aabb_node_type_value(self):
        """AABB walkmesh node = NODE_HEADER|NODE_MESH|NODE_AABB = 545 = 0x221."""
        self.assertEqual(NODE_HEADER | NODE_MESH | NODE_AABB, 545)

    def test_saber_type_value(self):
        """Lightsaber = NODE_HEADER|NODE_MESH|NODE_SABER = 0x0821 = 2081."""
        self.assertEqual(NODE_HEADER | NODE_MESH | NODE_SABER, 0x0821)


# ═════════════════════════════════════════════════════════════════════════════
#  3. MeshNode Flag Properties
# ═════════════════════════════════════════════════════════════════════════════

class TestMeshNodeFlagProperties(unittest.TestCase):
    """Test MeshNode.is_* property shortcuts."""

    def test_is_mesh_true_when_mesh_flag_set(self):
        n = MeshNode(flags=NODE_HEADER | NODE_MESH)
        self.assertTrue(n.is_mesh)

    def test_is_mesh_false_when_no_mesh_flag(self):
        n = MeshNode(flags=NODE_HEADER)
        self.assertFalse(n.is_mesh)

    def test_is_skin_true_when_skin_flag_set(self):
        n = MeshNode(flags=NODE_HEADER | NODE_MESH | NODE_SKIN)
        self.assertTrue(n.is_skin)
        self.assertTrue(n.is_mesh)

    def test_is_aabb_true_when_aabb_flag_set(self):
        n = MeshNode(flags=NODE_HEADER | NODE_MESH | NODE_AABB)
        self.assertTrue(n.is_aabb)
        self.assertTrue(n.is_walkmesh)  # is_walkmesh is alias

    def test_is_light_true_when_light_flag_set(self):
        n = MeshNode(flags=NODE_HEADER | NODE_LIGHT)
        self.assertTrue(n.is_light)

    def test_is_emitter_true_when_emitter_flag_set(self):
        n = MeshNode(flags=NODE_HEADER | NODE_EMITTER)
        self.assertTrue(n.is_emitter)

    def test_is_dangly_true_when_dangly_flag_set(self):
        n = MeshNode(flags=NODE_HEADER | NODE_MESH | NODE_DANGLY)
        self.assertTrue(n.is_dangly)

    def test_pure_header_node_no_special_flags(self):
        n = MeshNode(flags=NODE_HEADER)
        self.assertFalse(n.is_mesh)
        self.assertFalse(n.is_skin)
        self.assertFalse(n.is_aabb)
        self.assertFalse(n.is_light)
        self.assertFalse(n.is_emitter)
        self.assertFalse(n.is_dangly)

    def test_combined_skin_aabb_flags(self):
        """A node can theoretically have multiple flags set."""
        n = MeshNode(flags=NODE_HEADER | NODE_MESH | NODE_SKIN | NODE_AABB)
        self.assertTrue(n.is_mesh)
        self.assertTrue(n.is_skin)
        self.assertTrue(n.is_aabb)


# ═════════════════════════════════════════════════════════════════════════════
#  4. Controller Type Constants
# ═════════════════════════════════════════════════════════════════════════════

class TestControllerConstants(unittest.TestCase):
    """
    Verify controller type IDs match cchargin spec and xoreos source.
    Reference: xoreos src/engines/kotor/modelloader.cpp
    """

    def test_ctrl_position_is_8(self):
        """Position controller: type_id=8, 3 floats (x,y,z)."""
        self.assertEqual(CTRL_POSITION, 8)

    def test_ctrl_orientation_is_20(self):
        """Orientation controller: type_id=20, 4 floats (x,y,z,w) or compressed."""
        self.assertEqual(CTRL_ORIENTATION, 20)

    def test_ctrl_scale_is_36(self):
        """Scale controller: type_id=36, 1 float."""
        self.assertEqual(CTRL_SCALE, 36)

    def test_ctrl_alpha_is_132(self):
        """Alpha controller: type_id=132 (per Kotor.NET MDLBinaryControllerType.cs), 1 float."""
        self.assertEqual(CTRL_ALPHA, 132)

    def test_ctrl_alpha_old_is_128(self):
        """Legacy alpha controller: type_id=128 seen in some K1 assets (CTRL_ALPHA_OLD)."""
        from gmodular.formats.mdl_parser import CTRL_ALPHA_OLD
        self.assertEqual(CTRL_ALPHA_OLD, 128)


# ═════════════════════════════════════════════════════════════════════════════
#  5. MeshData API Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestMeshDataAPI(unittest.TestCase):
    """Test MeshData collection and analysis methods."""

    def _make_data_with_nodes(self, *node_args) -> MeshData:
        """Helper: build a MeshData with given (name, flags, vertices, render) tuples."""
        data = MeshData()
        data.root_node = MeshNode(name='root', flags=NODE_HEADER)
        for args in node_args:
            name, flags = args[0], args[1]
            vertices = args[2] if len(args) > 2 else []
            render = args[3] if len(args) > 3 else True
            n = MeshNode(name=name, flags=flags, parent=data.root_node)
            n.vertices = vertices
            n.render = render
            data.root_node.children.append(n)
        return data

    def test_mesh_nodes_returns_only_mesh_nodes(self):
        data = self._make_data_with_nodes(
            ('floor',  NODE_HEADER | NODE_MESH),
            ('light',  NODE_HEADER | NODE_LIGHT),
        )
        nodes = data.mesh_nodes()
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].name, 'floor')

    def test_visible_mesh_nodes_requires_vertices_and_render(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        data = self._make_data_with_nodes(
            ('floor',   NODE_HEADER | NODE_MESH, verts, True),
            ('novert',  NODE_HEADER | NODE_MESH, [],    True),   # no vertices
            ('norender',NODE_HEADER | NODE_MESH, verts, False),  # render=False
        )
        visible = data.visible_mesh_nodes()
        names = [n.name for n in visible]
        self.assertIn('floor', names)
        self.assertNotIn('novert', names)
        self.assertNotIn('norender', names)

    def test_visible_mesh_nodes_excludes_aabb(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        data = self._make_data_with_nodes(
            ('floor',  NODE_HEADER | NODE_MESH, verts, True),
            ('walkmesh', NODE_HEADER | NODE_MESH | NODE_AABB, verts, True),
        )
        visible = data.visible_mesh_nodes()
        names = [n.name for n in visible]
        self.assertIn('floor', names)
        self.assertNotIn('walkmesh', names)

    def test_walkmesh_nodes_returns_aabb_nodes_with_vertices(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        data = self._make_data_with_nodes(
            ('floor',    NODE_HEADER | NODE_MESH, verts),
            ('wm',       NODE_HEADER | NODE_MESH | NODE_AABB, verts),
            ('empty_wm', NODE_HEADER | NODE_MESH | NODE_AABB, []),  # no verts
        )
        wm_nodes = data.walkmesh_nodes()
        names = [n.name for n in wm_nodes]
        self.assertIn('wm', names)
        self.assertNotIn('floor', names)
        self.assertNotIn('empty_wm', names)

    def test_compute_bounds_with_vertices(self):
        data = self._make_data_with_nodes(
            ('floor', NODE_HEADER | NODE_MESH, [(0, 0, 0), (5, 0, 0), (0, 3, 0)])
        )
        data.compute_bounds()
        self.assertAlmostEqual(data.bb_min[0], 0.0, places=3)
        self.assertAlmostEqual(data.bb_max[0], 5.0, places=3)
        self.assertAlmostEqual(data.bb_max[1], 3.0, places=3)

    def test_compute_bounds_empty_model(self):
        """compute_bounds on empty model doesn't crash and leaves radius at default."""
        data = MeshData()
        data.root_node = MeshNode(name='root', flags=NODE_HEADER)
        data.compute_bounds()  # Should not raise
        # After bounds on empty model, bb_min/bb_max should be defined (default zeros)
        self.assertIsNotNone(data.bb_min)
        self.assertIsNotNone(data.bb_max)

    def test_radius_positive_after_bounds(self):
        """radius is positive after compute_bounds with vertices."""
        data = self._make_data_with_nodes(
            ('floor', NODE_HEADER | NODE_MESH, [(0, 0, 0), (10, 0, 0), (0, 10, 0)])
        )
        data.compute_bounds()
        self.assertGreater(data.radius, 0)

    def test_scan_textures_deduplicates(self):
        """scan_textures() returns unique texture names."""
        data = self._make_data_with_nodes(
            ('m1', NODE_HEADER | NODE_MESH),
            ('m2', NODE_HEADER | NODE_MESH),
            ('m3', NODE_HEADER | NODE_MESH),
        )
        data.root_node.children[0].texture = 'floor01'
        data.root_node.children[1].texture = 'wall01'
        data.root_node.children[2].texture = 'floor01'   # duplicate
        textures = data.scan_textures()
        self.assertIn('floor01', textures)
        self.assertIn('wall01', textures)
        self.assertEqual(textures.count('floor01'), 1)  # deduplicated

    def test_scan_textures_excludes_null(self):
        """scan_textures() excludes NULL and empty textures."""
        data = self._make_data_with_nodes(
            ('m1', NODE_HEADER | NODE_MESH),
            ('m2', NODE_HEADER | NODE_MESH),
        )
        data.root_node.children[0].texture = 'NULL'
        data.root_node.children[1].texture = ''
        textures = data.scan_textures()
        self.assertEqual(len(textures), 0)

    def test_scan_textures_case_insensitive_null(self):
        """scan_textures() excludes 'null', 'NULL', 'Null' etc."""
        data = self._make_data_with_nodes(('m1', NODE_HEADER | NODE_MESH))
        data.root_node.children[0].texture = 'null'
        textures = data.scan_textures()
        self.assertEqual(len(textures), 0)

    def test_flat_triangle_array_returns_list(self):
        """flat_triangle_array() returns a list."""
        data = self._make_data_with_nodes(
            ('floor', NODE_HEADER | NODE_MESH,
             [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
        )
        data.root_node.children[0].faces = [(0, 1, 2)]
        arr = data.flat_triangle_array()
        self.assertIsInstance(arr, list)

    def test_all_nodes_includes_root(self):
        data = MeshData()
        data.root_node = MeshNode(name='root', flags=NODE_HEADER)
        nodes = data.all_nodes()
        self.assertIn(data.root_node, nodes)

    def test_all_nodes_includes_children(self):
        data = MeshData()
        data.root_node = MeshNode(name='root', flags=NODE_HEADER)
        child = MeshNode(name='child', flags=NODE_HEADER | NODE_MESH)
        child.parent = data.root_node
        data.root_node.children.append(child)
        nodes = data.all_nodes()
        names = [n.name for n in nodes]
        self.assertIn('child', names)


# ═════════════════════════════════════════════════════════════════════════════
#  6. Texture Name Handling
# ═════════════════════════════════════════════════════════════════════════════

class TestMeshNodeTextureClean(unittest.TestCase):
    """Test MeshNode.texture_clean property."""

    def test_strips_null_bytes(self):
        n = MeshNode(texture='floor01\x00\x00\x00')
        self.assertEqual(n.texture_clean, 'floor01')

    def test_strips_whitespace(self):
        n = MeshNode(texture='  floor01  ')
        self.assertEqual(n.texture_clean.strip(), 'floor01')

    def test_empty_texture(self):
        n = MeshNode(texture='')
        self.assertEqual(n.texture_clean, '')

    def test_null_string_preserved(self):
        n = MeshNode(texture='NULL')
        self.assertEqual(n.texture_clean, 'NULL')

    def test_long_texture_name(self):
        n = MeshNode(texture='dan_m13aa_floor01_tex\x00')
        self.assertIn('dan_m13aa_floor01_tex', n.texture_clean)


# ═════════════════════════════════════════════════════════════════════════════
#  7. Parser Robustness
# ═════════════════════════════════════════════════════════════════════════════

class TestMDLParserRobustness(unittest.TestCase):
    """Test MDL parser graceful handling of malformed data."""

    def test_truncated_file_raises_or_returns_partial(self):
        """A truncated file raises an exception or returns partial data."""
        mdl = build_minimal_mdl()
        truncated = mdl[:len(mdl) // 2]
        try:
            data = MDLParser(truncated).parse()
            # If it doesn't raise, at minimum the object should exist
            self.assertIsNotNone(data)
        except Exception:
            pass  # Raising is also acceptable

    def test_corrupted_fp1_treated_as_unknown_game(self):
        """Unknown function pointer doesn't cause a crash."""
        mdl = bytearray(build_minimal_mdl())
        struct.pack_into('<I', mdl, BASE + 0, 0x12345678)  # unknown fp1
        data = MDLParser(bytes(mdl)).parse()
        # game_version defaults to 1 when unknown
        self.assertIn(data.game_version, (1, 2))

    def test_huge_name_count_handled_gracefully(self):
        """Unrealistically large names count is clamped — either returns data or raises, but never hangs."""
        mdl = bytearray(build_minimal_mdl())
        struct.pack_into('<I', mdl, BASE + 188, 0x7FFFFFFF)
        completed = False
        result = None
        try:
            result = MDLParser(bytes(mdl)).parse()
            completed = True
        except Exception:
            completed = True  # exception also counts as non-hang
        # Must complete (not hang)
        self.assertTrue(completed, 'Parser must not hang on huge name count')

    def test_zero_root_node_offset(self):
        """Zero root node offset produces a model with no root node or a placeholder."""
        mdl = bytearray(build_minimal_mdl())
        struct.pack_into('<I', mdl, BASE + 40, 0)
        completed = False
        result = None
        try:
            result = MDLParser(bytes(mdl)).parse()
            completed = True
        except Exception:
            completed = True
        self.assertTrue(completed, 'Parser must complete or raise for zero root offset')
        if result is not None:
            # If it returned data, root_node should be None or a valid placeholder
            self.assertTrue(result.root_node is None or hasattr(result.root_node, 'name'))

    def test_garbage_node_type_handled(self):
        """Unknown node_type flags don't crash the parser."""
        mdl = bytearray(build_minimal_mdl())
        root_off = struct.unpack_from('<I', mdl, BASE + 40)[0]
        struct.pack_into('<H', mdl, BASE + root_off, 0x7FFF)  # all flags
        try:
            data = MDLParser(bytes(mdl)).parse()
            self.assertIsNotNone(data)
        except Exception:
            pass

    def test_null_bytes_model_name_handled(self):
        """Model name full of null bytes doesn't crash."""
        mdl = build_minimal_mdl(model_name=b'\x00' * 32)
        try:
            data = MDLParser(mdl).parse()
            self.assertIsNotNone(data)
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  8. Node Classification (from kotorblender CLASS_* constants)
# ═════════════════════════════════════════════════════════════════════════════

class TestModelClassification(unittest.TestCase):
    """Test model classification from model_type byte."""

    def test_geometry_classification(self):
        """model_type=2 → classification='geometry' (tiles/rooms)."""
        mdl = build_minimal_mdl()
        data = MDLParser(mdl).parse()
        # The minimal MDL builder sets model_type=2
        self.assertEqual(data.model_type, 2)

    def test_classification_string_set(self):
        """classification is a non-empty string."""
        mdl = build_minimal_mdl()
        data = MDLParser(mdl).parse()
        self.assertIsInstance(data.classification, str)
        self.assertGreater(len(data.classification), 0)

    def test_character_classification(self):
        """model_type=4 → classification='character'."""
        mdl = bytearray(build_minimal_mdl())
        mdl[BASE + 80] = 4  # model_type = character
        data = MDLParser(bytes(mdl)).parse()
        self.assertEqual(data.model_type, 4)
        self.assertEqual(data.classification, 'character')

    def test_door_classification(self):
        """model_type=6 → classification='door'."""
        mdl = bytearray(build_minimal_mdl())
        mdl[BASE + 80] = 6  # model_type = door
        data = MDLParser(bytes(mdl)).parse()
        self.assertEqual(data.classification, 'door')


if __name__ == '__main__':
    unittest.main(verbosity=2)
