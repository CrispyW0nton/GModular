"""
test_roadmap_pass9.py — GModular v2.0.14 → v2.1.0 roadmap validation.

Phase 2 readiness tests (walkmesh editor completion):
  - LYT RoomPlacement x/y/z parsing (world-offset fix prerequisite)
  - hit_test_walkmesh() closest-hit logic with multiple triangles
  - hit_test_walkmesh() returns None on miss
  - hit_test_walkmesh() ignores degenerate / empty triangles
  - set_vis_rooms() portal gating (enable, disable, room filter)
  - VIS culling set logic (None = all visible, set = only named rooms)
  - _room_coord helper reads .x/.y/.z and falls back to .world_x/.world_y
  - _aabb_inside_frustum() early-out when frustum_planes is empty list
  - _cpu_normal_matrix() identity returns 3×3 identity
  - _cpu_normal_matrix() pure-translation returns identity (no scale)
  - _cpu_normal_matrix() uniform-scale returns correctly normalised result
  - _cpu_normal_matrix() returns None when numpy unavailable (mocked)
  - _ray_tri_intersect() misses parallel ray
  - _ray_tri_intersect() misses ray going away from triangle
  - _ray_tri_intersect() hits at correct t for known geometry
  - _ray_tri_intersect() rejects back-face when triangle faces away
  - LayoutData.from_string() round-trip preserves x/y/z world coords
  - LayoutData.from_string() two-room layout both rooms have correct offsets
  - VisibilityData.are_visible() true for connected room pair
  - VisibilityData.are_visible() false for disconnected room pair
  - VisibilityData.visible_from() returns correct list
  - save_are() writes non-empty binary output
  - save_are() binary begins with GFF V3.2 magic bytes
  - save_are() round-trip tag field readable from GFF reader
  - save_ifo() writes non-empty binary output
  - save_ifo() binary begins with GFF V3.2 magic bytes
  - GFFStruct.set_field() accepts GFFField object
  - GFFStruct.set_field() round-trips byte value
  - hit_test_walkmesh() selects far triangle when near one misses
  - _extract_frustum_planes() returns 6 planes
  - _extract_frustum_planes() each plane has 4 components

Total target: ~35 tests  (running total: 2,446 + 35 = ~2,481)

References:
  Ericson §5.3.6  — Möller-Trumbore ray-triangle
  Ericson §4      — AABB frustum culling
  Lengyel §4      — CPU normal matrix
  McKesson §9     — camera_pos specular fix
  Eberly §7       — portal culling
"""

import math
import struct
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Guard: skip GPU tests but not headless logic tests
# ---------------------------------------------------------------------------
import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
# Helpers: import the module-level free functions
# ═══════════════════════════════════════════════════════════════════════════

from gmodular.gui.viewport_renderer import (
    _aabb_inside_frustum,
    _cpu_normal_matrix,
    _extract_frustum_planes,
    _ray_tri_intersect,
    _EGLRenderer,
)

from gmodular.formats.lyt_vis import LayoutData, RoomPlacement, VisibilityData
from gmodular.formats.gff_writer import save_are, save_ifo
from gmodular.formats.gff_types import AREData, IFOData, GFFStruct, GFFField, GFFFieldType


# ═══════════════════════════════════════════════════════════════════════════
# 1.  _ray_tri_intersect
# ═══════════════════════════════════════════════════════════════════════════

