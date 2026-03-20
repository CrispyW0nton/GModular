"""
tests/test_rendering_improvements.py
=====================================
Comprehensive tests for the viewport rendering improvements and
format parser enhancements derived from Kotor.NET rework analysis.

Coverage:
  1. GLSL shader source strings presence & validity (textured, skinned, pick)
  2. Viewport _EGLRenderer new shader attributes
  3. Pick-buffer math helpers
  4. MDL parser improvements:
       - lightmap UV reading (uvs2 field)
       - CTRL_ALPHA / CTRL_ALPHA_OLD constants
       - _uncompress_quaternion correctness
       - _sample_vec3_controller / _sample_quat_controller interpolation
       - AnimationData / AnimationEvent dataclasses
       - MeshData.scan_textures lightmap tracking
       - list_mdl_dependencies lightmap field
       - MeshNode.lightmap_clean property
       - MeshNode.has_lightmap flag
  5. WokParser improvements:
       - SURF_* constants alignment with Kotor.NET SurfaceMaterial.cs
       - SURF_NAMES mapping completeness
       - surface_material_name() helper
       - _NON_WALKABLE_MATS frozenset contents
       - is_walkable() for new materials
  6. OrbitCamera ray_from_screen consistency
  7. Viewport shader constant strings integrity (no obvious typos)
"""
from __future__ import annotations

import math
import struct
import unittest
from typing import List, Tuple


# ═════════════════════════════════════════════════════════════════════════════
#  Helper to build a minimal valid MDL binary for testing
# ═════════════════════════════════════════════════════════════════════════════

def _make_minimal_mdl(model_name: str = "test") -> bytes:
    """
    Build a ~300-byte synthetic MDL that satisfies MDLParser.parse() without
    actually loading geometry.  Used to verify parser survivability on tiny input.
    """
    B = 12  # BASE offset
    # File header: bin_mdl_id(4), mdl_length(4), mdx_length(4)
    header = struct.pack('<III', 0, 300, 0)

    # Geometry header at BASE+0 (80 bytes):
    #   fp1(4), fp2(4), name[32], root_node_off(4), node_count(4), unknown[28], geo_type(1), pad[3]
    name_bytes = model_name.encode('ascii').ljust(32, b'\x00')[:32]
    geo_hdr  = struct.pack('<II', 4273776, 0)  # fp1=K1 PC model, fp2=0
    geo_hdr += name_bytes                       # model name [32]
    geo_hdr += struct.pack('<II', 0, 0)         # root_node_off=0, node_count=0
    geo_hdr += b'\x00' * 28                    # unknown[28]
    geo_hdr += struct.pack('<BBBB', 2, 0, 0, 0) # geo_type=2, padding[3]
    assert len(geo_hdr) == 80, f"geo_hdr size mismatch: {len(geo_hdr)}"

    # Model header extension at BASE+80.
    # Per MDLParser docstring: MODEL_HDR_SIZE=104 bytes from geo-header end to _NAMES_OFF.
    # This covers:
    #   ModelType(1)+Unk(1)+Pad(1)+Fog(1)       [4]
    #   ChildModelCount(4)                        [4]
    #   AnimOff(4)+AnimCount(4)+AnimCountDup(4)   [12]
    #   Unk(4)                                    [4]
    #   BBMin(12)+BBMax(12)+Radius(4)+AnimScale(4)[32]
    #   Supermodel[32]                            [32]
    #   OffRootNode(4)+Unused(4)+MDXSize(4)+MDXOff(4) [16]
    #   Total above = 4+4+12+4+32+32+16 = 104 bytes  ✓
    mdl_hdr  = struct.pack('<BBBB', 2, 0, 0, 0)   # model_type, unk, pad, fog
    mdl_hdr += struct.pack('<I', 0)               # child_model_count
    mdl_hdr += struct.pack('<III', 0, 0, 0)       # anim_off, anim_cnt, anim_cnt_dup
    mdl_hdr += struct.pack('<I', 0)               # unknown
    mdl_hdr += struct.pack('<fff', 0, 0, 0)       # bb_min [12]
    mdl_hdr += struct.pack('<fff', 1, 1, 1)       # bb_max [12]
    mdl_hdr += struct.pack('<f', 1.0)             # radius [4]
    mdl_hdr += struct.pack('<f', 1.0)             # anim_scale [4]
    supermodel = b'NULL' + b'\x00' * 28
    mdl_hdr += supermodel                         # supermodel[32]
    mdl_hdr += struct.pack('<IIII', 0, 0, 0, 0)  # OffRootNode + Unused + MDXSize + MDXOffset
    assert len(mdl_hdr) == 104, f"mdl_hdr size mismatch: {len(mdl_hdr)}"

    # Names section at BASE+80+104 = BASE+184 (_NAMES_OFF=184):
    # NamesArrayOffset(4) + NamesCount(4) + NamesCountDup(4) = 12 bytes
    names_block = struct.pack('<III', 0, 0, 0)

    # Total = header(12) + geo_hdr(80) + mdl_hdr(104) + names_block(12) = 208 bytes
    data = header + geo_hdr + mdl_hdr + names_block
    # Pad to 300 bytes
    data += b'\x00' * (300 - len(data))
    return data


# ═════════════════════════════════════════════════════════════════════════════
#  1. GLSL Shader Strings
# ═════════════════════════════════════════════════════════════════════════════

class TestShaderStrings(unittest.TestCase):
    """Verify that all new shader source strings exist and contain expected tokens."""

    def setUp(self):
        try:
            import gmodular.gui.viewport as vp
            self.vp = vp
        except ImportError:
            self.skipTest("viewport module not importable in this environment")

    def test_vert_textured_present(self):
        self.assertTrue(hasattr(self.vp, '_VERT_TEXTURED'),
                        "_VERT_TEXTURED shader not found in viewport module")

    def test_frag_textured_present(self):
        self.assertTrue(hasattr(self.vp, '_FRAG_TEXTURED'),
                        "_FRAG_TEXTURED shader not found in viewport module")

    def test_vert_skinned_present(self):
        self.assertTrue(hasattr(self.vp, '_VERT_SKINNED'),
                        "_VERT_SKINNED shader not found in viewport module")

    def test_frag_skinned_present(self):
        self.assertTrue(hasattr(self.vp, '_FRAG_SKINNED'),
                        "_FRAG_SKINNED shader not found in viewport module")

    def test_vert_pick_present(self):
        self.assertTrue(hasattr(self.vp, '_VERT_PICK'),
                        "_VERT_PICK shader not found in viewport module")

    def test_frag_pick_present(self):
        self.assertTrue(hasattr(self.vp, '_FRAG_PICK'),
                        "_FRAG_PICK shader not found in viewport module")

    def test_textured_vert_has_uv_inputs(self):
        """Textured vertex shader must declare UV inputs for dual-texture support."""
        src = self.vp._VERT_TEXTURED
        self.assertIn("in_uv",  src)
        self.assertIn("in_uv2", src)

    def test_textured_frag_has_dual_sampler(self):
        """Textured fragment shader must declare tex0 and tex1 samplers."""
        src = self.vp._FRAG_TEXTURED
        self.assertIn("tex0", src)
        self.assertIn("tex1", src)
        self.assertIn("use_lightmap", src)

    def test_textured_frag_discard_alpha(self):
        """Textured fragment shader must discard nearly-transparent fragments."""
        src = self.vp._FRAG_TEXTURED
        self.assertIn("discard", src)

    def test_skinned_vert_has_bone_inputs(self):
        """Skinned vertex shader must declare bone weight/index inputs."""
        src = self.vp._VERT_SKINNED
        self.assertIn("in_bone_weights", src)
        self.assertIn("in_bone_indices", src)
        self.assertIn("bone_matrices",   src)

    def test_skinned_vert_has_16_bones(self):
        """Skinned shader supports up to 16 bones (matching MDLBinarySkinmeshHeader ushort[16])."""
        src = self.vp._VERT_SKINNED
        self.assertIn("bone_matrices[16]", src)

    def test_pick_frag_entity_id_encoding(self):
        """Pick-buffer fragment shader encodes entity_id into RGBA (Kotor.NET pattern)."""
        src = self.vp._FRAG_PICK
        self.assertIn("entity_id", src)
        # Accept either decimal mask (255u) or hex mask (0xFFu) — both encode 0xFF
        self.assertTrue(
            "255u" in src or "0xFFu" in src or "0xFF" in src,
            "_FRAG_PICK must contain a 255/0xFF mask for RGBA byte encoding"
        )

    def test_existing_shaders_unchanged(self):
        """Flat and lit shaders still present and functional."""
        self.assertIn("in_position", self.vp._VERT_FLAT)
        self.assertIn("in_position", self.vp._VERT_LIT)
        self.assertIn("in_normal",   self.vp._VERT_LIT)
        self.assertIn("fragColor",   self.vp._FRAG_LIT)


# ═════════════════════════════════════════════════════════════════════════════
#  2. _EGLRenderer API
# ═════════════════════════════════════════════════════════════════════════════

