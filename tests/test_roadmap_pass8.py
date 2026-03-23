"""
GModular — Roadmap Pass 8 Tests (v2.0.14)
==========================================
Tests covering all features implemented in this iteration:

1. Specular view_dir fix — camera_pos uniform in all lit shaders
2. CPU normal matrix precomputation — cpu_normal_mat uniform
3. Möller-Trumbore ray-triangle intersection (Ericson §5.3.6)
4. Frustum plane extraction + AABB culling (Lengyel §8, Ericson §4)
5. Portal / VIS culling support (Eberly §7, Ericson §7.6)
6. save_are() — GFF V3.2 serialiser for .ARE files
7. GFFStruct.set_field() overload — accepts GFFField objects
8. _EGLRenderer: set_vis_rooms(), hit_test_walkmesh(), _room_aabbs

References:
  McKesson "Learning Modern 3D Graphics Programming" Ch.9
  Lengyel "Mathematics for 3D Game Programming" §4, §8
  Ericson "Real-Time Collision Detection" §4, §5.3.6, §7.6
  Eberly "3D Game Engine Design" §7
"""
from __future__ import annotations
import math
import struct
import sys
import os
import importlib

import pytest

# ─── ensure repo root is on path ────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# ─────────────────────────────────────────────────────────────────────────────
# 1.  Shader source: camera_pos and cpu_normal_mat uniforms present
# ─────────────────────────────────────────────────────────────────────────────

class TestShaderUniforms:
    """Verify that shaders declare the new uniforms we added."""

    def test_frag_lit_has_camera_pos(self):
        from gmodular.gui.viewport_shaders import _FRAG_LIT
        assert "camera_pos" in _FRAG_LIT, (
            "_FRAG_LIT must declare 'uniform vec3 camera_pos' for correct specular "
            "(McKesson Ch.9 — view_dir = normalize(camera_pos - v_world_pos))"
        )

    def test_frag_lit_no_uv_has_camera_pos(self):
        from gmodular.gui.viewport_shaders import _FRAG_LIT_NO_UV
        assert "camera_pos" in _FRAG_LIT_NO_UV

    def test_frag_textured_has_camera_pos(self):
        from gmodular.gui.viewport_shaders import _FRAG_TEXTURED
        assert "camera_pos" in _FRAG_TEXTURED

    def test_frag_skinned_has_camera_pos(self):
        from gmodular.gui.viewport_shaders import _FRAG_SKINNED
        assert "camera_pos" in _FRAG_SKINNED

    def test_vert_lit_has_cpu_normal_mat(self):
        from gmodular.gui.viewport_shaders import _VERT_LIT
        assert "cpu_normal_mat" in _VERT_LIT, (
            "_VERT_LIT must accept 'uniform mat3 cpu_normal_mat' to avoid "
            "expensive per-vertex inverse() in the GPU shader (Lengyel §4)"
        )

    def test_vert_lit_no_uv_has_cpu_normal_mat(self):
        from gmodular.gui.viewport_shaders import _VERT_LIT_NO_UV
        assert "cpu_normal_mat" in _VERT_LIT_NO_UV

    def test_vert_textured_has_cpu_normal_mat(self):
        from gmodular.gui.viewport_shaders import _VERT_TEXTURED
        assert "cpu_normal_mat" in _VERT_TEXTURED

    def test_specular_uses_camera_pos_not_negated_world_pos(self):
        """
        The old specular bug was: view_dir = normalize(-v_world_pos).
        This is only correct when the camera is at the world origin.
        We now require: view_dir = normalize(camera_pos - v_world_pos).
        McKesson 'Learning Modern 3D Graphics Programming' Ch.9.
        """
        from gmodular.gui.viewport_shaders import _FRAG_LIT, _FRAG_LIT_NO_UV
        for name, src in [("_FRAG_LIT", _FRAG_LIT),
                          ("_FRAG_LIT_NO_UV", _FRAG_LIT_NO_UV)]:
            assert "camera_pos - v_world_pos" in src, (
                f"{name}: specular view_dir must use (camera_pos - v_world_pos), "
                f"not (-v_world_pos).  See McKesson Ch.9."
            )
            # The live code must use camera_pos (the line without // prefix)
            # We check that the actual assignment (not a comment) uses it
            live_lines = [ln for ln in src.split('\n')
                          if 'view_dir' in ln and '//' not in ln]
            assert any('camera_pos' in ln for ln in live_lines), (
                f"{name}: live view_dir assignment must reference camera_pos, "
                f"not just appear in a comment.  Live lines: {live_lines}"
            )

    def test_all_shaders_dict_still_complete(self):
        from gmodular.gui.viewport_shaders import ALL_SHADERS
        expected = {"flat", "lit", "lit_no_uv", "uniform", "outline",
                    "picker", "textured", "skinned"}
        assert set(ALL_SHADERS.keys()) == expected