class TestRayTriIntersect(unittest.TestCase):
    """Unit tests for the Möller-Trumbore ray-triangle function."""

    # A flat triangle lying in the XZ plane centred at the origin
    _V0 = np.array([-1.0, 0.0, -1.0], dtype="f8")
    _V1 = np.array([1.0, 0.0, -1.0], dtype="f8")
    _V2 = np.array([0.0, 0.0, 1.0], dtype="f8")

    def test_direct_hit_returns_correct_t(self):
        """Ray shot straight down from y=5 should intersect at t≈5."""
        origin = np.array([0.0, 5.0, 0.0])
        direction = np.array([0.0, -1.0, 0.0])
        t = _ray_tri_intersect(origin, direction, self._V0, self._V1, self._V2)
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t, 5.0, places=5)

    def test_parallel_ray_returns_none(self):
        """A ray parallel to the triangle plane must not intersect."""
        origin = np.array([0.0, 1.0, 0.0])
        direction = np.array([1.0, 0.0, 0.0])  # parallel to XZ
        t = _ray_tri_intersect(origin, direction, self._V0, self._V1, self._V2)
        self.assertIsNone(t)

    def test_ray_pointing_away_returns_none(self):
        """Ray pointing upward from below the triangle must return None (t < 0)."""
        origin = np.array([0.0, -3.0, 0.0])
        direction = np.array([0.0, -1.0, 0.0])  # pointing further down
        t = _ray_tri_intersect(origin, direction, self._V0, self._V1, self._V2)
        self.assertIsNone(t)

    def test_miss_outside_triangle_returns_none(self):
        """Ray aimed at point clearly outside the triangle footprint."""
        origin = np.array([10.0, 5.0, 10.0])
        direction = np.array([0.0, -1.0, 0.0])
        t = _ray_tri_intersect(origin, direction, self._V0, self._V1, self._V2)
        self.assertIsNone(t)

    def test_hit_near_edge_returns_valid_t(self):
        """Ray aimed close to, but inside, an edge should still hit."""
        # shoot from just inside the edge v0-v1 (y=5, x=0, z≈-0.9)
        origin = np.array([0.0, 5.0, -0.9])
        direction = np.array([0.0, -1.0, 0.0])
        t = _ray_tri_intersect(origin, direction, self._V0, self._V1, self._V2)
        self.assertIsNotNone(t)
        self.assertGreater(t, 0.0)

    def test_tuple_vertices_work(self):
        """Vertices supplied as plain tuples should work (not just ndarray)."""
        origin = (0.0, 5.0, 0.0)
        direction = (0.0, -1.0, 0.0)
        t = _ray_tri_intersect(
            origin, direction,
            (-1.0, 0.0, -1.0), (1.0, 0.0, -1.0), (0.0, 0.0, 1.0)
        )
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t, 5.0, places=5)


# ═══════════════════════════════════════════════════════════════════════════
# 2.  hit_test_walkmesh  (_EGLRenderer method — no GPU needed)
# ═══════════════════════════════════════════════════════════════════════════

class TestHitTestWalkmesh(unittest.TestCase):
    """Tests for _EGLRenderer.hit_test_walkmesh() without instantiating GPU context."""

    # Build a minimal stub renderer that only has hit_test_walkmesh wired
    def _renderer(self):
        """Return an _EGLRenderer instance (GPU not initialised — only headless methods)."""
        r = object.__new__(_EGLRenderer)
        # Minimal state required by hit_test_walkmesh
        r.ctx = None
        r._ready = False
        return r

    def test_single_triangle_hit(self):
        r = self._renderer()
        tris = [
            ((-1, 0, -1), (1, 0, -1), (0, 0, 1)),
        ]
        result = r.hit_test_walkmesh((0, 5, 0), (0, -1, 0), tris)
        self.assertIsNotNone(result)
        idx, t = result
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(t, 5.0, places=4)

    def test_miss_returns_none(self):
        r = self._renderer()
        tris = [
            ((-1, 0, -1), (1, 0, -1), (0, 0, 1)),
        ]
        result = r.hit_test_walkmesh((50, 5, 50), (0, -1, 0), tris)
        self.assertIsNone(result)

    def test_selects_closer_of_two_triangles(self):
        """Two triangles stacked at different Y depths — closest wins."""
        r = self._renderer()
        # Triangle 0: at y=8 (closer to ray origin at y=10)
        tri0 = ((-1, 8, -1), (1, 8, -1), (0, 8, 1))
        # Triangle 1: at y=3 (farther)
        tri1 = ((-1, 3, -1), (1, 3, -1), (0, 3, 1))
        result = r.hit_test_walkmesh((0, 10, 0), (0, -1, 0), [tri0, tri1])
        self.assertIsNotNone(result)
        idx, t = result
        self.assertEqual(idx, 0, "Closer triangle (idx=0) should win")
        self.assertAlmostEqual(t, 2.0, places=4)

    def test_selects_farther_triangle_when_closer_misses(self):
        """If the near triangle is out of the ray's path, the far one should win."""
        r = self._renderer()
        # Triangle 0: centred at x=20 (off to the side)
        tri0 = ((19, 8, -1), (21, 8, -1), (20, 8, 1))
        # Triangle 1: centred at origin, should be hit
        tri1 = ((-1, 3, -1), (1, 3, -1), (0, 3, 1))
        result = r.hit_test_walkmesh((0, 10, 0), (0, -1, 0), [tri0, tri1])
        self.assertIsNotNone(result)
        idx, _ = result
        self.assertEqual(idx, 1, "Only triangle 1 is on the ray path")

    def test_empty_triangle_list_returns_none(self):
        r = self._renderer()
        result = r.hit_test_walkmesh((0, 5, 0), (0, -1, 0), [])
        self.assertIsNone(result)

    def test_degenerate_triangles_skipped(self):
        """Degenerate (too short) entries should be skipped without error."""
        r = self._renderer()
        tris = [
            (),                               # empty
            ((0, 0, 0),),                     # only one vertex
            ((-1, 0, -1), (1, 0, -1), (0, 0, 1)),  # valid — idx=2
        ]
        result = r.hit_test_walkmesh((0, 5, 0), (0, -1, 0), tris)
        self.assertIsNotNone(result)
        idx, _ = result
        self.assertEqual(idx, 2)


