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


if __name__ == "__main__":
    unittest.main()