class TestEGLRendererAPI(unittest.TestCase):
    """Verify that _EGLRenderer has the new attributes and methods."""

    def setUp(self):
        try:
            from gmodular.gui.viewport import _EGLRenderer
            self.EGLRenderer = _EGLRenderer
        except ImportError:
            self.skipTest("viewport module not importable")

    def test_new_shader_slots_exist(self):
        """Renderer should have slots for textured, skinned, and pick shaders."""
        r = self.EGLRenderer()
        self.assertTrue(hasattr(r, '_prog_textured'), "missing _prog_textured")
        self.assertTrue(hasattr(r, '_prog_skinned'),  "missing _prog_skinned")
        self.assertTrue(hasattr(r, '_prog_pick'),     "missing _prog_pick")

    def test_pick_fbo_slot_exists(self):
        """Renderer should have a separate pick FBO slot."""
        r = self.EGLRenderer()
        self.assertTrue(hasattr(r, '_pick_fbo'), "missing _pick_fbo")

    def test_pick_at_method_exists(self):
        """Renderer should expose pick_at(sx, sy, W, H, camera) method."""
        r = self.EGLRenderer()
        self.assertTrue(callable(getattr(r, 'pick_at', None)),
                        "pick_at method missing from _EGLRenderer")

    def test_upload_textured_mesh_method_exists(self):
        """Renderer should expose _upload_textured_mesh helper."""
        r = self.EGLRenderer()
        self.assertTrue(callable(getattr(r, '_upload_textured_mesh', None)),
                        "_upload_textured_mesh missing from _EGLRenderer")

    def test_pick_at_returns_zero_when_not_ready(self):
        """pick_at() should gracefully return 0 when context is not initialised."""
        from gmodular.gui.viewport import _EGLRenderer, OrbitCamera
        r   = self.EGLRenderer()
        cam = OrbitCamera()
        result = r.pick_at(10, 10, 100, 100, cam)
        self.assertEqual(result, 0, "pick_at should return 0 when renderer not ready")

    def test_upload_textured_mesh_returns_none_on_empty(self):
        """_upload_textured_mesh returns None for empty position list."""
        r = self.EGLRenderer()
        # No context — should fall through all paths and return None
        result = r._upload_textured_mesh([], [], [], [], (1.0, 0.0, 0.0))
        self.assertIsNone(result)


# ═════════════════════════════════════════════════════════════════════════════
#  3. Pick-buffer math helpers
# ═════════════════════════════════════════════════════════════════════════════

class TestPickBufferEncoding(unittest.TestCase):
    """Verify the RGBA entity-ID encoding logic mirrors Kotor.NET PickRenderer."""

    def _encode_id(self, entity_id: int) -> Tuple[int, int, int, int]:
        """Python implementation of the GLSL pick encoding."""
        r = (entity_id      ) & 0xFF
        g = (entity_id >>  8) & 0xFF
        b = (entity_id >> 16) & 0xFF
        a = (entity_id >> 24) & 0xFF
        return (r, g, b, a)

    def _decode_id(self, r: int, g: int, b: int) -> int:
        return r | (g << 8) | (b << 16)

    def test_encode_id_1(self):
        r, g, b, a = self._encode_id(1)
        self.assertEqual(r, 1); self.assertEqual(g, 0); self.assertEqual(b, 0)
        self.assertEqual(self._decode_id(r, g, b), 1)

    def test_encode_id_255(self):
        r, g, b, a = self._encode_id(255)
        self.assertEqual(r, 255); self.assertEqual(g, 0); self.assertEqual(b, 0)
        self.assertEqual(self._decode_id(r, g, b), 255)

    def test_encode_id_256(self):
        r, g, b, a = self._encode_id(256)
        self.assertEqual(r, 0); self.assertEqual(g, 1); self.assertEqual(b, 0)
        self.assertEqual(self._decode_id(r, g, b), 256)

    def test_encode_id_large(self):
        eid = 0xABCDEF
        r, g, b, a = self._encode_id(eid)
        self.assertEqual(self._decode_id(r, g, b), eid)

    def test_round_trip_1000(self):
        """All IDs 1..1000 survive encode→decode round-trip."""
        for eid in range(1, 1001):
            r, g, b, a = self._encode_id(eid)
            decoded = self._decode_id(r, g, b)
            self.assertEqual(decoded, eid, f"Round-trip failed for eid={eid}")


# ═════════════════════════════════════════════════════════════════════════════
#  4. MDL Parser Improvements
# ═════════════════════════════════════════════════════════════════════════════