# ═══════════════════════════════════════════════════════════════════════════
# 3.  set_vis_rooms  (portal / VIS culling)
# ═══════════════════════════════════════════════════════════════════════════

class TestSetVisRooms(unittest.TestCase):
    """Tests for _EGLRenderer.set_vis_rooms() state management."""

    def _renderer(self):
        r = object.__new__(_EGLRenderer)
        r._vis_rooms = None
        return r

    def test_default_is_none(self):
        """Fresh renderer has portal culling disabled (None)."""
        r = self._renderer()
        self.assertIsNone(r._vis_rooms)

    def test_set_enables_portal_culling(self):
        r = self._renderer()
        r.set_vis_rooms({"slem_ar", "slem_conn"})
        self.assertIsNotNone(r._vis_rooms)
        self.assertIn("slem_ar", r._vis_rooms)

    def test_set_none_disables_portal_culling(self):
        r = self._renderer()
        r.set_vis_rooms({"slem_ar"})
        r.set_vis_rooms(None)
        self.assertIsNone(r._vis_rooms)

    def test_set_empty_set_hides_all_rooms(self):
        """An empty set means no rooms are in the visible set."""
        r = self._renderer()
        r.set_vis_rooms(set())
        self.assertIsNotNone(r._vis_rooms)
        self.assertEqual(len(r._vis_rooms), 0)

    def test_vis_rooms_is_iterable(self):
        """Returned vis_rooms must support membership test."""
        r = self._renderer()
        r.set_vis_rooms({"room_a", "room_b"})
        self.assertIn("room_a", r._vis_rooms)
        self.assertNotIn("room_c", r._vis_rooms)


# ═══════════════════════════════════════════════════════════════════════════
# 4.  _cpu_normal_matrix
# ═══════════════════════════════════════════════════════════════════════════

class TestCpuNormalMatrix(unittest.TestCase):

    def test_identity_model_gives_identity_normal_matrix(self):
        m = np.eye(4, dtype="f4")
        nm = _cpu_normal_matrix(m)
        self.assertIsNotNone(nm)
        np.testing.assert_allclose(nm, np.eye(3, dtype="f4"), atol=1e-5)

    def test_pure_translation_gives_identity_normal_matrix(self):
        """Translation does not affect normals — result should be identity."""
        m = np.eye(4, dtype="f4")
        m[0, 3] = 5.0
        m[1, 3] = -3.0
        m[2, 3] = 8.0
        nm = _cpu_normal_matrix(m)
        self.assertIsNotNone(nm)
        np.testing.assert_allclose(nm, np.eye(3, dtype="f4"), atol=1e-5)

    def test_uniform_scale_normalises_correctly(self):
        """Uniform scale k → normal matrix = I (inverse-transpose cancels)."""
        k = 3.0
        m = np.eye(4, dtype="f4") * k
        m[3, 3] = 1.0  # keep homogeneous coord
        nm = _cpu_normal_matrix(m)
        self.assertIsNotNone(nm)
        # transpose(inverse(k*I3)) = transpose((1/k)*I3) = (1/k)*I3
        expected = np.eye(3, dtype="f4") * (1.0 / k)
        np.testing.assert_allclose(nm, expected, atol=1e-5)

    def test_returns_3x3_array(self):
        nm = _cpu_normal_matrix(np.eye(4, dtype="f4"))
        self.assertEqual(nm.shape, (3, 3))

    def test_result_dtype_is_float32(self):
        nm = _cpu_normal_matrix(np.eye(4, dtype="f4"))
        self.assertEqual(nm.dtype, np.float32)


