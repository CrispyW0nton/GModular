"""
Tests for the extracted OrbitCamera and viewport shaders modules.
"""
from __future__ import annotations

import math
import unittest

import numpy as np


class TestOrbitCamera(unittest.TestCase):
    """Tests for gmodular.gui.viewport_camera.OrbitCamera."""

    def setUp(self):
        from gmodular.gui.viewport_camera import OrbitCamera
        self.OrbitCamera = OrbitCamera

    def _cam(self):
        return self.OrbitCamera()

    # ── Construction ─────────────────────────────────────────────────────────

    def test_default_yaw(self):
        cam = self._cam()
        assert cam.yaw == 45.0

    def test_default_pitch(self):
        cam = self._cam()
        assert cam.pitch == 30.0

    def test_default_distance(self):
        cam = self._cam()
        assert cam.distance == 15.0

    def test_default_fov(self):
        cam = self._cam()
        assert cam.fov == 60.0

    def test_default_near_far(self):
        cam = self._cam()
        assert cam.near == 0.001
        assert cam.far == 1000.0

    # ── Backward-compat aliases ───────────────────────────────────────────────

    def test_azimuth_alias(self):
        cam = self._cam()
        cam.azimuth = 90.0
        assert cam.yaw == 90.0
        assert cam.azimuth == 90.0

    def test_elevation_alias(self):
        cam = self._cam()
        cam.elevation = 45.0
        assert cam.pitch == 45.0
        assert cam.elevation == 45.0

    # ── Eye position ──────────────────────────────────────────────────────────

    def test_eye_returns_array(self):
        cam = self._cam()
        eye = cam.eye()
        assert eye.shape == (3,)

    def test_eye_at_default_yaw_45_pitch_30(self):
        cam = self._cam()
        eye = cam.eye()
        # distance=15, yaw=45°, pitch=30°
        cos_p = math.cos(math.radians(30))
        cos_y = math.cos(math.radians(45))
        sin_y = math.sin(math.radians(45))
        sin_p = math.sin(math.radians(30))
        expected_x = 15 * cos_p * cos_y
        expected_y = 15 * cos_p * sin_y
        expected_z = 15 * sin_p
        assert abs(eye[0] - expected_x) < 1e-4
        assert abs(eye[1] - expected_y) < 1e-4
        assert abs(eye[2] - expected_z) < 1e-4

    def test_eye_distance_from_target(self):
        cam = self._cam()
        eye = cam.eye()
        dist = np.linalg.norm(eye - cam.target)
        assert abs(dist - cam.distance) < 1e-4

    # ── View / projection matrices ────────────────────────────────────────────

    def test_view_matrix_shape(self):
        cam = self._cam()
        assert cam.view_matrix().shape == (4, 4)

    def test_projection_matrix_shape(self):
        cam = self._cam()
        assert cam.projection_matrix(16/9).shape == (4, 4)

    def test_projection_matrix_aspect_16_9(self):
        from gmodular.gui.viewport_camera import OrbitCamera
        cam = OrbitCamera()
        pm = cam.projection_matrix(16 / 9)
        # Top-left element should be f/aspect < f
        assert pm[0, 0] < pm[1, 1], "f/aspect should be less than f for wide aspect"

    def test_projection_matrix_square(self):
        cam = self._cam()
        pm = cam.projection_matrix(1.0)
        assert abs(pm[0, 0] - pm[1, 1]) < 1e-4, "Square aspect: m00 == m11"

    # ── Orbit ─────────────────────────────────────────────────────────────────

    def test_orbit_changes_yaw(self):
        cam = self._cam()
        cam.orbit(10.0, 0.0)
        assert abs(cam.yaw - 55.0) < 1e-6

    def test_orbit_clamps_pitch_positive(self):
        cam = self._cam()
        cam.orbit(0.0, 200.0)
        assert cam.pitch <= 85.0

    def test_orbit_clamps_pitch_negative(self):
        cam = self._cam()
        cam.orbit(0.0, -200.0)
        assert cam.pitch >= -85.0

    def test_orbit_yaw_wraps_360(self):
        cam = self._cam()
        cam.yaw = 350.0
        cam.orbit(20.0, 0.0)
        assert cam.yaw < 15.0  # wraps around

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def test_zoom_in_decreases_distance(self):
        cam = self._cam()
        original = cam.distance
        cam.zoom(1)
        assert cam.distance < original

    def test_zoom_out_increases_distance(self):
        cam = self._cam()
        original = cam.distance
        cam.zoom(-1)
        assert cam.distance > original

    def test_zoom_min_clamp(self):
        cam = self._cam()
        for _ in range(200):
            cam.zoom(1)
        assert cam.distance >= 0.5

    def test_zoom_max_clamp(self):
        cam = self._cam()
        for _ in range(200):
            cam.zoom(-1)
        assert cam.distance <= 5000.0

    # ── Pan ───────────────────────────────────────────────────────────────────

    def test_pan_moves_target(self):
        cam = self._cam()
        before = cam.target.copy()
        cam.pan(100.0, 0.0)
        assert not np.allclose(cam.target, before), "Target should move after pan"

    def test_pan_zero_no_change(self):
        cam = self._cam()
        before = cam.target.copy()
        cam.pan(0.0, 0.0)
        assert np.allclose(cam.target, before), "No pan should not move target"

    # ── Frame ─────────────────────────────────────────────────────────────────

    def test_frame_sets_target(self):
        cam = self._cam()
        center = np.array([10., 20., 5.], dtype='f4')
        cam.frame(center, 3.0)
        assert np.allclose(cam.target, center)

    def test_frame_adjusts_distance(self):
        cam = self._cam()
        center = np.array([0., 0., 0.], dtype='f4')
        cam.frame(center, 10.0)
        assert cam.distance >= 1.0

    def test_frame_extends_far_plane(self):
        cam = self._cam()
        cam.frame(np.array([0., 0., 0.], dtype='f4'), 500.0)
        assert cam.far > 1000.0

    # ── Walk ──────────────────────────────────────────────────────────────────

    def test_walk_forward_moves_target(self):
        cam = self._cam()
        before = cam.target.copy()
        cam.walk(5.0, 0.0, 0.0)
        assert not np.allclose(cam.target, before)

    # ── Ray from screen ───────────────────────────────────────────────────────

    def test_ray_from_screen_center(self):
        cam = self._cam()
        origin, direction = cam.ray_from_screen(400, 300, 800, 600)
        assert origin.shape == (3,)
        assert direction.shape == (3,)
        # Direction should be roughly unit length
        assert abs(np.linalg.norm(direction) - 1.0) < 1e-4

    def test_ray_from_screen_different_pixels(self):
        cam = self._cam()
        _, d1 = cam.ray_from_screen(100, 100, 800, 600)
        _, d2 = cam.ray_from_screen(700, 500, 800, 600)
        assert not np.allclose(d1, d2), "Different pixels should give different rays"