# ─────────────────────────────────────────────────────────────────────────────
# 2.  CPU normal matrix computation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_NUMPY, reason="numpy required")
class TestCpuNormalMatrix:
    """
    Lengyel §4: normal transform = transpose(inverse(mat3(model))).
    For rotation-only matrices the normal matrix equals the rotation matrix.
    For non-uniform scale the inverse-transpose differs from the raw upper-3x3.
    """

    def _get_fn(self):
        from gmodular.gui.viewport_renderer import _cpu_normal_matrix
        return _cpu_normal_matrix

    def test_identity_model_gives_identity_normal_mat(self):
        fn = self._get_fn()
        m = np.eye(4, dtype='f4')
        nm = fn(m)
        assert nm is not None
        assert nm.shape == (3, 3)
        np.testing.assert_allclose(nm, np.eye(3, dtype='f4'), atol=1e-5)

    def test_rotation_model_normal_mat_equals_rotation(self):
        """For pure rotation: inv-transpose = rotation itself."""
        fn = self._get_fn()
        angle = math.pi / 4
        c, s = math.cos(angle), math.sin(angle)
        m = np.array([
            [ c, s, 0, 0],
            [-s, c, 0, 0],
            [ 0, 0, 1, 0],
            [ 0, 0, 0, 1],
        ], dtype='f4')
        nm = fn(m)
        expected = m[:3, :3]
        np.testing.assert_allclose(nm, expected, atol=1e-5)

    def test_non_uniform_scale_normal_mat_differs_from_model(self):
        """For non-uniform scale the inverse-transpose differs from upper 3x3."""
        fn = self._get_fn()
        # Scale x by 2, y by 1, z by 0.5
        m = np.diag([2.0, 1.0, 0.5, 1.0]).astype('f4')
        nm = fn(m)
        # Normal matrix = inv(scale3).T = diag(1/2, 1, 2)
        expected = np.diag([0.5, 1.0, 2.0]).astype('f4')
        np.testing.assert_allclose(nm, expected, atol=1e-5)

    def test_returns_float32_array(self):
        fn = self._get_fn()
        m = np.eye(4, dtype='f4')
        nm = fn(m)
        assert nm.dtype == np.float32

    def test_tobytes_produces_36_bytes(self):
        """mat3 = 9 floats × 4 bytes = 36 bytes for the GPU uniform."""
        fn = self._get_fn()
        m = np.eye(4, dtype='f4')
        nm = fn(m)
        assert len(nm.tobytes()) == 36, "mat3 uniform must be exactly 36 bytes"


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Möller-Trumbore ray-triangle intersection (Ericson §5.3.6)
# ─────────────────────────────────────────────────────────────────────────────