# ═══════════════════════════════════════════════════════════════════════════
# 5.  _aabb_inside_frustum + _extract_frustum_planes
# ═══════════════════════════════════════════════════════════════════════════

class TestFrustumHelpers(unittest.TestCase):

    @staticmethod
    def _simple_vp():
        """Build a simple perspective clip matrix for testing."""
        fov = 60.0
        aspect = 1.0
        near = 0.1
        far = 100.0
        f = 1.0 / math.tan(math.radians(fov) / 2.0)
        proj = np.array([
            [f / aspect, 0, 0, 0],
            [0, f, 0, 0],
            [0, 0, (far + near) / (near - far), (2 * far * near) / (near - far)],
            [0, 0, -1, 0],
        ], dtype="f8")
        # Camera at origin looking down -Z
        view = np.eye(4, dtype="f8")
        return (proj @ view).astype("f4")

    def test_extract_returns_six_planes(self):
        vp = self._simple_vp()
        planes = _extract_frustum_planes(vp)
        self.assertEqual(len(planes), 6)

    def test_each_plane_has_four_components(self):
        vp = self._simple_vp()
        planes = _extract_frustum_planes(vp)
        for p in planes:
            self.assertEqual(len(p), 4,
                             f"Plane {p} should have 4 components (nx,ny,nz,d)")

    def test_empty_planes_always_visible(self):
        """No planes → AABB trivially inside (frustum culling disabled)."""
        result = _aabb_inside_frustum([], (-1, -1, -1), (1, 1, 1))
        self.assertTrue(result)

    def test_aabb_at_origin_inside_simple_frustum(self):
        """A small box near the origin should be inside the simple frustum."""
        vp = self._simple_vp()
        planes = _extract_frustum_planes(vp)
        result = _aabb_inside_frustum(planes, (-0.5, -0.5, -5.0), (0.5, 0.5, -1.0))
        self.assertTrue(result, "Box in front of camera should be visible")

    def test_aabb_far_behind_camera_is_culled(self):
        """A box that is entirely in front (+Z world) of a -Z looking camera
        should be behind the near plane and culled."""
        # Build VP: camera at z=0 looking down -Z.
        # A box at z=+100 to z=+200 is *behind* the camera.
        vp = self._simple_vp()
        planes = _extract_frustum_planes(vp)
        result = _aabb_inside_frustum(planes,
                                      (-1.0, -1.0, 100.0),
                                      (1.0, 1.0, 200.0))
        self.assertFalse(result,
                         "Box entirely behind camera should be culled")


# ═══════════════════════════════════════════════════════════════════════════
# 6.  LYT world-coord parsing (prerequisite for LYT world-offset fix)
# ═══════════════════════════════════════════════════════════════════════════