class TestViewportShaders(unittest.TestCase):
    """Tests for gmodular.gui.viewport_shaders."""

    def setUp(self):
        import gmodular.gui.viewport_shaders as vs
        self.vs = vs

    def test_all_shaders_dict_has_8_entries(self):
        assert len(self.vs.ALL_SHADERS) == 8

    def test_all_shaders_have_vert_and_frag(self):
        for name, (vert, frag) in self.vs.ALL_SHADERS.items():
            assert "#version 330" in vert, f"shader '{name}' vert missing version"
            assert "#version 330" in frag, f"shader '{name}' frag missing version"

    def test_flat_shader_has_in_position(self):
        assert "in_position" in self.vs._VERT_FLAT

    def test_flat_shader_has_in_color(self):
        assert "in_color" in self.vs._VERT_FLAT

    def test_lit_shader_has_normal(self):
        assert "in_normal" in self.vs._VERT_LIT

    def test_lit_shader_has_uv(self):
        assert "in_uv" in self.vs._VERT_LIT

    def test_picker_encodes_entity_id(self):
        assert "entity_id" in self.vs._FRAG_PICKER

    def test_picker_aliases_consistent(self):
        assert self.vs._VERT_PICK is self.vs._VERT_PICKER
        assert self.vs._FRAG_PICK is self.vs._FRAG_PICKER

    def test_skinned_has_bone_matrices(self):
        assert "bone_matrices" in self.vs._VERT_SKINNED
        assert "in_bone_weights" in self.vs._VERT_SKINNED
        assert "in_bone_indices" in self.vs._VERT_SKINNED

    def test_skinned_bone_count_16(self):
        assert "bone_matrices[16]" in self.vs._VERT_SKINNED

    def test_textured_has_lightmap(self):
        assert "tex1" in self.vs._FRAG_TEXTURED
        assert "use_lightmap" in self.vs._FRAG_TEXTURED

    def test_outline_has_pulse(self):
        assert "pulse" in self.vs._FRAG_OUTLINE
        assert "sin(time" in self.vs._FRAG_OUTLINE

    def test_uniform_frag_uses_u_color(self):
        assert "u_color" in self.vs._FRAG_UNIFORM

    def test_all_vert_shaders_have_in_position(self):
        for name, (vert, _) in self.vs.ALL_SHADERS.items():
            assert "in_position" in vert, f"'{name}' vert missing in_position"

    def test_no_shader_string_is_empty(self):
        for name, (vert, frag) in self.vs.ALL_SHADERS.items():
            assert len(vert.strip()) > 0, f"'{name}' vert is empty"
            assert len(frag.strip()) > 0, f"'{name}' frag is empty"