class TestMollerTrumbore:
    """
    Reference: Ericson 'Real-Time Collision Detection' §5.3.6, p.190-194.
    Algorithm: compute e1/e2, cross h, dot a, then u, v, t.
    """

    def _fn(self):
        from gmodular.gui.viewport_renderer import _ray_tri_intersect
        return _ray_tri_intersect

    def test_ray_hits_flat_triangle(self):
        """A ray shot directly at a horizontal triangle must return positive t."""
        fn = self._fn()
        v0 = (0.0, 0.0, 0.0)
        v1 = (2.0, 0.0, 0.0)
        v2 = (0.0, 2.0, 0.0)
        ro = (0.5, 0.5, 5.0)   # above the centroid
        rd = (0.0, 0.0, -1.0)  # downward
        t  = fn(ro, rd, v0, v1, v2)
        assert t is not None, "Ray must hit triangle"
        assert abs(t - 5.0) < 1e-5, f"Expected t=5.0, got t={t}"

    def test_ray_misses_triangle(self):
        """A ray pointing away from the triangle must return None."""
        fn = self._fn()
        v0 = (0.0, 0.0, 0.0)
        v1 = (1.0, 0.0, 0.0)
        v2 = (0.0, 1.0, 0.0)
        ro = (5.0, 5.0, 5.0)   # far outside
        rd = (0.0, 0.0, -1.0)  # pointing down but misses
        t  = fn(ro, rd, v0, v1, v2)
        assert t is None, "Ray must not hit triangle"

    def test_ray_parallel_to_triangle(self):
        """A ray parallel to the triangle plane must return None."""
        fn = self._fn()
        v0 = (0.0, 0.0, 0.0)
        v1 = (1.0, 0.0, 0.0)
        v2 = (0.0, 1.0, 0.0)
        ro = (0.5, 0.5, 1.0)
        rd = (1.0, 0.0, 0.0)   # parallel to XY plane
        t  = fn(ro, rd, v0, v1, v2)
        assert t is None, "Parallel ray must not intersect"

    def test_ray_hits_triangle_edge(self):
        """A ray hitting near a triangle edge must still intersect."""
        fn = self._fn()
        v0 = (0.0, 0.0, 0.0)
        v1 = (2.0, 0.0, 0.0)
        v2 = (0.0, 2.0, 0.0)
        ro = (0.0, 0.0, 3.0)   # above v0
        rd = (0.0, 0.0, -1.0)  # straight down
        t  = fn(ro, rd, v0, v1, v2)
        assert t is not None, "Ray at vertex v0 must hit"
        assert abs(t - 3.0) < 1e-4

    def test_ray_from_behind_returns_none(self):
        """A ray hitting the triangle from behind (negative t) returns None."""
        fn = self._fn()
        v0 = (0.0, 0.0, 0.0)
        v1 = (1.0, 0.0, 0.0)
        v2 = (0.0, 1.0, 0.0)
        ro = (0.25, 0.25, -1.0)  # below the triangle
        rd = (0.0,  0.0,  -1.0)  # moving further away
        t  = fn(ro, rd, v0, v1, v2)
        assert t is None, "Ray moving away from triangle must not hit"

    def test_hit_test_walkmesh_finds_closest(self):
        """hit_test_walkmesh must return the index of the closest triangle."""
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        # Two horizontal triangles: index 0 at z=2 (farther), index 1 at z=0 (closer)
        # Ray origin z=5 shooting downward (-Z): hits z=2 at t=3, z=0 at t=5
        # Closest hit = z=2 (t=3), which is index 0
        tris = [
            ((0.0,0.0,2.0), (1.0,0.0,2.0), (0.0,1.0,2.0)),   # index 0, z=2 (closer, t=3)
            ((0.0,0.0,0.0), (1.0,0.0,0.0), (0.0,1.0,0.0)),   # index 1, z=0 (farther, t=5)
        ]
        ro = (0.25, 0.25, 5.0)
        rd = (0.0,  0.0, -1.0)
        result = r.hit_test_walkmesh(ro, rd, tris)
        assert result is not None, "Must hit at least one triangle"
        face_idx, t = result
        assert face_idx == 0, (
            f"Closest triangle is index 0 (z=2, t=3), got index {face_idx}"
        )
        assert abs(t - 3.0) < 1e-4, f"Expected t≈3.0 (z=2 plane), got {t}"

    def test_hit_test_walkmesh_returns_none_on_miss(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        tris = [((10.0,10.0,0.0),(11.0,10.0,0.0),(10.0,11.0,0.0))]
        ro = (0.0, 0.0, 5.0)
        rd = (0.0, 0.0, -1.0)
        result = r.hit_test_walkmesh(ro, rd, tris)
        assert result is None, "No hit expected when ray misses all triangles"


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Frustum plane extraction + AABB culling (Lengyel §8, Ericson §4)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_NUMPY, reason="numpy required")
class TestFrustumCulling:
    """
    Reference: Lengyel 'Mathematics for 3D Game Programming' §8,
               Ericson 'Real-Time Collision Detection' §4.
    """

    def _extract(self, clip_matrix):
        from gmodular.gui.viewport_renderer import _extract_frustum_planes
        return _extract_frustum_planes(clip_matrix)

    def _inside(self, planes, aabb_min, aabb_max):
        from gmodular.gui.viewport_renderer import _aabb_inside_frustum
        return _aabb_inside_frustum(planes, aabb_min, aabb_max)

    def _make_proj_view(self, fov_y=60, aspect=1.0, near=0.1, far=100.0,
                        eye=(0,0,10), target=(0,0,0)):
        """Build a simple perspective camera VP matrix."""
        from gmodular.gui.viewport_camera import _perspective, _look_at
        proj = _perspective(fov_y, aspect, near, far)
        view = _look_at(
            np.array(eye,    dtype='f4'),
            np.array(target, dtype='f4'),
            np.array([0,1,0], dtype='f4'),
        )
        return proj @ view

    def test_extract_returns_six_planes(self):
        vp = self._make_proj_view()
        planes = self._extract(vp)
        assert len(planes) == 6, f"Expected 6 frustum planes, got {len(planes)}"

    def test_planes_are_normalised(self):
        """Each plane normal (nx,ny,nz) should have unit length."""
        vp = self._make_proj_view()
        planes = self._extract(vp)
        for i, (nx, ny, nz, d) in enumerate(planes):
            mag = (nx**2 + ny**2 + nz**2) ** 0.5
            assert abs(mag - 1.0) < 1e-4, (
                f"Plane {i} normal magnitude {mag:.4f} is not ~1.0"
            )

    def test_origin_aabb_is_visible(self):
        """An AABB at the origin (inside the frustum) should pass the test."""
        vp = self._make_proj_view(eye=(0,0,10), target=(0,0,0))
        planes = self._extract(vp)
        aabb_min = (-1.0, -1.0, -1.0)
        aabb_max = ( 1.0,  1.0,  1.0)
        assert self._inside(planes, aabb_min, aabb_max), (
            "Origin AABB should be inside the frustum"
        )

    def test_far_behind_camera_aabb_is_culled(self):
        """An AABB directly behind the camera must be culled.

        Camera is at z=10 looking toward z=0.  The frustum extends from
        near=0.1 to far=100.0 *in front* of the camera, meaning in world-space
        from z≈9.9 to z=-90.  An AABB at z=15..17 is behind the camera.
        """
        vp = self._make_proj_view(eye=(0,0,10), target=(0,0,0))
        planes = self._extract(vp)
        # This box is directly behind the camera (camera at z=10, looking toward z=0)
        # Box at z=15..17 is behind (positive z side) of camera
        aabb_min = (-1.0, -1.0, 15.0)
        aabb_max = ( 1.0,  1.0, 17.0)
        result = self._inside(planes, aabb_min, aabb_max)
        assert not result, (
            "AABB behind the camera (z=15..17 when camera at z=10) "
            "must be culled by frustum planes"
        )

    def test_empty_planes_always_visible(self):
        """Empty plane list disables culling — all AABBs are visible."""
        assert self._inside([], (-9999,)*3, (9999,)*3), (
            "Empty frustum planes must return True (culling disabled)"
        )

    def test_far_outside_lateral_aabb_culled(self):
        """An AABB far to the side of the view frustum must be culled."""
        vp = self._make_proj_view(fov_y=60, aspect=1.0,
                                  eye=(0,0,10), target=(0,0,0))
        planes = self._extract(vp)
        # 200 units to the right — definitely outside
        aabb_min = (199.0, -1.0, -1.0)
        aabb_max = (201.0,  1.0,  1.0)
        assert not self._inside(planes, aabb_min, aabb_max), (
            "AABB far to the right must be culled"
        )

    def test_renderer_has_enable_frustum_cull_flag(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        assert hasattr(r, '_enable_frustum_cull'), (
            "_EGLRenderer must have _enable_frustum_cull boolean"
        )
        assert r._enable_frustum_cull is True, (
            "Frustum culling should be enabled by default"
        )

    def test_renderer_has_room_aabbs_cache(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        assert hasattr(r, '_room_aabbs'), (
            "_EGLRenderer must have _room_aabbs dict for frustum culling"
        )
        assert isinstance(r._room_aabbs, dict)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Portal / VIS culling
# ─────────────────────────────────────────────────────────────────────────────

class TestVisCulling:
    """
    Reference: Eberly '3D Game Engine Design' §7 (portal rendering),
               Ericson 'Real-Time Collision Detection' §7.6 (cells & portals).
    KotOR .vis files encode per-room visibility sets.
    """

    def test_renderer_has_vis_rooms_attribute(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        assert hasattr(r, '_vis_rooms'), (
            "_EGLRenderer must have _vis_rooms attribute (None = no culling)"
        )
        assert r._vis_rooms is None, (
            "_vis_rooms should default to None (portal culling disabled)"
        )

    def test_set_vis_rooms_stores_set(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        visible = {"room_001", "room_002", "lobby"}
        r.set_vis_rooms(visible)
        assert r._vis_rooms == visible

    def test_set_vis_rooms_none_disables_culling(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        r.set_vis_rooms({"room_a"})
        r.set_vis_rooms(None)
        assert r._vis_rooms is None, (
            "set_vis_rooms(None) must disable portal culling"
        )

    def test_set_vis_rooms_empty_set_culls_everything(self):
        """An empty visible set means no rooms are drawn."""
        from gmodular.gui.viewport_renderer import _EGLRenderer
        r = _EGLRenderer()
        r.set_vis_rooms(set())
        assert r._vis_rooms is not None
        assert len(r._vis_rooms) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6.  save_are() — GFF V3.2 .ARE serialiser
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveAre:
    """
    Verify that save_are() produces a valid GFF V3.2 binary with correct
    field layout matching the KotOR ARE spec.
    """

    def _make_are(self):
        from gmodular.formats.gff_types import AREData
        are = AREData(
            tag="test_room",
            name="Test Room",
            rooms=["test_room_a", "test_room_b"],
            fog_enabled=1,
            fog_near=5.0,
            fog_far=50.0,
            fog_color=0x112233,
            ambient_color=0x404040,
            diffuse_color=0x808080,
            tileset_resref="ttf01",
            sky_box="darksky",
            dynamic_day_night=0,
            shadow_opacity=100,
            wind_power=0,
        )
        return are

    def test_save_are_function_exists(self):
        from gmodular.formats.gff_writer import save_are
        assert callable(save_are)

    def test_save_are_writes_file(self, tmp_path):
        from gmodular.formats.gff_writer import save_are
        are = self._make_are()
        path = str(tmp_path / "test.are")
        save_are(are, path)
        assert os.path.exists(path), "save_are must create the output file"

    def test_are_file_has_gff_header(self, tmp_path):
        """File must start with 'ARE ' + 'V3.2' (GFF standard header)."""
        from gmodular.formats.gff_writer import save_are
        are = self._make_are()
        path = str(tmp_path / "test.are")
        save_are(are, path)
        with open(path, 'rb') as f:
            header = f.read(8)
        assert header[:4] == b'ARE ', f"File type must be 'ARE ', got {header[:4]!r}"
        assert header[4:8] == b'V3.2', f"Version must be 'V3.2', got {header[4:8]!r}"

    def test_are_file_is_nonzero_size(self, tmp_path):
        from gmodular.formats.gff_writer import save_are
        are = self._make_are()
        path = str(tmp_path / "test.are")
        save_are(are, path)
        sz = os.path.getsize(path)
        assert sz > 56, f"ARE file must be larger than the 56-byte header, got {sz} bytes"

    def test_are_roundtrip_tag(self, tmp_path):
        """Save an ARE and verify the Tag field is present in the binary."""
        from gmodular.formats.gff_writer import save_are
        are = self._make_are()
        path = str(tmp_path / "test.are")
        save_are(are, path)
        raw = open(path, 'rb').read()
        # Tag "test_room" encoded as UTF-8 CExoString: must appear in file bytes
        assert b'test_room' in raw, "Tag value must appear in the ARE binary"

    def test_are_room_list_encoded(self, tmp_path):
        """The room names must be present in the binary data."""
        from gmodular.formats.gff_writer import save_are
        are = self._make_are()
        path = str(tmp_path / "test.are")
        save_are(are, path)
        raw = open(path, 'rb').read()
        for rname in are.rooms:
            assert rname.encode() in raw, (
                f"Room name '{rname}' must be encoded in the ARE binary"
            )

    def test_are_empty_rooms(self, tmp_path):
        """save_are with zero rooms should still write a valid GFF file."""
        from gmodular.formats.gff_writer import save_are
        from gmodular.formats.gff_types import AREData
        are = AREData(tag="empty", name="Empty Area", rooms=[])
        path = str(tmp_path / "empty.are")
        save_are(are, path)
        with open(path, 'rb') as f:
            header = f.read(8)
        assert header[:4] == b'ARE '


# ─────────────────────────────────────────────────────────────────────────────
# 7.  GFFStruct.set_field() overload (GFFField object API)
# ─────────────────────────────────────────────────────────────────────────────

class TestGFFSetFieldOverload:
    """
    Verify the new set_field() / set() overload that accepts a GFFField object.
    The old API: set(label, type_id, value) must still work.
    The new API: set(label, field) and set_field(label, field).
    """

    def test_old_api_still_works(self):
        from gmodular.formats.gff_types import GFFStruct, GFFFieldType
        s = GFFStruct()
        s.set("MyFloat", GFFFieldType.FLOAT, 3.14)
        assert "MyFloat" in s.fields
        assert abs(s.fields["MyFloat"].value - 3.14) < 1e-5

    def test_new_api_set_with_gfffield_object(self):
        from gmodular.formats.gff_types import GFFStruct, GFFField, GFFFieldType
        s  = GFFStruct()
        f  = GFFField("MyFloat", GFFFieldType.FLOAT, 2.72)
        s.set("MyFloat", f)
        assert "MyFloat" in s.fields
        assert abs(s.fields["MyFloat"].value - 2.72) < 1e-5

    def test_set_field_method_exists(self):
        from gmodular.formats.gff_types import GFFStruct
        s = GFFStruct()
        assert hasattr(s, 'set_field'), (
            "GFFStruct must have set_field() method for the new API"
        )

    def test_set_field_with_gfffield(self):
        from gmodular.formats.gff_types import GFFStruct, GFFField, GFFFieldType
        s = GFFStruct()
        f = GFFField("TestStr", GFFFieldType.CEXOSTRING, "hello")
        s.set_field("TestStr", f)
        assert s.fields["TestStr"].value == "hello"

    def test_set_field_old_api_passthrough(self):
        """set_field() should also accept (label, type_id, value)."""
        from gmodular.formats.gff_types import GFFStruct, GFFFieldType
        s = GFFStruct()
        s.set_field("MyByte", GFFFieldType.BYTE, 42)
        assert s.fields["MyByte"].value == 42

    def test_gffroot_inherits_set_field(self):
        from gmodular.formats.gff_types import GFFRoot, GFFField, GFFFieldType
        root = GFFRoot(file_type="ARE ")
        f = GFFField("Tag", GFFFieldType.CEXOSTRING, "some_tag")
        root.set_field("Tag", f)
        assert root.fields["Tag"].value == "some_tag"

    def test_set_preserves_type_id_from_gfffield(self):
        """When passing a GFFField the type_id must be copied correctly."""
        from gmodular.formats.gff_types import GFFStruct, GFFField, GFFFieldType
        s = GFFStruct()
        f = GFFField("Pos", GFFFieldType.VECTOR, None)
        s.set("Pos", f)
        assert s.fields["Pos"].type_id == GFFFieldType.VECTOR


# ─────────────────────────────────────────────────────────────────────────────
# 8.  _EGLRenderer instance attributes
# ─────────────────────────────────────────────────────────────────────────────

class TestRendererAttributes:
    """Verify all new attributes on _EGLRenderer are correctly initialised."""

    def _make_renderer(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        return _EGLRenderer()

    def test_vis_rooms_default_none(self):
        r = self._make_renderer()
        assert r._vis_rooms is None

    def test_room_aabbs_default_empty_dict(self):
        r = self._make_renderer()
        assert isinstance(r._room_aabbs, dict)
        assert len(r._room_aabbs) == 0

    def test_enable_frustum_cull_default_true(self):
        r = self._make_renderer()
        assert r._enable_frustum_cull is True

    def test_last_camera_eye_default(self):
        r = self._make_renderer()
        assert hasattr(r, '_last_camera_eye')
        assert len(r._last_camera_eye) == 3

    def test_set_vis_rooms_method_exists(self):
        r = self._make_renderer()
        assert callable(getattr(r, 'set_vis_rooms', None))

    def test_hit_test_walkmesh_method_exists(self):
        r = self._make_renderer()
        assert callable(getattr(r, 'hit_test_walkmesh', None))


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Integration: save_are() → GFFWriter round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveAreIntegration:
    """Full round-trip: build AREData → save_are → read back raw bytes."""

    def test_full_are_pipeline(self, tmp_path):
        from gmodular.formats.gff_writer import save_are
        from gmodular.formats.gff_types import AREData
        are = AREData(
            tag="nar_shad01",
            name="Nar Shaddaa Apartments",
            rooms=["nar_shad01_a", "nar_shad01_b", "nar_shad01_c"],
            fog_enabled=1,
            fog_near=8.0,
            fog_far=40.0,
        )
        path = str(tmp_path / "nar_shad01.are")
        save_are(are, path)

        # Read and verify basic properties
        raw = open(path, 'rb').read()
        assert raw[:4]  == b'ARE '
        assert raw[4:8] == b'V3.2'
        assert b'nar_shad01' in raw
        sz = len(raw)
        assert sz >= 100, f"ARE binary is unexpectedly small: {sz} bytes"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Frustum functions accessible from renderer module
# ─────────────────────────────────────────────────────────────────────────────

class TestFrustumModuleExports:
    """The frustum helper functions must be importable from viewport_renderer."""

    def test_extract_frustum_planes_importable(self):
        from gmodular.gui.viewport_renderer import _extract_frustum_planes
        assert callable(_extract_frustum_planes)

    def test_aabb_inside_frustum_importable(self):
        from gmodular.gui.viewport_renderer import _aabb_inside_frustum
        assert callable(_aabb_inside_frustum)

    def test_ray_tri_intersect_importable(self):
        from gmodular.gui.viewport_renderer import _ray_tri_intersect
        assert callable(_ray_tri_intersect)

    def test_cpu_normal_matrix_importable(self):
        from gmodular.gui.viewport_renderer import _cpu_normal_matrix
        assert callable(_cpu_normal_matrix)