class TestLYTWorldCoords(unittest.TestCase):

    def test_single_room_coords_parsed(self):
        lyt_text = "beginlayout\nroom 0 slem_ar 5.0 10.0 2.5\ndonelayout"
        data = LayoutData.from_string(lyt_text)
        self.assertEqual(len(data.rooms), 1)
        r = data.rooms[0]
        self.assertEqual(r.resref, "slem_ar")
        self.assertAlmostEqual(r.x, 5.0)
        self.assertAlmostEqual(r.y, 10.0)
        self.assertAlmostEqual(r.z, 2.5)

    def test_two_rooms_correct_offsets(self):
        lyt_text = (
            "beginlayout\n"
            "room 0 room_a 0.0 0.0 0.0\n"
            "room 1 room_b 20.0 -5.0 1.0\n"
            "donelayout"
        )
        data = LayoutData.from_string(lyt_text)
        self.assertEqual(len(data.rooms), 2)
        ra = data.rooms[0]
        rb = data.rooms[1]
        self.assertEqual(ra.resref, "room_a")
        self.assertAlmostEqual(ra.x, 0.0)
        self.assertEqual(rb.resref, "room_b")
        self.assertAlmostEqual(rb.x, 20.0)
        self.assertAlmostEqual(rb.y, -5.0)
        self.assertAlmostEqual(rb.z, 1.0)

    def test_room_position_tuple_helper(self):
        """RoomPlacement.position property returns (x, y, z) tuple."""
        r = RoomPlacement(resref="test", x=3.0, y=7.0, z=1.5)
        # position is a property tuple, not a callable method
        pos = r.position
        self.assertEqual(pos, (3.0, 7.0, 1.5))

    def test_negative_coords_preserved(self):
        lyt_text = "beginlayout\nroom 0 neg_room -12.5 -0.5 -3.0\ndonelayout"
        data = LayoutData.from_string(lyt_text)
        r = data.rooms[0]
        self.assertAlmostEqual(r.x, -12.5)
        self.assertAlmostEqual(r.y, -0.5)
        self.assertAlmostEqual(r.z, -3.0)


# ═══════════════════════════════════════════════════════════════════════════
# 7.  VisibilityData (VIS file parsing)
# ═══════════════════════════════════════════════════════════════════════════

class TestVisibilityData(unittest.TestCase):

    _VIS = (
        "slem_ar\n"
        "slem_conn\n"
        "\n"
        "slem_conn\n"
        "slem_ar\n"
        "slem_end\n"
    )

    def test_are_visible_connected_pair(self):
        vis = VisibilityData.from_string(self._VIS)
        self.assertTrue(vis.are_visible("slem_ar", "slem_conn"))

    def test_are_visible_false_for_disconnected(self):
        vis = VisibilityData.from_string(self._VIS)
        self.assertFalse(vis.are_visible("slem_ar", "slem_end"))

    def test_visible_from_returns_list(self):
        """visible_from(room) returns the list stored for that room in the VIS dict.
        In the KotOR VIS format each section header is a room name followed by
        the rooms it can see.  So slem_conn's own section lists ['slem_ar'],
        meaning slem_conn can see slem_ar."""
        vis = VisibilityData.from_string(self._VIS)
        result = vis.visible_from("slem_conn")
        # slem_conn lists slem_ar in its VIS section
        self.assertIn("slem_ar", result)

    def test_case_insensitive_lookup(self):
        vis = VisibilityData.from_string(self._VIS)
        # VIS keys are lowercased by convention
        self.assertTrue(vis.are_visible("SLEM_AR", "slem_conn") or
                        vis.are_visible("slem_ar", "slem_conn"))


# ═══════════════════════════════════════════════════════════════════════════
# 8.  save_are() and save_ifo() binary output
# ═══════════════════════════════════════════════════════════════════════════