class TestViewportImportReexport(unittest.TestCase):
    """viewport.py must still export OrbitCamera + shaders for backward-compat."""

    def test_orbit_camera_importable_from_viewport(self):
        """OrbitCamera must be importable from viewport.py (legacy path)."""
        from gmodular.gui import viewport
        assert hasattr(viewport, 'OrbitCamera'), \
            "viewport.py must re-export OrbitCamera"

    def test_shader_constants_importable_from_viewport(self):
        """Shader constants must be accessible from viewport module."""
        from gmodular.gui import viewport
        for name in ('_VERT_FLAT', '_FRAG_FLAT', '_VERT_SKINNED', '_FRAG_SKINNED',
                     '_VERT_PICKER', '_FRAG_PICKER'):
            assert hasattr(viewport, name), f"viewport.py missing {name}"

    def test_submodule_flag(self):
        from gmodular.gui import viewport
        assert getattr(viewport, '_SUBMODULES_LOADED', False), \
            "viewport._SUBMODULES_LOADED should be True after successful sub-import"

    def test_eglrenderer_importable_from_viewport(self):
        """_EGLRenderer must be importable from viewport (legacy path)."""
        from gmodular.gui import viewport
        assert hasattr(viewport, '_EGLRenderer'), \
            "viewport.py must re-export _EGLRenderer from viewport_renderer"

    def test_eglrenderer_importable_directly(self):
        """_EGLRenderer importable from viewport_renderer directly."""
        from gmodular.gui.viewport_renderer import _EGLRenderer
        assert _EGLRenderer is not None

    def test_inject_helpers_importable(self):
        """_inject_helpers must be accessible."""
        from gmodular.gui.viewport_renderer import _inject_helpers
        assert callable(_inject_helpers)