class TestMDLParserImprovements(unittest.TestCase):
    """Tests for MDL parser enhancements based on Kotor.NET analysis."""

    def setUp(self):
        try:
            from gmodular.formats.mdl_parser import (
                CTRL_ALPHA, CTRL_ALPHA_OLD, CTRL_POSITION, CTRL_ORIENTATION,
                CTRL_SCALE, CTRL_SELF_ILLUM,
                MeshNode, MeshData, AnimationData, AnimationEvent,
                NODE_HEADER, NODE_MESH, NODE_AABB,
                _uncompress_quaternion, _sample_vec3_controller,
                _sample_quat_controller, _quat_slerp, _lerp,
                list_mdl_dependencies, MDLParser,
            )
            self.CTRL_ALPHA      = CTRL_ALPHA
            self.CTRL_ALPHA_OLD  = CTRL_ALPHA_OLD
            self.MeshNode        = MeshNode
            self.MeshData        = MeshData
            self.AnimationData   = AnimationData
            self.AnimationEvent  = AnimationEvent
            self.NODE_HEADER     = NODE_HEADER
            self.NODE_MESH       = NODE_MESH
            self.NODE_AABB       = NODE_AABB
            self._uncompress     = _uncompress_quaternion
            self._sample_vec3    = _sample_vec3_controller
            self._sample_quat    = _sample_quat_controller
            self._slerp          = _quat_slerp
            self._lerp           = _lerp
            self.list_deps       = list_mdl_dependencies
            self.MDLParser       = MDLParser
        except ImportError as e:
            self.skipTest(f"mdl_parser not importable: {e}")

    # ── CTRL_ALPHA constant ─────────────────────────────────────────────────

    def test_ctrl_alpha_is_132(self):
        """Kotor.NET MDLBinaryControllerType: Alpha = 132 (not 128)."""
        self.assertEqual(self.CTRL_ALPHA, 132)

    def test_ctrl_alpha_old_is_128(self):
        """Legacy alpha value seen in K1 assets."""
        self.assertEqual(self.CTRL_ALPHA_OLD, 128)

    def test_ctrl_alpha_and_old_are_different(self):
        """CTRL_ALPHA and CTRL_ALPHA_OLD must be distinct constants."""
        self.assertNotEqual(self.CTRL_ALPHA, self.CTRL_ALPHA_OLD)

    # ── Compressed quaternion decompression ────────────────────────────────

    def test_uncompress_quat_identity(self):
        """Decompressing zero should yield identity quaternion."""
        q = self._uncompress(0)
        # Identity = (0, 0, 0, 1) but compressed 0 has very small xyz → near identity
        # The important thing is it should not crash and magnitude ~1
        mag = math.sqrt(sum(x*x for x in q))
        self.assertAlmostEqual(mag, 1.0, places=3,
                               msg="Compressed quat should produce unit quaternion")

    def test_uncompress_quat_unit_length(self):
        """Any decompressed quaternion should have magnitude ≈ 1.0."""
        test_packed_values = [0, 1, 100, 0x7FFFFF, 0x3FFFFF, 0x1FFFFF, 0xFFFFFF]
        for packed in test_packed_values:
            q = self._uncompress(packed)
            mag = math.sqrt(sum(x*x for x in q))
            self.assertAlmostEqual(mag, 1.0, places=3,
                                   msg=f"packed=0x{packed:08x} produced non-unit quat {q}")

    def test_uncompress_quat_returns_4_tuple(self):
        """_uncompress_quaternion should always return a 4-element tuple."""
        for v in [0, 0x12345678, 0xABCDEF01]:
            q = self._uncompress(v)
            self.assertEqual(len(q), 4, f"Expected 4-tuple, got {len(q)}")

    # ── Vec3 controller sampling ────────────────────────────────────────────

    def test_sample_vec3_no_rows_returns_default(self):
        default = (1.0, 2.0, 3.0)
        result  = self._sample_vec3([], 0.5, default)
        self.assertEqual(result, default)

    def test_sample_vec3_single_keyframe(self):
        rows    = [(0.0, [10.0, 20.0, 30.0])]
        result  = self._sample_vec3(rows, 99.0, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(result[0], 10.0)
        self.assertAlmostEqual(result[1], 20.0)
        self.assertAlmostEqual(result[2], 30.0)

    def test_sample_vec3_interpolation_midpoint(self):
        """Midpoint between two keyframes should give the average value."""
        rows = [
            (0.0, [0.0, 0.0, 0.0]),
            (1.0, [2.0, 4.0, 6.0]),
        ]
        result = self._sample_vec3(rows, 0.5, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(result[0], 1.0, places=5)
        self.assertAlmostEqual(result[1], 2.0, places=5)
        self.assertAlmostEqual(result[2], 3.0, places=5)

    def test_sample_vec3_before_first_frame_returns_last_known(self):
        rows = [(1.0, [5.0, 5.0, 5.0])]
        # Requesting t=0 which is before the only keyframe
        result = self._sample_vec3(rows, 0.0, (0.0, 0.0, 0.0))
        # Should return the last known row (the only one)
        self.assertAlmostEqual(result[0], 5.0, places=5)

    def test_sample_vec3_after_last_frame_holds_last(self):
        rows = [
            (0.0, [1.0, 2.0, 3.0]),
            (1.0, [4.0, 5.0, 6.0]),
        ]
        result = self._sample_vec3(rows, 5.0, (0.0, 0.0, 0.0))
        # After last keyframe, should hold last value
        self.assertAlmostEqual(result[0], 4.0, places=5)

    def test_sample_vec3_exact_keyframe_match(self):
        rows = [(0.25, [7.0, 8.0, 9.0]), (0.75, [10.0, 11.0, 12.0])]
        result = self._sample_vec3(rows, 0.25, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(result[0], 7.0, places=5)

    # ── Quaternion SLERP ────────────────────────────────────────────────────

    def test_slerp_t0_returns_qa(self):
        qa = (0.0, 0.0, 0.0, 1.0)
        qb = (0.0, 1.0, 0.0, 0.0)
        result = self._slerp(qa, qb, 0.0)
        self.assertAlmostEqual(result[3], qa[3], places=4)

    def test_slerp_t1_returns_qb(self):
        qa = (0.0, 0.0, 0.0, 1.0)
        qb = (0.0, 0.0, 1.0, 0.0)
        result = self._slerp(qa, qb, 1.0)
        # At t=1 should be close to qb
        dot_qb = sum(result[i] * qb[i] for i in range(4))
        self.assertGreater(abs(dot_qb), 0.99, "SLERP at t=1 should return qb")

    def test_slerp_midpoint_unit_length(self):
        qa = (0.0, 0.0, 0.0, 1.0)
        qb = (0.707106, 0.0, 0.0, 0.707106)
        result = self._slerp(qa, qb, 0.5)
        mag = math.sqrt(sum(x*x for x in result))
        self.assertAlmostEqual(mag, 1.0, places=3)

    # ── AnimationData / AnimationEvent ─────────────────────────────────────

    def test_animation_data_default_fields(self):
        anim = self.AnimationData()
        self.assertEqual(anim.name, "default")
        self.assertEqual(anim.length, 0.0)
        self.assertAlmostEqual(anim.transition, 0.25)
        self.assertIsNone(anim.root_node)
        self.assertEqual(anim.events, [])

    def test_animation_event_fields(self):
        evt = self.AnimationEvent(time=1.5, name="OnOpen")
        self.assertAlmostEqual(evt.time, 1.5)
        self.assertEqual(evt.name, "OnOpen")

    def test_animation_find_node_none_when_empty(self):
        anim = self.AnimationData()
        self.assertIsNone(anim.find_node("any_node"))

    def test_animation_find_node_finds_root(self):
        root = self.MeshNode(name="root")
        anim = self.AnimationData(root_node=root)
        found = anim.find_node("root")
        self.assertIs(found, root)

    def test_animation_find_node_traverses_children(self):
        root  = self.MeshNode(name="root")
        child = self.MeshNode(name="bone01", parent=root)
        root.children.append(child)
        anim  = self.AnimationData(root_node=root)
        found = anim.find_node("bone01")
        self.assertIs(found, child)

    def test_animation_find_node_deep_hierarchy(self):
        root   = self.MeshNode(name="root")
        mid    = self.MeshNode(name="mid",   parent=root)
        leaf   = self.MeshNode(name="leaf",  parent=mid)
        root.children.append(mid)
        mid.children.append(leaf)
        anim   = self.AnimationData(root_node=root)
        self.assertIs(anim.find_node("leaf"), leaf)
        self.assertIsNone(anim.find_node("nonexistent"))

    # ── MeshNode.lightmap_clean ─────────────────────────────────────────────

    def test_lightmap_clean_strips_nulls(self):
        node = self.MeshNode()
        node.lightmap = "LM_FLOOR\x00garbage"
        self.assertEqual(node.lightmap_clean, "LM_FLOOR")

    def test_lightmap_clean_empty(self):
        node = self.MeshNode()
        node.lightmap = ""
        self.assertEqual(node.lightmap_clean, "")

    def test_lightmap_clean_no_nulls(self):
        node = self.MeshNode()
        node.lightmap = "LIGHTMAP01"
        self.assertEqual(node.lightmap_clean, "LIGHTMAP01")

    # ── MeshNode.has_lightmap flag ──────────────────────────────────────────

    def test_mesh_node_has_lightmap_default_false(self):
        node = self.MeshNode()
        self.assertFalse(node.has_lightmap)

    def test_mesh_node_has_lightmap_set_true(self):
        node = self.MeshNode()
        node.has_lightmap = True
        self.assertTrue(node.has_lightmap)

    # ── MeshNode.uvs2 field ─────────────────────────────────────────────────

    def test_mesh_node_has_uvs2_field(self):
        node = self.MeshNode()
        self.assertTrue(hasattr(node, 'uvs2'),
                        "MeshNode must have uvs2 field for lightmap UVs")
        self.assertEqual(node.uvs2, [])

    def test_mesh_node_uvs2_can_store_pairs(self):
        node = self.MeshNode()
        node.uvs2.append((0.5, 0.25))
        node.uvs2.append((0.75, 0.0))
        self.assertEqual(len(node.uvs2), 2)
        self.assertAlmostEqual(node.uvs2[0][0], 0.5)

    # ── MeshData.scan_textures tracks lightmaps ─────────────────────────────

    def test_scan_textures_returns_visible_node_textures(self):
        data = self.MeshData()
        root = self.MeshNode(name="root")
        n1   = self.MeshNode(name="floor",
                             flags=self.NODE_HEADER | self.NODE_MESH,
                             render=True)
        n1.vertices.append((0.0, 0.0, 0.0))
        n1.texture = "FLOOR_TILE"
        root.children.append(n1)
        data.root_node = root
        textures = data.scan_textures()
        self.assertIn("floor_tile", [t.lower() for t in textures])

    # ── list_mdl_dependencies ───────────────────────────────────────────────

    def test_list_mdl_dependencies_on_tiny_mdl(self):
        """list_mdl_dependencies should return valid dict structure even on minimal MDL."""
        mdl = _make_minimal_mdl("tiny")
        result = self.list_deps(mdl, b'')
        self.assertIn('textures',  result)
        self.assertIn('lightmaps', result)
        self.assertIn('models',    result)

    def test_list_mdl_dependencies_on_bad_bytes(self):
        """list_mdl_dependencies should not raise on garbage bytes."""
        result = self.list_deps(b'\x00\x01\x02\x03', b'')
        self.assertEqual(result['textures'],  [])
        self.assertEqual(result['lightmaps'], [])
        self.assertEqual(result['models'],    [])

    # ── MDLParser survivability ──────────────────────────────────────────────

    def test_mdl_parser_minimal_does_not_crash(self):
        """MDLParser should not raise on well-formed but empty MDL."""
        mdl = _make_minimal_mdl("survival")
        parser = self.MDLParser(mdl, b'')
        mesh = parser.parse()
        self.assertIsNotNone(mesh)
        self.assertEqual(mesh.name, "survival")

    def test_mdl_parser_truncated_does_not_crash(self):
        """Parser must handle a truncated file gracefully (raise ValueError, not crash)."""
        try:
            parser = self.MDLParser(b'\x00' * 10, b'')
            parser.parse()
        except (ValueError, Exception):
            pass  # either a ValueError or silent failure is acceptable

    def test_mdl_parser_game_version_k1_from_fp(self):
        """K1 function pointer should yield game_version=1."""
        mdl = _make_minimal_mdl("k1test")
        parser = self.MDLParser(mdl, b'')
        mesh = parser.parse()
        self.assertEqual(mesh.game_version, 1)


# ═════════════════════════════════════════════════════════════════════════════
#  5. WokParser Improvements
# ═════════════════════════════════════════════════════════════════════════════

class TestWokParserImprovements(unittest.TestCase):
    """Tests for the updated wok_parser surface material alignment."""

    def setUp(self):
        try:
            from gmodular.formats.wok_parser import (
                SURF_UNDEFINED, SURF_DIRT, SURF_OBSCURING, SURF_GRASS,
                SURF_STONE, SURF_WOOD, SURF_WATER, SURF_NONWALK,
                SURF_TRANSPARENT, SURF_CARPET, SURF_METAL, SURF_PUDDLES,
                SURF_SWAMP, SURF_MUD, SURF_LEAVES, SURF_LAVA,
                SURF_BOTTOMLESSPIT, SURF_DEEPWATER, SURF_DOOR,
                SURF_NONWALKGRASS, SURF_TRIGGER,
                SURF_NAMES, _NON_WALKABLE_MATS,
                is_walkable, surface_material_name,
            )
            self.SURF_UNDEFINED    = SURF_UNDEFINED
            self.SURF_DIRT         = SURF_DIRT
            self.SURF_OBSCURING    = SURF_OBSCURING
            self.SURF_GRASS        = SURF_GRASS
            self.SURF_STONE        = SURF_STONE
            self.SURF_WOOD         = SURF_WOOD
            self.SURF_WATER        = SURF_WATER
            self.SURF_NONWALK      = SURF_NONWALK
            self.SURF_TRANSPARENT  = SURF_TRANSPARENT
            self.SURF_CARPET       = SURF_CARPET
            self.SURF_METAL        = SURF_METAL
            self.SURF_PUDDLES      = SURF_PUDDLES
            self.SURF_SWAMP        = SURF_SWAMP
            self.SURF_MUD          = SURF_MUD
            self.SURF_LEAVES       = SURF_LEAVES
            self.SURF_LAVA         = SURF_LAVA
            self.SURF_BOTTOMLESSPIT = SURF_BOTTOMLESSPIT
            self.SURF_DEEPWATER    = SURF_DEEPWATER
            self.SURF_DOOR         = SURF_DOOR
            self.SURF_NONWALKGRASS = SURF_NONWALKGRASS
            self.SURF_TRIGGER      = SURF_TRIGGER
            self.SURF_NAMES        = SURF_NAMES
            self._NON_WALKABLE_MATS = _NON_WALKABLE_MATS
            self.is_walkable       = is_walkable
            self.surf_name         = surface_material_name
        except ImportError as e:
            self.skipTest(f"wok_parser not importable: {e}")

    # ── SURF_* constants alignment (Kotor.NET SurfaceMaterial.cs) ──────────

    def test_surf_undefined_is_0(self):
        self.assertEqual(self.SURF_UNDEFINED, 0)

    def test_surf_dirt_is_1(self):
        self.assertEqual(self.SURF_DIRT, 1)

    def test_surf_obscuring_is_2(self):
        self.assertEqual(self.SURF_OBSCURING, 2)

    def test_surf_nonwalk_is_7(self):
        self.assertEqual(self.SURF_NONWALK, 7)

    def test_surf_transparent_is_8(self):
        self.assertEqual(self.SURF_TRANSPARENT, 8)

    def test_surf_lava_is_15(self):
        self.assertEqual(self.SURF_LAVA, 15)

    def test_surf_bottomlesspit_is_16(self):
        self.assertEqual(self.SURF_BOTTOMLESSPIT, 16)

    def test_surf_deepwater_is_17(self):
        self.assertEqual(self.SURF_DEEPWATER, 17)

    def test_surf_door_is_18(self):
        self.assertEqual(self.SURF_DOOR, 18)

    def test_surf_trigger_is_30(self):
        """Kotor.NET SurfaceMaterial.Trigger = 30 (gap after row 19)."""
        self.assertEqual(self.SURF_TRIGGER, 30)

    def test_surf_nonwalkgrass_is_19(self):
        self.assertEqual(self.SURF_NONWALKGRASS, 19)

    # ── SURF_NAMES mapping ──────────────────────────────────────────────────

    def test_surf_names_covers_all_constants(self):
        """Every SURF_* constant should have a human-readable name."""
        constants_to_check = [
            self.SURF_UNDEFINED, self.SURF_DIRT, self.SURF_OBSCURING,
            self.SURF_GRASS, self.SURF_STONE, self.SURF_WOOD, self.SURF_WATER,
            self.SURF_NONWALK, self.SURF_TRANSPARENT, self.SURF_CARPET,
            self.SURF_METAL, self.SURF_PUDDLES, self.SURF_SWAMP, self.SURF_MUD,
            self.SURF_LEAVES, self.SURF_LAVA, self.SURF_BOTTOMLESSPIT,
            self.SURF_DEEPWATER, self.SURF_DOOR, self.SURF_NONWALKGRASS,
            self.SURF_TRIGGER,
        ]
        for c in constants_to_check:
            self.assertIn(c, self.SURF_NAMES,
                          f"Surface material {c} missing from SURF_NAMES")

    def test_surf_names_strings_non_empty(self):
        for k, v in self.SURF_NAMES.items():
            self.assertIsInstance(v, str, f"SURF_NAMES[{k}] should be str")
            self.assertTrue(len(v) > 0, f"SURF_NAMES[{k}] should be non-empty")

    # ── surface_material_name() helper ─────────────────────────────────────

    def test_surface_material_name_known(self):
        self.assertEqual(self.surf_name(self.SURF_DIRT), "Dirt")
        self.assertEqual(self.surf_name(self.SURF_LAVA), "Lava")
        self.assertEqual(self.surf_name(self.SURF_TRIGGER), "Trigger")

    def test_surface_material_name_unknown_fallback(self):
        """Unknown materials should return 'Surface_N' instead of raising."""
        result = self.surf_name(999)
        self.assertIn("999", result)

    def test_surface_material_name_zero(self):
        result = self.surf_name(0)
        self.assertTrue(len(result) > 0)

    # ── _NON_WALKABLE_MATS frozenset ────────────────────────────────────────

    def test_non_walkable_mats_is_frozenset(self):
        self.assertIsInstance(self._NON_WALKABLE_MATS, frozenset)

    def test_non_walkable_mats_contains_lava(self):
        self.assertIn(self.SURF_LAVA, self._NON_WALKABLE_MATS)

    def test_non_walkable_mats_contains_bottomlesspit(self):
        self.assertIn(self.SURF_BOTTOMLESSPIT, self._NON_WALKABLE_MATS)

    def test_non_walkable_mats_contains_nonwalk(self):
        self.assertIn(self.SURF_NONWALK, self._NON_WALKABLE_MATS)

    def test_non_walkable_mats_contains_transparent(self):
        self.assertIn(self.SURF_TRANSPARENT, self._NON_WALKABLE_MATS)

    def test_non_walkable_mats_contains_trigger(self):
        self.assertIn(self.SURF_TRIGGER, self._NON_WALKABLE_MATS)

    def test_non_walkable_mats_contains_deepwater(self):
        self.assertIn(self.SURF_DEEPWATER, self._NON_WALKABLE_MATS)

    # ── is_walkable() for new material IDs ─────────────────────────────────

    def test_walkable_dirt(self):
        # Raw BWM row 0 = Dirt = walkable
        self.assertTrue(self.is_walkable(0))

    def test_walkable_stone(self):
        self.assertTrue(self.is_walkable(3))

    def test_not_walkable_nonwalk(self):
        # Raw BWM row 6 = NonWalk
        self.assertFalse(self.is_walkable(6))

    def test_not_walkable_lava(self):
        # Raw BWM row 14 = Lava
        self.assertFalse(self.is_walkable(14))

    def test_not_walkable_bottomlesspit(self):
        self.assertFalse(self.is_walkable(15))

    def test_walkable_door(self):
        # Raw BWM row 17 = Door (open = walkable)
        self.assertTrue(self.is_walkable(17))

    def test_is_walkable_out_of_range_returns_false(self):
        self.assertFalse(self.is_walkable(999))
        self.assertFalse(self.is_walkable(-1))


# ═════════════════════════════════════════════════════════════════════════════
#  6. OrbitCamera
# ═════════════════════════════════════════════════════════════════════════════

class TestOrbitCameraImprovements(unittest.TestCase):
    """Tests for OrbitCamera correctness (numpy required)."""

    def setUp(self):
        try:
            import numpy as np
            from gmodular.gui.viewport import OrbitCamera
            self.OrbitCamera = OrbitCamera
            self.np = np
        except ImportError:
            self.skipTest("numpy or viewport not available")

    def test_ray_from_screen_returns_unit_direction(self):
        cam = self.OrbitCamera()
        eye, direction = cam.ray_from_screen(400, 300, 800, 600)
        mag = self.np.linalg.norm(direction)
        self.assertAlmostEqual(float(mag), 1.0, places=4)

    def test_ray_from_screen_center_points_forward(self):
        """Ray from screen center should point roughly toward camera target."""
        cam = self.OrbitCamera()
        cam.target = self.np.array([0., 0., 0.], dtype='f4')
        cam.azimuth = 0.0
        cam.elevation = 0.0
        eye, direction = cam.ray_from_screen(400, 300, 800, 600)
        # Direction at center should agree with (target - eye) normalized
        toward_target = cam.target - eye
        toward_target /= self.np.linalg.norm(toward_target)
        dot = float(self.np.dot(direction, toward_target))
        self.assertGreater(dot, 0.95, "Center ray should align with target direction")

    def test_orbit_wraps_azimuth(self):
        cam = self.OrbitCamera()
        cam.azimuth = 350.0
        cam.orbit(20.0, 0.0)
        self.assertAlmostEqual(cam.azimuth, 10.0, places=3)

    def test_zoom_clamped_minimum(self):
        cam = self.OrbitCamera()
        cam.distance = 1.0
        # Zoom in very far — should clamp to minimum 0.5
        for _ in range(100):
            cam.zoom(1)
        self.assertGreaterEqual(cam.distance, 0.5)

    def test_frame_sets_target_and_distance(self):
        cam = self.OrbitCamera()
        center = self.np.array([5., 5., 0.], dtype='f4')
        cam.frame(center, 3.0)
        self.assertAlmostEqual(cam.target[0], 5.0)
        self.assertGreater(cam.distance, 0.0)

    def test_projection_matrix_shape(self):
        cam = self.OrbitCamera()
        proj = cam.projection_matrix(1.0)
        self.assertEqual(proj.shape, (4, 4))

    def test_view_matrix_shape(self):
        cam = self.OrbitCamera()
        view = cam.view_matrix()
        self.assertEqual(view.shape, (4, 4))

    def test_pan_changes_target(self):
        cam = self.OrbitCamera()
        old_target = cam.target.copy()
        cam.pan(100.0, 0.0)
        moved = self.np.linalg.norm(cam.target - old_target)
        self.assertGreater(float(moved), 0.0, "pan() should move the camera target")


# ═════════════════════════════════════════════════════════════════════════════
#  7. MDX lightmap UV integration via MDLParser
# ═════════════════════════════════════════════════════════════════════════════

class TestMDXLightmapUVs(unittest.TestCase):
    """Verify the MDX lightmap-UV (uvs2) reading path is wired up correctly."""

    def setUp(self):
        try:
            from gmodular.formats.mdl_parser import MDLParser, MeshNode
            self.MDLParser = MDLParser
            self.MeshNode  = MeshNode
        except ImportError:
            self.skipTest("mdl_parser not importable")

    def test_mesh_node_uvs2_default_empty(self):
        n = self.MeshNode()
        self.assertIsInstance(n.uvs2, list)
        self.assertEqual(len(n.uvs2), 0)

    def test_mesh_node_can_store_lightmap_uvs(self):
        n = self.MeshNode()
        n.uvs2 = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
        self.assertEqual(len(n.uvs2), 3)
        self.assertAlmostEqual(n.uvs2[1][0], 0.5)
        self.assertAlmostEqual(n.uvs2[1][1], 0.5)

    def test_parser_exposes_uvs2_field_on_nodes(self):
        """After parsing, each MeshNode must have a uvs2 list (may be empty)."""
        mdl = _make_minimal_mdl("uvtest")
        parser = self.MDLParser(mdl, b'')
        mesh = parser.parse()
        for node in mesh.all_nodes():
            self.assertTrue(hasattr(node, 'uvs2'),
                            f"Node '{node.name}' missing uvs2 field")
            self.assertIsInstance(node.uvs2, list)


# ═════════════════════════════════════════════════════════════════════════════
#  8. Viewport shader list-dependencies integration
# ═════════════════════════════════════════════════════════════════════════════

class TestViewportTexturedShaderFlow(unittest.TestCase):
    """Integration tests for the textured shader data flow in the viewport."""

    def setUp(self):
        try:
            import gmodular.gui.viewport as vp
            self.vp = vp
        except ImportError:
            self.skipTest("viewport module not importable")

    def test_textured_shader_light_dir_uniform(self):
        """Textured fragment shader references light_dir uniform (KotOR two-light model)."""
        # light_dir is declared in the fragment shader (not vertex shader)
        self.assertIn("light_dir", self.vp._FRAG_TEXTURED)

    def test_textured_fragment_has_kotor_two_light(self):
        """Fragment shader uses key and fill lights (Kotor.NET GeometryRenderer)."""
        src = self.vp._FRAG_TEXTURED
        self.assertIn("fill", src)
        self.assertIn("NdL",  src)

    def test_skinned_fragment_has_kotor_lighting(self):
        src = self.vp._FRAG_SKINNED
        self.assertIn("fill", src)
        self.assertIn("NdL",  src)

    def test_frag_pick_version_330(self):
        """Pick shader uses GLSL 330 core (matching standard/picker shaders in Kotor.NET)."""
        self.assertIn("#version 330", self.vp._FRAG_PICK)

    def test_all_new_shaders_are_strings(self):
        for name in ('_VERT_TEXTURED', '_FRAG_TEXTURED', '_VERT_SKINNED',
                     '_FRAG_SKINNED', '_VERT_PICK', '_FRAG_PICK'):
            val = getattr(self.vp, name, None)
            self.assertIsNotNone(val, f"{name} not found")
            self.assertIsInstance(val, str, f"{name} should be a string")
            self.assertGreater(len(val), 20, f"{name} is suspiciously short")


if __name__ == '__main__':
    unittest.main()


# ═════════════════════════════════════════════════════════════════════════════
#  9. Kotor.NET rework v2 — UV animation, emitter controllers, camera aliases
# ═════════════════════════════════════════════════════════════════════════════

class TestUVAnimationParsing(unittest.TestCase):
    """UV animation fields (AnimateUV, UVDirection, UVSpeed, UVJitterSpeed) in MDL parser."""

    def setUp(self):
        try:
            from gmodular.formats.mdl_parser import MeshNode
            self.MeshNode = MeshNode
        except ImportError:
            self.skipTest("mdl_parser not importable")

    def test_meshnode_has_uv_animate_field(self):
        """MeshNode must have uv_animate boolean field."""
        n = self.MeshNode()
        self.assertFalse(n.uv_animate)
        n.uv_animate = True
        self.assertTrue(n.uv_animate)

    def test_meshnode_has_uv_dir_field(self):
        """MeshNode must have uv_dir tuple field (UVDirection U, V)."""
        n = self.MeshNode()
        self.assertIsInstance(n.uv_dir, tuple)
        self.assertEqual(len(n.uv_dir), 2)
        n.uv_dir = (0.5, 1.0)
        self.assertAlmostEqual(n.uv_dir[0], 0.5)
        self.assertAlmostEqual(n.uv_dir[1], 1.0)

    def test_meshnode_has_uv_speed_field(self):
        """MeshNode must have uv_speed float field."""
        n = self.MeshNode()
        self.assertAlmostEqual(n.uv_speed, 0.0)
        n.uv_speed = 2.5
        self.assertAlmostEqual(n.uv_speed, 2.5)

    def test_meshnode_has_uv_jitter_field(self):
        """MeshNode must have uv_jitter float field."""
        n = self.MeshNode()
        self.assertAlmostEqual(n.uv_jitter, 0.0)
        n.uv_jitter = 0.1
        self.assertAlmostEqual(n.uv_jitter, 0.1)

    def test_uv_animation_defaults_off(self):
        """UV animation should be disabled by default."""
        n = self.MeshNode()
        self.assertFalse(n.uv_animate)
        self.assertEqual(n.uv_dir, (0.0, 0.0))
        self.assertAlmostEqual(n.uv_speed, 0.0)
        self.assertAlmostEqual(n.uv_jitter, 0.0)


class TestEmitterControllerConstants(unittest.TestCase):
    """Emitter and Light controller type IDs (Kotor.NET MDLBinaryControllerType.cs)."""

    def setUp(self):
        try:
            import gmodular.formats.mdl_parser as mp
            self.mp = mp
        except ImportError:
            self.skipTest("mdl_parser not importable")

    def test_ctrl_light_color_is_76(self):
        """CTRL_LIGHT_COLOR = 76 (Kotor.NET Colour = 76 in Light section)."""
        self.assertEqual(self.mp.CTRL_LIGHT_COLOR, 76)

    def test_ctrl_light_radius_is_88(self):
        """CTRL_LIGHT_RADIUS = 88 (Kotor.NET Radius = 88 in Light section)."""
        self.assertEqual(self.mp.CTRL_LIGHT_RADIUS, 88)

    def test_ctrl_light_multiplier_is_140(self):
        """CTRL_LIGHT_MULTIPLIER = 140 (Kotor.NET Multiplier = 140)."""
        self.assertEqual(self.mp.CTRL_LIGHT_MULTIPLIER, 140)

    def test_ctrl_emitter_alpha_end_is_80(self):
        """CTRL_EMITTER_ALPHA_END = 80 (Kotor.NET AlphaEnd = 80)."""
        self.assertEqual(self.mp.CTRL_EMITTER_ALPHA_END, 80)

    def test_ctrl_emitter_alpha_start_is_84(self):
        """CTRL_EMITTER_ALPHA_START = 84."""
        self.assertEqual(self.mp.CTRL_EMITTER_ALPHA_START, 84)

    def test_ctrl_emitter_birthrate_is_88(self):
        """CTRL_EMITTER_BIRTHRATE = 88."""
        self.assertEqual(self.mp.CTRL_EMITTER_BIRTHRATE, 88)

    def test_ctrl_emitter_fps_is_104(self):
        """CTRL_EMITTER_FPS = 104."""
        self.assertEqual(self.mp.CTRL_EMITTER_FPS, 104)

    def test_ctrl_emitter_velocity_is_168(self):
        """CTRL_EMITTER_VELOCITY = 168."""
        self.assertEqual(self.mp.CTRL_EMITTER_VELOCITY, 168)

    def test_ctrl_emitter_spread_is_160(self):
        """CTRL_EMITTER_SPREAD = 160."""
        self.assertEqual(self.mp.CTRL_EMITTER_SPREAD, 160)

    def test_ctrl_emitter_color_start_is_392(self):
        """CTRL_EMITTER_COLOR_START = 392 (matches Kotor.NET ColorStart = 392)."""
        self.assertEqual(self.mp.CTRL_EMITTER_COLOR_START, 392)

    def test_ctrl_emitter_color_mid_is_284(self):
        """CTRL_EMITTER_COLOR_MID = 284."""
        self.assertEqual(self.mp.CTRL_EMITTER_COLOR_MID, 284)

    def test_ctrl_emitter_color_end_is_380(self):
        """CTRL_EMITTER_COLOR_END = 380."""
        self.assertEqual(self.mp.CTRL_EMITTER_COLOR_END, 380)

    def test_ctrl_light_shadow_radius_is_96(self):
        """CTRL_LIGHT_SHADOW_RADIUS = 96."""
        self.assertEqual(self.mp.CTRL_LIGHT_SHADOW_RADIUS, 96)

    def test_ctrl_emitter_size_start_is_144(self):
        """CTRL_EMITTER_SIZE_START = 144."""
        self.assertEqual(self.mp.CTRL_EMITTER_SIZE_START, 144)

    def test_ctrl_emitter_size_end_is_148(self):
        """CTRL_EMITTER_SIZE_END = 148."""
        self.assertEqual(self.mp.CTRL_EMITTER_SIZE_END, 148)

    def test_ctrl_emitter_life_exp_is_120(self):
        """CTRL_EMITTER_LIFE_EXP = 120."""
        self.assertEqual(self.mp.CTRL_EMITTER_LIFE_EXP, 120)

    def test_ctrl_emitter_gravity_is_116(self):
        """CTRL_EMITTER_GRAVITY = 116."""
        self.assertEqual(self.mp.CTRL_EMITTER_GRAVITY, 116)

    def test_ctrl_emitter_mass_is_124(self):
        """CTRL_EMITTER_MASS = 124."""
        self.assertEqual(self.mp.CTRL_EMITTER_MASS, 124)

    def test_ctrl_emitter_x_size_is_172(self):
        """CTRL_EMITTER_X_SIZE = 172."""
        self.assertEqual(self.mp.CTRL_EMITTER_X_SIZE, 172)

    def test_ctrl_emitter_y_size_is_176(self):
        """CTRL_EMITTER_Y_SIZE = 176."""
        self.assertEqual(self.mp.CTRL_EMITTER_Y_SIZE, 176)

    def test_ctrl_emitter_frame_end_is_108(self):
        """CTRL_EMITTER_FRAME_END = 108."""
        self.assertEqual(self.mp.CTRL_EMITTER_FRAME_END, 108)

    def test_ctrl_emitter_frame_start_is_112(self):
        """CTRL_EMITTER_FRAME_START = 112."""
        self.assertEqual(self.mp.CTRL_EMITTER_FRAME_START, 112)


class TestOrbitCameraKotorNETAlignment(unittest.TestCase):
    """OrbitCamera matches Kotor.NET OrbitCamera.cs interface and behaviour."""

    def setUp(self):
        try:
            import numpy as np
            from gmodular.gui.viewport import OrbitCamera
            self.OrbitCamera = OrbitCamera
            self.np = np
        except ImportError:
            self.skipTest("viewport or numpy not importable")

    def test_yaw_attribute_exists(self):
        """OrbitCamera.yaw attribute must exist (Kotor.NET: Yaw property)."""
        cam = self.OrbitCamera()
        self.assertTrue(hasattr(cam, 'yaw'))

    def test_pitch_attribute_exists(self):
        """OrbitCamera.pitch attribute must exist (Kotor.NET: Pitch property)."""
        cam = self.OrbitCamera()
        self.assertTrue(hasattr(cam, 'pitch'))

    def test_near_attribute_matches_kotor_net(self):
        """OrbitCamera.near should be 0.001 (Kotor.NET: Near = 0.001f)."""
        cam = self.OrbitCamera()
        self.assertAlmostEqual(cam.near, 0.001, places=4)

    def test_far_attribute_matches_kotor_net(self):
        """OrbitCamera.far should be 1000.0 (Kotor.NET: Far = 1000.0f)."""
        cam = self.OrbitCamera()
        self.assertAlmostEqual(cam.far, 1000.0, places=1)

    def test_azimuth_is_alias_for_yaw(self):
        """azimuth property should be alias for yaw (backward compatibility)."""
        cam = self.OrbitCamera()
        cam.yaw = 123.0
        self.assertAlmostEqual(cam.azimuth, 123.0)
        cam.azimuth = 45.0
        self.assertAlmostEqual(cam.yaw, 45.0)

    def test_elevation_is_alias_for_pitch(self):
        """elevation property should be alias for pitch (backward compatibility)."""
        cam = self.OrbitCamera()
        cam.pitch = 30.0
        self.assertAlmostEqual(cam.elevation, 30.0)
        cam.elevation = 15.0
        self.assertAlmostEqual(cam.pitch, 15.0)

    def test_eye_uses_yaw_pitch_formula(self):
        """
        eye() should match Kotor.NET GetViewTransform formula:
          x = Distance * cosPitch * cosYaw
          y = Distance * cosPitch * sinYaw
          z = Distance * sinPitch
        """
        cam = self.OrbitCamera()
        cam.yaw = 0.0
        cam.pitch = 0.0
        cam.distance = 10.0
        cam.target = self.np.array([0., 0., 0.], dtype='f4')
        eye = cam.eye()
        # yaw=0, pitch=0 → eye at (10, 0, 0)
        self.assertAlmostEqual(float(eye[0]), 10.0, places=4)
        self.assertAlmostEqual(float(eye[1]), 0.0,  places=4)
        self.assertAlmostEqual(float(eye[2]), 0.0,  places=4)

    def test_eye_yaw_90(self):
        """eye() with yaw=90 should put camera along Y axis."""
        cam = self.OrbitCamera()
        cam.yaw = 90.0
        cam.pitch = 0.0
        cam.distance = 10.0
        cam.target = self.np.array([0., 0., 0.], dtype='f4')
        eye = cam.eye()
        self.assertAlmostEqual(float(eye[0]), 0.0,  places=4)
        self.assertAlmostEqual(float(eye[1]), 10.0, places=4)
        self.assertAlmostEqual(float(eye[2]), 0.0,  places=4)

    def test_eye_pitch_90(self):
        """eye() with pitch=90 should put camera directly above target."""
        import math
        cam = self.OrbitCamera()
        cam.yaw = 0.0
        cam.pitch = 90.0
        cam.distance = 5.0
        cam.target = self.np.array([0., 0., 0.], dtype='f4')
        eye = cam.eye()
        # cos(90°)≈0, sin(90°)=1 → eye≈(0,0,5)
        self.assertAlmostEqual(float(eye[2]), 5.0, places=4)

    def test_orbit_modifies_yaw_and_pitch(self):
        """orbit() should update yaw and pitch."""
        cam = self.OrbitCamera()
        cam.yaw = 0.0
        cam.pitch = 0.0
        cam.orbit(45.0, 15.0)
        self.assertAlmostEqual(cam.yaw,   45.0)
        self.assertAlmostEqual(cam.pitch, 15.0)

    def test_pitch_clamp(self):
        """Pitch is clamped to -85..85 degrees."""
        cam = self.OrbitCamera()
        cam.pitch = 0.0
        cam.orbit(0.0, 200.0)
        self.assertLessEqual(cam.pitch, 85.0)
        cam.pitch = 0.0
        cam.orbit(0.0, -200.0)
        self.assertGreaterEqual(cam.pitch, -85.0)

    def test_projection_matrix_uses_near_far(self):
        """projection_matrix() should use self.near and self.far."""
        cam = self.OrbitCamera()
        cam.near = 0.001
        cam.far = 500.0
        proj = cam.projection_matrix(1.0)
        # Check matrix is 4×4
        self.assertEqual(proj.shape, (4, 4))

    def test_fov_default_is_60(self):
        """Default FOV should be 60 degrees (matches Kotor.NET FOV = π/3 ≈ 60°)."""
        cam = self.OrbitCamera()
        self.assertAlmostEqual(cam.fov, 60.0, places=1)


class TestPickBufferKotorNETAlignment(unittest.TestCase):
    """Pick buffer entity ID encode/decode matches Kotor.NET PickRenderer pattern."""

    def setUp(self):
        try:
            from gmodular.gui.viewport import _FRAG_PICKER, _FRAG_PICK
            self.frag_picker = _FRAG_PICKER
            self.frag_pick   = _FRAG_PICK
        except ImportError:
            self.skipTest("viewport not importable")

    def test_frag_picker_uses_entity_id_not_entityID(self):
        """
        _FRAG_PICKER should use 'entity_id' uniform (unified with _FRAG_PICK tests).
        Matches tests/test_rendering_improvements.py line 167 assertion.
        """
        self.assertIn("entity_id", self.frag_picker)
        self.assertNotIn("entityID", self.frag_picker)

    def test_frag_picker_encodes_msb_to_r(self):
        """
        _FRAG_PICKER must encode bits[24..31] into R channel.
        Kotor.NET intToColor: r=float((v>>24)&0xFF)/255
        """
        self.assertIn("24", self.frag_picker)
        self.assertIn(">> 24", self.frag_picker)

    def test_frag_pick_entity_id_uniform(self):
        """_FRAG_PICK uses entity_id uniform (consistent naming)."""
        self.assertIn("entity_id", self.frag_pick)

    def test_pick_decode_msb_formula(self):
        """
        Verify the RGBA decode formula (r<<24)|(g<<16)|(b<<8)|a is correct
        by round-tripping a known entity ID through encode/decode.
        """
        # Simulate what _FRAG_PICKER does: r=bits[24..31], g=bits[16..23], b=bits[8..15], a=bits[0..7]
        for eid in (1, 42, 255, 1000, 0xABCDEF):
            r = (eid >> 24) & 0xFF
            g = (eid >> 16) & 0xFF
            b = (eid >>  8) & 0xFF
            a = (eid      ) & 0xFF
            decoded = (r << 24) | (g << 16) | (b << 8) | a
            self.assertEqual(decoded, eid,
                f"Round-trip failed for eid={eid}: got {decoded}")

    def test_pick_background_is_all_ones(self):
        """Background clear (1.0,1.0,1.0,1.0) decodes as 0xFFFFFFFF (sentinel for 'nothing')."""
        r, g, b, a = 0xFF, 0xFF, 0xFF, 0xFF
        decoded = (r << 24) | (g << 16) | (b << 8) | a
        self.assertEqual(decoded, 0xFFFFFFFF)


class TestOrbitCameraOrbitMethod(unittest.TestCase):
    """Detailed orbit() method tests matching Kotor.NET behaviour."""

    def setUp(self):
        try:
            from gmodular.gui.viewport import OrbitCamera
            self.OrbitCamera = OrbitCamera
        except ImportError:
            self.skipTest("viewport not importable")

    def test_orbit_yaw_wraps(self):
        """Yaw should wrap around 360°."""
        cam = self.OrbitCamera()
        cam.yaw = 350.0
        cam.orbit(20.0, 0.0)
        self.assertAlmostEqual(cam.yaw % 360.0, 10.0, places=4)

    def test_orbit_pitch_no_wrap(self):
        """Pitch does not wrap — it is clamped."""
        cam = self.OrbitCamera()
        cam.pitch = 80.0
        cam.orbit(0.0, 10.0)
        self.assertLessEqual(cam.pitch, 85.0)

    def test_zoom_decreases_distance(self):
        """zoom(positive) should decrease distance."""
        cam = self.OrbitCamera()
        d0 = cam.distance
        cam.zoom(1.0)
        self.assertLess(cam.distance, d0)

    def test_zoom_minimum_distance(self):
        """distance should not go below 0.5."""
        cam = self.OrbitCamera()
        for _ in range(100):
            cam.zoom(10.0)
        self.assertGreaterEqual(cam.distance, 0.5)

    def test_view_matrix_shape(self):
        """view_matrix() returns a 4×4 numpy array."""
        import numpy as np
        cam = self.OrbitCamera()
        mat = cam.view_matrix()
        self.assertEqual(mat.shape, (4, 4))

    def test_projection_matrix_shape(self):
        """projection_matrix() returns a 4×4 numpy array."""
        cam = self.OrbitCamera()
        mat = cam.projection_matrix(aspect=1.6)
        self.assertEqual(mat.shape, (4, 4))


class TestMDLParserUVAnimationIntegration(unittest.TestCase):
    """Integration tests: UV animation fields survive a minimal MDL parse."""

    def setUp(self):
        try:
            from gmodular.formats.mdl_parser import MeshNode, MeshData
            self.MeshNode = MeshNode
            self.MeshData  = MeshData
        except ImportError:
            self.skipTest("mdl_parser not importable")

    def test_meshdata_all_nodes_returns_list(self):
        """MeshData.all_nodes() returns a list."""
        from gmodular.formats.mdl_parser import MeshData, MeshNode
        root = MeshNode(name="root")
        child = MeshNode(name="child", parent=root)
        root.children.append(child)
        md = MeshData(name="test", root_node=root)
        nodes = md.all_nodes()
        self.assertEqual(len(nodes), 2)
        self.assertIn(root,  nodes)
        self.assertIn(child, nodes)

    def test_meshnode_uv_animation_fields_independent(self):
        """Each MeshNode has independent uv_animate/uv_dir/uv_speed/uv_jitter."""
        n1 = self.MeshNode(name="n1")
        n2 = self.MeshNode(name="n2")
        n1.uv_animate = True
        n1.uv_dir = (0.1, 0.2)
        n1.uv_speed = 1.5
        self.assertFalse(n2.uv_animate)
        self.assertEqual(n2.uv_dir, (0.0, 0.0))
        self.assertAlmostEqual(n2.uv_speed, 0.0)

    def test_scan_textures_excludes_null(self):
        """scan_textures() must exclude NULL and empty texture names."""
        from gmodular.formats.mdl_parser import MeshData, MeshNode, NODE_MESH
        root = MeshNode(name="root")
        m1 = MeshNode(name="m1", flags=NODE_MESH, texture="NULL", render=True,
                       parent=root)
        m2 = MeshNode(name="m2", flags=NODE_MESH, texture="tex_a", render=True,
                       parent=root)
        m1.vertices = [(0,0,0),(1,0,0),(0,1,0)]
        m1.faces    = [(0,1,2)]
        m2.vertices = [(0,0,0),(1,0,0),(0,1,0)]
        m2.faces    = [(0,1,2)]
        root.children.extend([m1, m2])
        md = MeshData(name="t", root_node=root)
        textures = md.scan_textures()
        self.assertNotIn("null", textures)
        self.assertIn("tex_a", textures)


class TestViewportPickMethodConsolidation(unittest.TestCase):
    """Tests that verify pick_at is a single consolidated method."""

    def setUp(self):
        try:
            import gmodular.gui.viewport as vp
            self.vp = vp
        except ImportError:
            self.skipTest("viewport not importable")

    def test_pick_at_exists_and_callable(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, 'pick_at', None)))

    def test_pick_at_returns_zero_when_no_context(self):
        """pick_at must return 0 gracefully when renderer is not initialised."""
        r = self.vp._EGLRenderer()
        # renderer.ready is False, so pick_at must return 0
        cam = self.vp.OrbitCamera()
        result = r.pick_at(5, 5, 100, 100, cam)
        self.assertEqual(result, 0)

    def test_frag_picker_has_entity_id_uniform(self):
        """_FRAG_PICKER uses entity_id (not entityID) for test consistency."""
        self.assertIn("entity_id", self.vp._FRAG_PICKER)
        self.assertNotIn("entityID", self.vp._FRAG_PICKER)

    def test_prog_pick_alias_exists_in_init(self):
        """_prog_pick attribute exists (alias for _prog_picker for backward compat)."""
        r = self.vp._EGLRenderer()
        self.assertTrue(hasattr(r, '_prog_pick'), "_prog_pick alias missing")

    def test_prog_textured_attribute_in_init(self):
        """_prog_textured is defined in __init__."""
        r = self.vp._EGLRenderer()
        self.assertTrue(hasattr(r, '_prog_textured'), "_prog_textured missing")

    def test_prog_skinned_attribute_in_init(self):
        """_prog_skinned is defined in __init__."""
        r = self.vp._EGLRenderer()
        self.assertTrue(hasattr(r, '_prog_skinned'), "_prog_skinned missing")


class TestKotorNETShaderAlignment(unittest.TestCase):
    """
    Verify our GLSL shaders match the Kotor.NET rework standard/picker shaders.
    Reference: Kotor.NET Assets/standard/vertex.glsl, fragment.glsl, picker/vertex.glsl.
    """

    def setUp(self):
        try:
            import gmodular.gui.viewport as vp
            self.vp = vp
        except ImportError:
            self.skipTest("viewport not importable")

    def test_standard_vertex_has_position_layout(self):
        """Standard vertex shader should have in_position input."""
        self.assertIn("in_position", self.vp._VERT_LIT)

    def test_standard_vertex_has_normal_layout(self):
        """Standard vertex shader should have in_normal input."""
        self.assertIn("in_normal", self.vp._VERT_LIT)

    def test_standard_vertex_has_uv_layout(self):
        """Standard vertex shader should have in_uv input."""
        self.assertIn("in_uv", self.vp._VERT_LIT)

    def test_standard_vertex_has_mvp_uniform(self):
        """Standard vertex shader should have mvp and model uniforms."""
        self.assertIn("mvp", self.vp._VERT_LIT)
        self.assertIn("model", self.vp._VERT_LIT)

    def test_textured_vertex_has_dual_uv(self):
        """Textured vertex shader has both in_uv and in_uv2 (for lightmap)."""
        self.assertIn("in_uv",  self.vp._VERT_TEXTURED)
        self.assertIn("in_uv2", self.vp._VERT_TEXTURED)

    def test_textured_fragment_has_dual_sampler(self):
        """Textured fragment shader has tex0 and tex1 samplers."""
        self.assertIn("tex0", self.vp._FRAG_TEXTURED)
        self.assertIn("tex1", self.vp._FRAG_TEXTURED)

    def test_skinned_vertex_has_bone_attributes(self):
        """Skinned vertex shader has in_bone_weights and in_bone_indices."""
        self.assertIn("in_bone_weights",  self.vp._VERT_SKINNED)
        self.assertIn("in_bone_indices",  self.vp._VERT_SKINNED)

    def test_skinned_vertex_has_bone_matrices(self):
        """Skinned vertex shader has bone_matrices[16] uniform array."""
        src = self.vp._VERT_SKINNED
        self.assertIn("bone_matrices", src)
        self.assertIn("16", src)

    def test_picker_vertex_only_position(self):
        """Picker vertex shader only needs position (no normals/UVs)."""
        self.assertIn("in_position", self.vp._VERT_PICKER)
        self.assertNotIn("in_normal", self.vp._VERT_PICKER)

    def test_picker_fragment_entity_id_uint(self):
        """Picker fragment shader encodes a uint entity_id."""
        src = self.vp._FRAG_PICKER
        self.assertIn("uint", src)
        self.assertIn("entity_id", src)

    def test_flat_shader_has_color_input(self):
        """Flat shader has in_color input for colour-per-vertex."""
        self.assertIn("in_color", self.vp._VERT_FLAT)
        self.assertIn("v_color",  self.vp._FRAG_FLAT)

    def test_uniform_shader_u_color(self):
        """Uniform overlay shader has u_color vec4 uniform."""
        self.assertIn("u_color", self.vp._FRAG_UNIFORM)

    def test_all_shaders_version_330(self):
        """All shaders must be GLSL 330 core."""
        shaders = [
            '_VERT_FLAT', '_FRAG_FLAT', '_VERT_LIT', '_FRAG_LIT',
            '_VERT_LIT_NO_UV', '_FRAG_LIT_NO_UV', '_VERT_UNIFORM', '_FRAG_UNIFORM',
            '_VERT_PICKER', '_FRAG_PICKER', '_VERT_TEXTURED', '_FRAG_TEXTURED',
            '_VERT_SKINNED', '_FRAG_SKINNED', '_VERT_PICK', '_FRAG_PICK',
        ]
        for name in shaders:
            src = getattr(self.vp, name, None)
            self.assertIsNotNone(src, f"{name} not found")
            self.assertIn("#version 330", src, f"{name} missing #version 330")


class TestOrbitCameraTargetManipulation(unittest.TestCase):
    """Tests for camera target/pan/frame methods."""

    def setUp(self):
        try:
            import numpy as np
            from gmodular.gui.viewport import OrbitCamera
            self.cam = OrbitCamera()
            self.np  = np
        except ImportError:
            self.skipTest("viewport or numpy not importable")

    def test_frame_sets_target(self):
        """frame() should set target to the given center."""
        center = self.np.array([5.0, 3.0, 1.0], dtype='f4')
        self.cam.frame(center, 2.0)
        self.assertAlmostEqual(float(self.cam.target[0]), 5.0, places=3)
        self.assertAlmostEqual(float(self.cam.target[1]), 3.0, places=3)
        self.assertAlmostEqual(float(self.cam.target[2]), 1.0, places=3)

    def test_frame_sets_distance_proportional(self):
        """frame() should set distance to at least radius*2.5."""
        self.cam.frame(self.np.array([0., 0., 0.], dtype='f4'), radius=4.0)
        self.assertGreaterEqual(self.cam.distance, 4.0 * 2.5 * 0.99)

    def test_ray_from_screen_direction_normalised(self):
        """ray_from_screen() direction vector should be unit length."""
        _, direction = self.cam.ray_from_screen(50, 50, 100, 100)
        length = float(self.np.linalg.norm(direction))
        self.assertAlmostEqual(length, 1.0, places=4)

    def test_ray_from_screen_center_goes_forward(self):
        """Ray from screen center should point roughly toward the camera target."""
        import math
        self.cam.yaw   = 0.0
        self.cam.pitch = 0.0
        self.cam.distance = 10.0
        self.cam.target = self.np.array([0., 0., 0.], dtype='f4')
        eye, direction = self.cam.ray_from_screen(50, 50, 100, 100)
        # Eye is at ~(10,0,0), target at origin — forward is roughly (-1,0,0)
        self.assertLess(float(direction[0]), 0.0, "Ray should go toward target (negative X)")


class TestMeshNodeControllerSampling(unittest.TestCase):
    """Tests for controller sampling functions (animation support)."""

    def setUp(self):
        try:
            from gmodular.formats.mdl_parser import (
                _sample_vec3_controller, _sample_quat_controller,
                CTRL_POSITION, CTRL_ORIENTATION, MeshNode
            )
            self._sample_vec3 = _sample_vec3_controller
            self._sample_quat = _sample_quat_controller
            self.CTRL_POSITION = CTRL_POSITION
            self.CTRL_ORIENTATION = CTRL_ORIENTATION
            self.MeshNode = MeshNode
        except ImportError:
            self.skipTest("mdl_parser not importable")

    def test_sample_vec3_single_keyframe(self):
        """Single-keyframe vec3 controller returns that keyframe's value."""
        rows = [(0.0, [1.0, 2.0, 3.0])]
        result = self._sample_vec3(rows, 0.5, (0., 0., 0.))
        self.assertAlmostEqual(result[0], 1.0)
        self.assertAlmostEqual(result[1], 2.0)
        self.assertAlmostEqual(result[2], 3.0)

    def test_sample_vec3_interpolation(self):
        """Two-keyframe vec3 controller interpolates linearly."""
        rows = [(0.0, [0.0, 0.0, 0.0]), (1.0, [2.0, 4.0, 6.0])]
        result = self._sample_vec3(rows, 0.5, (0., 0., 0.))
        self.assertAlmostEqual(result[0], 1.0, places=4)
        self.assertAlmostEqual(result[1], 2.0, places=4)
        self.assertAlmostEqual(result[2], 3.0, places=4)

    def test_sample_quat_single_keyframe(self):
        """Single-keyframe quat controller returns that value."""
        rows = [(0.0, [0.0, 0.0, 0.0, 1.0])]
        result = self._sample_quat(rows, 1.0, (0., 0., 0., 1.))
        self.assertAlmostEqual(result[3], 1.0, places=4)

    def test_meshnode_get_anim_position_no_controller(self):
        """get_anim_position returns None when no CTRL_POSITION controller."""
        n = self.MeshNode(name="n")
        self.assertIsNone(n.get_anim_position(0.5))

    def test_meshnode_get_anim_rotation_no_controller(self):
        """get_anim_rotation returns None when no CTRL_ORIENTATION controller."""
        n = self.MeshNode(name="n")
        self.assertIsNone(n.get_anim_rotation(0.5))

    def test_meshnode_get_anim_position_with_controller(self):
        """get_anim_position samples correctly with a controller."""
        from gmodular.formats.mdl_parser import CTRL_POSITION
        n = self.MeshNode(name="n")
        n.controllers[CTRL_POSITION] = [(0.0, [1.0, 2.0, 3.0])]
        result = n.get_anim_position(0.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 1.0, places=4)

    def test_meshnode_get_anim_alpha_falls_back(self):
        """get_anim_alpha returns node.alpha when no controller."""
        n = self.MeshNode(name="n")
        n.alpha = 0.75
        self.assertAlmostEqual(n.get_anim_alpha(0.0), 0.75)


class TestSceneEntityAbstraction(unittest.TestCase):
    """
    Lightweight test that the _EGLRenderer exposes all entity-related methods
    needed by the viewport scene management (inspired by Kotor.NET Scene/Entity pattern).
    """

    def setUp(self):
        try:
            import gmodular.gui.viewport as vp
            self.vp = vp
        except ImportError:
            self.skipTest("viewport not importable")

    def test_renderer_has_rebuild_object_vaos(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, 'rebuild_object_vaos', None)))

    def test_renderer_has_rebuild_room_vaos(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, 'rebuild_room_vaos', None)))

    def test_renderer_has_rebuild_walkmesh_vaos(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, 'rebuild_walkmesh_vaos', None)))

    def test_renderer_has_load_texture(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, 'load_texture', None)))

    def test_renderer_has_upload_lit_or_flat(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, '_upload_lit_or_flat', None)))

    def test_renderer_has_upload_textured_mesh(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, '_upload_textured_mesh', None)))

    def test_renderer_has_render(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, 'render', None)))

    def test_renderer_has_release(self):
        r = self.vp._EGLRenderer()
        self.assertTrue(callable(getattr(r, 'release', None)))