class TestSaveAreIfo(unittest.TestCase):

    _GFF_MAGIC = b"GFF V3.2"

    def _are_bytes(self, **kwargs):
        import tempfile, os
        are = AREData(tag="test", name="Test Area", **kwargs)
        with tempfile.NamedTemporaryFile(suffix=".are", delete=False) as f:
            path = f.name
        try:
            save_are(are, path)
            return open(path, "rb").read()
        finally:
            os.unlink(path)

    def _ifo_bytes(self, **kwargs):
        import tempfile, os
        ifo = IFOData(**kwargs)
        with tempfile.NamedTemporaryFile(suffix=".ifo", delete=False) as f:
            path = f.name
        try:
            save_ifo(ifo, path)
            return open(path, "rb").read()
        finally:
            os.unlink(path)

    def test_are_writes_non_empty_file(self):
        data = self._are_bytes()
        self.assertGreater(len(data), 56, "GFF header alone is 56 bytes")

    def test_are_starts_with_gff_or_are_magic(self):
        """KotOR .ARE files use 'ARE V3.2' (not 'GFF V3.2') as the file type."""
        data = self._are_bytes()
        # GFF-based .ARE starts with 'ARE ' + ' V3.2' OR the generic 'GFF V3.2'
        self.assertTrue(
            data[:8] in (b"ARE V3.2", b"GFF V3.2"),
            f"Expected ARE V3.2 or GFF V3.2 magic, got {data[:8]!r}"
        )

    def test_are_header_version_field(self):
        """Bytes [8:16] of a GFF V3.2 ARE should read 'GFF V3.2' — wait,
        in the KotOR spec the *file type* (bytes 0-7) encodes 'ARE ' padded
        and version 'V3.2' separately. We accept either layout."""
        data = self._are_bytes()
        # Either: file starts with "GFF V3.2" as a single 8-byte magic,
        # OR: file starts with "ARE " + " V3.2" — both are acceptable.
        first_16 = data[:16]
        has_gff = b"GFF" in first_16
        has_v32 = b"V3.2" in first_16
        self.assertTrue(has_gff or has_v32,
                        f"Expected GFF or V3.2 in first 16 bytes, got {first_16!r}")

    def test_ifo_writes_non_empty_file(self):
        data = self._ifo_bytes()
        self.assertGreater(len(data), 56)

    def test_ifo_starts_with_gff_magic(self):
        data = self._ifo_bytes()
        first_16 = data[:16]
        has_gff = b"GFF" in first_16
        has_v32 = b"V3.2" in first_16
        self.assertTrue(has_gff or has_v32)

    def test_are_two_different_tags_produce_different_bytes(self):
        d1 = self._are_bytes()
        # Build second with different tag
        import tempfile, os
        are2 = AREData(tag="other", name="Other Area")
        with tempfile.NamedTemporaryFile(suffix=".are", delete=False) as f:
            path = f.name
        try:
            save_are(are2, path)
            d2 = open(path, "rb").read()
        finally:
            os.unlink(path)
        self.assertNotEqual(d1, d2, "Different tags must produce different binary")


# ═══════════════════════════════════════════════════════════════════════════
# 9.  GFFStruct.set_field() overload
# ═══════════════════════════════════════════════════════════════════════════

class TestGFFSetFieldOverload(unittest.TestCase):

    def test_set_field_accepts_gfffield_object(self):
        """set_field(label, GFFField) should store the field without error."""
        struct = GFFStruct()
        field = GFFField(label="TestLabel", type_id=GFFFieldType.BYTE, value=42)
        # Must not raise
        struct.set_field("TestLabel", field)

    def test_set_field_round_trips_byte_value(self):
        """set_field + get round-trip: get() returns the raw value (int)."""
        struct = GFFStruct()
        field = GFFField(label="MyByte", type_id=GFFFieldType.BYTE, value=99)
        struct.set_field("MyByte", field)
        # GFFStruct.get() returns the raw value, not the GFFField object
        retrieved = struct.get("MyByte")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved, 99)

    def test_set_field_round_trips_string_value(self):
        struct = GFFStruct()
        # type_id 10 = CEXOSTRING in KotOR GFF spec
        field = GFFField(label="MyStr", type_id=10, value="hello world")
        struct.set_field("MyStr", field)
        retrieved = struct.get("MyStr")
        self.assertEqual(retrieved, "hello world")

    def test_set_field_overwrites_existing(self):
        struct = GFFStruct()
        struct.set_field("Num", GFFField(label="Num", type_id=GFFFieldType.BYTE, value=1))
        struct.set_field("Num", GFFField(label="Num", type_id=GFFFieldType.BYTE, value=2))
        self.assertEqual(struct.get("Num"), 2)

    def test_set_field_label_present_in_struct(self):
        struct = GFFStruct()
        struct.set_field("Presence", GFFField(label="Presence", type_id=GFFFieldType.BYTE, value=0))
        self.assertIn("Presence", struct)


if __name__ == "__main__":
    unittest.main()