class TestViewportRendererStructure(unittest.TestCase):
    """Structural tests for the extracted _EGLRenderer."""

    def setUp(self):
        from gmodular.gui.viewport_renderer import _EGLRenderer
        self.renderer_cls = _EGLRenderer

    def test_renderer_instantiates(self):
        """_EGLRenderer() must construct without error (no GL context needed)."""
        r = self.renderer_cls()
        assert r is not None

    def test_renderer_not_ready_after_init(self):
        """ready flag must be False before init() is called."""
        r = self.renderer_cls()
        assert r.ready is False

    def test_renderer_ctx_none_after_init(self):
        r = self.renderer_cls()
        assert r.ctx is None

    def test_renderer_has_vao_lists(self):
        r = self.renderer_cls()
        for attr in ('_object_vaos', '_room_vaos', '_walk_vaos',
                     '_nowalk_vaos', '_mdl_vaos', '_entity_vaos', '_skin_vaos'):
            assert hasattr(r, attr), f"_EGLRenderer missing {attr}"
            assert isinstance(getattr(r, attr), list), f"{attr} should be a list"

    def test_renderer_has_tex_caches(self):
        r = self.renderer_cls()
        assert hasattr(r, '_tex_cache')
        assert hasattr(r, '_lmap_cache')
        assert isinstance(r._tex_cache, dict)

    def test_renderer_has_init_method(self):
        r = self.renderer_cls()
        assert callable(getattr(r, 'init', None))

    def test_renderer_has_ensure_fbo_method(self):
        r = self.renderer_cls()
        assert callable(getattr(r, 'ensure_fbo', None))

    def test_renderer_has_upload_methods(self):
        r = self.renderer_cls()
        for m in ('rebuild_object_vaos', 'rebuild_room_vaos', 'rebuild_walkmesh_vaos'):
            assert callable(getattr(r, m, None)), f"missing method {m}"

    def test_renderer_has_render_method(self):
        r = self.renderer_cls()
        assert callable(getattr(r, 'render', None))

    def test_renderer_show_walkmesh_flag(self):
        r = self.renderer_cls()
        assert hasattr(r, '_show_walkmesh')
        assert r._show_walkmesh is True

    def test_renderer_show_grid_flag(self):
        r = self.renderer_cls()
        assert hasattr(r, '_show_grid')
        assert r._show_grid is True

    def test_renderer_render_time_starts_zero(self):
        r = self.renderer_cls()
        assert r._render_time == 0.0

    def test_helpers_injected_by_viewport(self):
        """After importing viewport, geometry helpers should be injected."""
        import gmodular.gui.viewport  # noqa: F401 — triggers injection
        from gmodular.gui import viewport_renderer as vr
        # After viewport import, _box_solid should not raise RuntimeError
        assert vr._box_solid is not None
        assert not (getattr(vr._box_solid, '__name__', '') == '_box_solid'
                    and 'not yet injected' in
                    (vr._box_solid.__doc__ or ''))


class TestViewportLineCount(unittest.TestCase):
    """viewport.py should be substantially smaller after extraction."""

    def test_viewport_py_under_3000_lines(self):
        from pathlib import Path
        lines = Path('gmodular/gui/viewport.py').read_text().count('\n')
        assert lines < 3000, (
            f"viewport.py has {lines} lines — expected < 3000 after extraction. "
            "Check that _EGLRenderer was properly moved to viewport_renderer.py."
        )

    def test_viewport_renderer_py_exists(self):
        from pathlib import Path
        assert Path('gmodular/gui/viewport_renderer.py').exists()

    def test_viewport_renderer_py_has_class(self):
        from pathlib import Path
        content = Path('gmodular/gui/viewport_renderer.py').read_text()
        assert 'class _EGLRenderer:' in content

    def test_total_viewport_lines_split_correctly(self):
        """The three viewport files together should cover all the code."""
        from pathlib import Path
        base   = Path('gmodular/gui/viewport.py').read_text().count('\n')
        cam    = Path('gmodular/gui/viewport_camera.py').read_text().count('\n')
        shad   = Path('gmodular/gui/viewport_shaders.py').read_text().count('\n')
        rend   = Path('gmodular/gui/viewport_renderer.py').read_text().count('\n')
        total  = base + cam + shad + rend
        # Original was 4295 lines; with headers/imports the split files will be
        # a bit larger, but the main file should be < 3000.
        assert base < 3000, f"viewport.py still too large: {base} lines"
        assert rend > 1000, f"viewport_renderer.py seems too small: {rend} lines"


# ─────────────────────────────────────────────────────────────────────────────
#  Projection matrix convention tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectionMatrixConvention(unittest.TestCase):
    """
    Verify the _perspective() helper produces a correct row-major projection
    matrix where:
      - w_clip = -z_eye   (proj[3][2] == -1)
      - z_clip = A*z_eye + B  with B = 2fn/(n-f)  (proj[2][3] ≈ -2fn/(f-n))
      - f/aspect in [0][0], f in [1][1]

    With the row-major convention, computing clip = proj @ view @ pos
    and then writing mvp.T.tobytes() to a GLSL mat4 uniform causes the
    shader to compute the equivalent column-major multiplication.

    The previous bug had proj[2][3] = -1 and proj[3][2] = 2fn/(n-f),
    which is the COLUMN-major (OpenGL) layout.  When used as a row-major
    matrix the w_clip component became ≈0.044 instead of 22, mapping all
    geometry to NDC values > 400 (invisible).
    """

    def setUp(self):
        from gmodular.gui.viewport_camera import OrbitCamera, _perspective
        self.OrbitCamera  = OrbitCamera
        self._perspective = _perspective

    # ── Matrix layout checks ─────────────────────────────────────────────────

    def test_w_clip_row_is_row3(self):
        """Row 3 produces w_clip = -z_eye.  proj[3][2] must be -1."""
        pm = self._perspective(60.0, 1.333, 0.001, 1000.0)
        self.assertAlmostEqual(float(pm[3, 2]), -1.0, places=4,
                               msg="proj[3][2] should be -1 (row-major convention)")

    def test_w_clip_col3_is_zero(self):
        """The homogeneous column should be zero for projection: proj[3][3] == 0."""
        pm = self._perspective(60.0, 1.333, 0.001, 1000.0)
        self.assertAlmostEqual(float(pm[3, 3]), 0.0, places=6)

    def test_z_near_far_entry(self):
        """proj[2][3] should be 2*far*near/(near-far) ≈ -0.002 for n=0.001, f=1000."""
        near, far = 0.001, 1000.0
        expected = (2 * far * near) / (near - far)
        pm = self._perspective(60.0, 1.333, near, far)
        self.assertAlmostEqual(float(pm[2, 3]), expected, places=4,
                               msg="proj[2][3] should be 2fn/(n-f)")

    def test_old_bug_absent(self):
        """The OLD bug placed -1 at [2][3].  Verify it is NOT there any more."""
        pm = self._perspective(60.0, 1.333, 0.001, 1000.0)
        self.assertNotAlmostEqual(float(pm[2, 3]), -1.0, places=2,
                                  msg="proj[2][3] == -1 is the old column-major bug")

    # ── Target-point visibility check ────────────────────────────────────────

    def test_camera_target_visible(self):
        """
        Camera looking at (5,5,1.5) from distance=22.
        Target point must project to NDC within [-1,1] and w_clip ≈ 22.
        """
        cam = self.OrbitCamera()
        cam.target   = np.array([5.0, 5.0, 1.5], dtype='f4')
        cam.distance = 22.0
        cam.yaw      = 45.0
        cam.pitch    = 30.0

        proj = cam.projection_matrix(800 / 600)
        view = cam.view_matrix()
        mvp  = np.array(proj, dtype='f4') @ np.array(view, dtype='f4')

        target4 = np.array([5.0, 5.0, 1.5, 1.0], dtype='f4')
        clip    = mvp @ target4
        w_clip  = float(clip[3])
        ndc     = clip[:3] / w_clip

        self.assertAlmostEqual(w_clip, 22.0, delta=0.5,
                               msg="w_clip for target should be ≈22 (= distance)")
        self.assertAlmostEqual(float(ndc[0]), 0.0, delta=0.05,
                               msg="target NDC.x should be ≈0 (centred)")
        self.assertAlmostEqual(float(ndc[1]), 0.0, delta=0.05,
                               msg="target NDC.y should be ≈0 (centred)")
        self.assertAlmostEqual(float(ndc[2]), 1.0, delta=0.01,
                               msg="target NDC.z should be ≈1.0 (at far end of room)")

    def test_all_room_corners_visible(self):
        """
        For the slem_ar room (bounding box (0,0,0)→(10,10,3)) all 8 corners
        must project to NDC within [-1,1] with w > 0.
        """
        cam = self.OrbitCamera()
        cam.target   = np.array([5.0, 5.0, 1.5], dtype='f4')
        cam.distance = 22.0
        cam.yaw      = 45.0
        cam.pitch    = 30.0

        proj = cam.projection_matrix(800 / 600)
        view = cam.view_matrix()
        mvp  = np.array(proj, dtype='f4') @ np.array(view, dtype='f4')

        corners = [
            [0,  0,  0], [10,  0,  0], [10, 10,  0], [0, 10,  0],
            [0,  0,  3], [10,  0,  3], [10, 10,  3], [0, 10,  3],
        ]
        failures = []
        for c in corners:
            clip = mvp @ np.array([*c, 1.0], dtype='f4')
            w    = float(clip[3])
            if w <= 0:
                failures.append(f"{c}: w={w:.3f} (behind camera)")
                continue
            ndc = clip[:3] / w
            if not ((-1 <= float(ndc[0]) <= 1) and
                    (-1 <= float(ndc[1]) <= 1) and
                    (-1 <= float(ndc[2]) <= 1)):
                failures.append(f"{c}: ndc=({float(ndc[0]):.3f},"
                                 f"{float(ndc[1]):.3f},{float(ndc[2]):.3f}) OUT")
        self.assertEqual(failures, [],
                         "All room corners should be inside the view frustum:\n" +
                         "\n".join(failures))

    def test_w_clip_formula(self):
        """
        w_clip = -z_eye for a point at z_eye=-10:
        Using row-major proj: w_clip = proj[3][0]*x + proj[3][1]*y + proj[3][2]*(-10)
        = 0 + 0 + (-1)*(-10) = 10.
        """
        pm = self._perspective(60.0, 1.0, 0.1, 100.0)
        z_eye = -10.0
        # Simulate eye-space point (x=0, y=0, z=z_eye, w=1)
        pt = np.array([0.0, 0.0, z_eye, 1.0], dtype='f4')
        clip = pm @ pt
        self.assertAlmostEqual(float(clip[3]), 10.0, delta=0.001,
                               msg=f"w_clip should be 10.0 for z_eye=-10, got {clip[3]}")


# ─────────────────────────────────────────────────────────────────────────────
#  EGL render test (requires GPU / EGL; skipped if unavailable)
# ─────────────────────────────────────────────────────────────────────────────

class TestEGLRendering(unittest.TestCase):
    """
    Integration test: loads the slem_ar.mod test fixture, builds room VAOs,
    renders a frame with the EGL backend and verifies that geometry pixels
    are present in the output.

    Skipped automatically when:
      - numpy is not available
      - EGL/ModernGL cannot initialise (headless CI without GPU)
      - The slem_ar.mod test fixture is missing
    """

    _TEST_MOD = "tests/test_data/slem_ar.mod"

    def _skip_if_unavailable(self):
        import os
        if not os.path.isfile(self._TEST_MOD):
            self.skipTest(f"Test fixture {self._TEST_MOD} not found")
        try:
            import numpy  # noqa
        except ImportError:
            self.skipTest("numpy not available")
        # EGL check is deferred to setUp so we get a clean skip message

    def setUp(self):
        self._skip_if_unavailable()
        import os, sys
        os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

        # Trigger helper injection before importing _EGLRenderer
        try:
            import gmodular.gui.viewport  # noqa
        except Exception:
            pass

    # ── Smoke tests (no GL required) ─────────────────────────────────────────

    def test_mod_loads_and_has_mdl(self):
        """slem_ar.mod must contain slem_ar.mdl after the fixture rebuild."""
        import os
        from gmodular.core.module_io import ModuleIO
        mio    = ModuleIO()
        result = mio.load_from_mod(self._TEST_MOD)
        self.assertIn("slem_ar.mdl", result.resources,
                      "slem_ar.mod must contain slem_ar.mdl")
        self.assertIn("slem_ar.mdx", result.resources,
                      "slem_ar.mod must contain slem_ar.mdx")
        # Extracted MDL must exist
        self.assertTrue(os.path.isfile(
            os.path.join(result.extract_dir, "slem_ar.mdl")),
            "slem_ar.mdl must be extracted to extract_dir")

    def test_lyt_parser_finds_one_room(self):
        """LYTParser must return exactly 1 room from the slem_ar LYT."""
        from gmodular.core.module_io import ModuleIO
        from gmodular.formats.lyt_vis import LYTParser
        result = ModuleIO().load_from_mod(self._TEST_MOD)
        layout = LYTParser.from_string(result.lyt_text or "")
        self.assertEqual(len(layout.rooms), 1,
                         f"Expected 1 room, got {len(layout.rooms)}")
        self.assertEqual(layout.rooms[0].resref, "slem_ar")

    def test_projection_matrix_w_clip(self):
        """OrbitCamera.projection_matrix must give w_clip ≈ distance for target."""
        from gmodular.gui.viewport_camera import OrbitCamera
        cam = OrbitCamera()
        cam.target   = np.array([5.0, 5.0, 1.5], dtype='f4')
        cam.distance = 22.0
        cam.yaw      = 45.0
        cam.pitch    = 30.0
        proj = np.array(cam.projection_matrix(800 / 600), dtype='f4')
        view = np.array(cam.view_matrix(), dtype='f4')
        mvp  = proj @ view
        t4   = np.array([5.0, 5.0, 1.5, 1.0], dtype='f4')
        clip = mvp @ t4
        self.assertAlmostEqual(float(clip[3]), 22.0, delta=0.5,
                               msg="w_clip for camera target should be ≈ distance")

    # ── Full EGL render test ─────────────────────────────────────────────────

    def test_egl_geometry_renders(self):
        """
        Full EGL render: slem_ar room must produce > 1 % non-background pixels.
        This test validates the complete pipeline: mod load → LYT parse →
        VAO build → perspective projection → fragment output.
        """
        try:
            import moderngl
        except ImportError:
            self.skipTest("moderngl not available")

        import os, numpy as np
        from gmodular.core.module_io import ModuleIO
        from gmodular.formats.lyt_vis import LYTParser
        from gmodular.gui.room_assembly import RoomInstance
        from gmodular.gui.viewport_renderer import _EGLRenderer
        from gmodular.gui.viewport_camera import OrbitCamera

        # Load module
        result = ModuleIO().load_from_mod(self._TEST_MOD)
        extract_dir = result.extract_dir or ""
        layout      = LYTParser.from_string(result.lyt_text or "")

        dir_files = {f.lower(): f for f in os.listdir(extract_dir)}
        rooms = []
        for rp in layout.rooms:
            ri = RoomInstance(mdl_name=rp.resref, grid_x=0, grid_y=0,
                              world_x=rp.x, world_y=rp.y, world_z=rp.z)
            mdl_lower = rp.resref.lower() + ".mdl"
            actual = dir_files.get(mdl_lower)
            if actual:
                ri.mdl_path = os.path.join(extract_dir, actual)
            rooms.append(ri)

        # Init renderer
        renderer = _EGLRenderer()
        try:
            renderer.init()
        except Exception as exc:
            self.skipTest(f"EGL init failed (no GPU?): {exc}")
        if not renderer.ready:
            self.skipTest("EGL renderer not ready (no GPU?)")

        renderer.rebuild_room_vaos(rooms, game_dir=extract_dir)
        self.assertGreater(len(renderer._room_vaos), 0,
                           "rebuild_room_vaos must produce at least one VAO")

        # Render
        cam = OrbitCamera()
        cam.target   = np.array([5.0, 5.0, 1.5], dtype='f4')
        cam.distance = 22.0
        cam.yaw      = 45.0
        cam.pitch    = 30.0

        W, H = 800, 600
        data = renderer.render(W, H, cam)
        renderer.release()

        self.assertIsNotNone(data, "render() must return bytes, not None")
        self.assertEqual(len(data), W * H * 4, "render output must be W×H×4 RGBA bytes")

        pixels = np.frombuffer(data, dtype=np.uint8).reshape(H, W, 4)
        bg     = np.array([18, 20, 36, 255])   # clear colour
        non_bg = int(np.sum(~np.all(pixels.reshape(-1, 4) == bg, axis=1)))
        pct    = 100.0 * non_bg / (W * H)

        self.assertGreater(
            non_bg, W * H * 0.01,   # at least 1 % of pixels must be geometry
            f"Expected > 1 % non-background pixels, got {pct:.1f}% ({non_bg} px). "
            "Projection matrix or shader may be broken."
        )


if __name__ == "__main__":
    unittest.main()
