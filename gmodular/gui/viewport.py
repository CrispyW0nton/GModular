"""
GModular — 3D Viewport Widget
==============================
Pure-QWidget + ModernGL EGL offscreen rendering.

Architecture
------------
Instead of QOpenGLWidget (which requires an X11/Wayland display to create
a real GL context), we use:

  1. moderngl.create_standalone_context(backend='egl')
     — creates an EGL surfaceless context using Mesa's llvmpipe software
       rasterizer.  Works on any Linux machine including headless CI/CD
       servers and sandboxes with no GPU.

  2. Render everything into an FBO (framebuffer object) at widget size.

  3. Read the FBO pixels → QImage → draw with QPainter in paintEvent.
     This blits the rendered frame onto the screen at native resolution.

No GhostRigger, GhostScripter or any external tool is required.
The viewport is fully self-contained.

Features
--------
- Orbit camera (RMB drag), pan (MMB drag), zoom (scroll)
- WASD editor camera fly-through
- Ground grid (Z=0 plane)
- GIT object proxy boxes (placeables, creatures, doors, waypoints, …)
- Room MDL geometry — rendered as soon as rooms are placed on the Room Grid
  (falls back to a coloured placeholder box if no .mdl file is found)
- Object selection via raycasting
- Object placement (left-click on ground)
- Transform gizmo (2-D overlay) — XYZ translate + Z rotate
- Snap (Ctrl / Shift / Ctrl+Shift)
- First-person play preview mode
- 2-D fallback painter when GL is completely unavailable
- Coordinate system: Z-up right-handed (matches KotOR / Odyssey engine)
"""
from __future__ import annotations

import math
import os
import time
import ctypes
import logging
from typing import Optional, List, Tuple, Dict

log = logging.getLogger(__name__)

# ─── numpy ────────────────────────────────────────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None  # type: ignore

# ─── Qt ───────────────────────────────────────────────────────────────────────
try:
    from qtpy.QtWidgets import QWidget, QSizePolicy
    from qtpy.QtCore import Qt, QTimer, Signal, QPoint
    from qtpy.QtGui import (
        QKeyEvent, QMouseEvent, QWheelEvent,
        QPainter, QPen, QBrush, QColor, QFont,
        QPolygon, QCursor, QImage,
    )
    _HAS_QT = True
    _QWidget_base = QWidget
except ImportError:
    _HAS_QT = False
    _QWidget_base = object  # type: ignore
    QColor = None  # type: ignore
    class Signal:  # type: ignore
        def __init__(self, *a, **kw): pass
        def __set_name__(self, o, n): pass

# ─── ModernGL / EGL ───────────────────────────────────────────────────────────
_HAS_MODERNGL = False
_GL_INIT_ERROR = ""
_GL_BACKEND = "none"   # 'egl' | 'default' | 'none'


def _bootstrap_gl_linux():
    """
    Pre-load libGL and libEGL into the process on Linux.
    Searches common system library paths (both x86_64 and aarch64).
    Silent on Windows/macOS — those platforms don't need this step.
    """
    if os.name != "posix":
        return  # Windows / macOS — not needed
    import glob
    search_patterns = [
        "/usr/lib/x86_64-linux-gnu/libGL.so*",
        "/usr/lib/aarch64-linux-gnu/libGL.so*",
        "/usr/lib/libGL.so*",
        "/usr/local/lib/libGL.so*",
    ]
    egl_patterns = [
        "/usr/lib/x86_64-linux-gnu/libEGL.so*",
        "/usr/lib/aarch64-linux-gnu/libEGL.so*",
        "/usr/lib/libEGL.so*",
    ]
    for patterns, tag in [(search_patterns, "libGL"), (egl_patterns, "libEGL")]:
        loaded = False
        for pat in patterns:
            for path in sorted(glob.glob(pat), reverse=True):
                try:
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                    log.debug(f"Pre-loaded {path}")
                    loaded = True
                    break
                except OSError:
                    pass
            if loaded:
                break


def _init_moderngl():
    """Import moderngl and set _HAS_MODERNGL."""
    global _HAS_MODERNGL, _GL_INIT_ERROR
    try:
        import moderngl as _mgl  # noqa: F401
        _HAS_MODERNGL = True
    except ImportError as exc:
        _GL_INIT_ERROR = f"moderngl not installed: {exc}"
        log.warning(_GL_INIT_ERROR)


# Bootstrap on import
_bootstrap_gl_linux()
_init_moderngl()

# ─── Other GModular imports (graceful) ────────────────────────────────────────
try:
    from ..formats.gff_types import Vector3
    from ..core.module_state import get_module_state
except ImportError:
    pass

try:
    # New engine systems (animation, scene graph, entity system, play mode)
    from ..engine.play_mode import PlaySession, PlayModeController, CameraMode
    from ..engine.animation_system import AnimationSet, get_default_idle_animation
    from ..engine.scene_manager import SceneGraph, SceneRoom, SceneEntity, Frustum, AABB
    from ..engine.entity_system import EntityRegistry, Door3D, Creature3D, Placeable3D
    _HAS_ENGINE = True
    _HAS_NEW_ENGINE = True
except ImportError:
    _HAS_ENGINE = False
    _HAS_NEW_ENGINE = False
    try:
        # Legacy fallback
        from ..engine.player_controller import PlaySession
        from ..engine.npc_instance import NPCRegistry
        _HAS_ENGINE = True
    except ImportError:
        pass

try:
    from ..formats.mdl_parser import get_model_cache
    _HAS_MDL = True
except ImportError:
    _HAS_MDL = False

# ─── Sub-modules extracted from this file ────────────────────────────────────
# OrbitCamera and GLSL shaders live in their own modules for testability.
# viewport.py re-exports them so that existing ``from .viewport import …``
# callsites continue to work without modification.
try:
    from .viewport_camera import OrbitCamera, _look_at as _look_at_cam, _perspective as _perspective_cam  # noqa: F401
    from .viewport_shaders import (  # noqa: F401
        _VERT_FLAT, _FRAG_FLAT,
        _VERT_LIT, _FRAG_LIT,
        _VERT_LIT_NO_UV, _FRAG_LIT_NO_UV,
        _VERT_UNIFORM, _FRAG_UNIFORM,
        _VERT_OUTLINE, _FRAG_OUTLINE,
        _VERT_PICKER, _FRAG_PICKER, _VERT_PICK, _FRAG_PICK,
        _VERT_TEXTURED, _FRAG_TEXTURED,
        _VERT_SKINNED, _FRAG_SKINNED,
        ALL_SHADERS,
    )
    from .viewport_renderer import _EGLRenderer, _inject_helpers  # noqa: F401
    _SUBMODULES_LOADED = True
except ImportError:
    _SUBMODULES_LOADED = False


# ═════════════════════════════════════════════════════════════════════════════
#  Math helpers
# ═════════════════════════════════════════════════════════════════════════════

def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> "np.ndarray":
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    d = near - far
    return np.array([
        [f / aspect, 0,  0,  0],
        [0,          f,  0,  0],
        [0,          0,  (far + near) / d, (2 * far * near) / d],
        [0,          0, -1,  0],
    ], dtype='f4')


def _look_at(eye: "np.ndarray", target: "np.ndarray",
             up: "np.ndarray") -> "np.ndarray":
    """Standard view matrix (row-major).  Write .T.tobytes() to GL uniforms."""
    f = target - eye
    f_len = np.linalg.norm(f)
    f = f / f_len if f_len > 1e-9 else np.array([0., 1., 0.], dtype='f4')
    r = np.cross(f, up)
    r_len = np.linalg.norm(r)
    r = r / r_len if r_len > 1e-9 else np.array([1., 0., 0.], dtype='f4')
    u = np.cross(r, f)
    return np.array([
        [ r[0],  r[1],  r[2], -np.dot(r, eye)],
        [ u[0],  u[1],  u[2], -np.dot(u, eye)],
        [-f[0], -f[1], -f[2],  np.dot(f, eye)],
        [ 0.,    0.,    0.,    1.            ],
    ], dtype='f4')


def _translation(tx: float, ty: float, tz: float) -> "np.ndarray":
    m = np.eye(4, dtype='f4')
    m[0, 3] = tx; m[1, 3] = ty; m[2, 3] = tz
    return m


# ═════════════════════════════════════════════════════════════════════════════
#  Orbit Camera
# ═════════════════════════════════════════════════════════════════════════════

class OrbitCamera:
    """
    Maya-style orbit camera (Z-up, right-handed — matches KotOR / Odyssey).

    Aligned with Kotor.NET OrbitCamera.cs (Graphics/Cameras/OrbitCamera.cs):
      Yaw   = azimuth  angle in degrees around Z-axis
      Pitch = elevation angle in degrees (clamped -85..85, like Kotor.NET -1.55..1.55 rad)
      FOV   = field of view in degrees (default 60° ≈ π/3 rad)
      Near  = 0.001 (matches Kotor.NET OrbitCamera.Near = 0.001f)
      Far   = 1000.0 (matches Kotor.NET OrbitCamera.Far = 1000.0f)
    """

    def __init__(self):
        # Primary attributes (Kotor.NET naming)
        self.yaw       = 45.0    # azimuth in degrees (Kotor.NET: Yaw in radians)
        self.pitch     = 30.0    # elevation in degrees (Kotor.NET: Pitch in radians)
        self.distance  = 15.0   # Kotor.NET: Distance
        self.target    = np.array([0., 0., 0.], dtype='f4')  # Kotor.NET: Target
        self.fov       = 60.0   # degrees (Kotor.NET: FOV = Math.PI/3 = 60°)
        self.near      = 0.001  # Kotor.NET: Near = 0.001f (very close near plane)
        self.far       = 1000.0 # Kotor.NET: Far = 1000.0f

    # Backward-compatible aliases for legacy code
    @property
    def azimuth(self) -> float:
        return self.yaw

    @azimuth.setter
    def azimuth(self, v: float):
        self.yaw = v

    @property
    def elevation(self) -> float:
        return self.pitch

    @elevation.setter
    def elevation(self, v: float):
        self.pitch = v

    @property
    def _near(self) -> float:
        return self.near

    @_near.setter
    def _near(self, v: float):
        self.near = v

    @property
    def _far(self) -> float:
        return self.far

    @_far.setter
    def _far(self, v: float):
        self.far = v

    def eye(self) -> "np.ndarray":
        """
        Compute camera eye position from Yaw/Pitch/Distance/Target.

        Matches Kotor.NET OrbitCamera.GetViewTransform():
          cosPitch = cos(Pitch), sinPitch = sin(Pitch)
          cosYaw   = cos(Yaw),   sinYaw   = sin(Yaw)
          x = Distance * cosPitch * cosYaw
          y = Distance * cosPitch * sinYaw
          z = Distance * sinPitch
        """
        yaw = math.radians(self.yaw)
        pit = math.radians(self.pitch)
        cos_p = math.cos(pit)
        x = self.distance * cos_p * math.cos(yaw)
        y = self.distance * cos_p * math.sin(yaw)
        z = self.distance * math.sin(pit)
        return self.target + np.array([x, y, z], dtype='f4')

    def view_matrix(self) -> "np.ndarray":
        """View matrix — LookAt(eye, target, Z-up). Matches Kotor.NET GetViewTransform."""
        return _look_at(self.eye(), self.target,
                        np.array([0., 0., 1.], dtype='f4'))

    def projection_matrix(self, aspect: float) -> "np.ndarray":
        """Perspective projection. Matches Kotor.NET GetProjectionTransform."""
        return _perspective(self.fov, aspect, self.near, self.far)

    def orbit(self, d_az: float, d_el: float):
        """Orbit by (d_az, d_el) degrees. Pitch clamped to -85..85°."""
        self.yaw   = (self.yaw + d_az) % 360.
        self.pitch = max(-85., min(85., self.pitch + d_el))

    def zoom(self, delta: float):
        """Exponential zoom — feels natural at all distances."""
        factor = 0.88 if delta > 0 else (1.0 / 0.88)
        self.distance = max(0.5, min(5000.0, self.distance * (factor ** abs(delta))))

    def pan(self, dx: float, dy: float):
        """Pan camera target in screen-right + screen-up plane."""
        az    = math.radians(self.yaw)
        # Screen-right vector (perpendicular to view, horizontal)
        right = np.array([-math.sin(az), math.cos(az), 0.], dtype='f4')
        fwd = self.target - self.eye()
        fwd_len = np.linalg.norm(fwd)
        if fwd_len < 1e-9:
            return
        fwd /= fwd_len
        up = np.cross(right, fwd)
        up_len = np.linalg.norm(up)
        if up_len < 1e-9:
            up = np.array([0., 0., 1.], dtype='f4')
        else:
            up /= up_len
        # Scale pan by distance so it feels consistent at all zoom levels
        scale = self.distance * 0.0015
        self.target += right * dx * scale
        self.target -= up    * dy * scale

    def walk(self, forward: float, right: float, up: float):
        """
        Fly-through movement — moves both eye and target together.
        Used by WASD keys in free-fly mode.
        forward/right/up: movement amounts in world units.
        """
        az  = math.radians(self.yaw)
        pit = math.radians(self.pitch)
        # Forward direction (pitch-aware for fly-through mode)
        fwd_h = np.array([math.cos(az), math.sin(az), 0.], dtype='f4')
        rt    = np.array([-math.sin(az), math.cos(az), 0.], dtype='f4')
        up_v  = np.array([0., 0., 1.], dtype='f4')
        # For fly-through, include pitch component in forward
        fwd_3d = np.array([
            math.cos(pit) * math.cos(az),
            math.cos(pit) * math.sin(az),
            math.sin(pit)
        ], dtype='f4')
        delta = fwd_3d * forward + rt * right + up_v * up
        self.target += delta

    def frame(self, center: "np.ndarray", radius: float):
        """Frame camera to show a sphere of given center and radius."""
        self.target = center.copy()
        self.distance = max(1., radius * 2.5)
        # Extend far plane to ensure geometry at this distance is visible
        self.far = max(1000.0, self.distance * 4.0 + radius * 2.0)
        # Also set near plane to reasonable value relative to scene size
        self.near = max(0.001, radius * 0.001)

    def ray_from_screen(self, sx: int, sy: int, W: int, H: int
                        ) -> Tuple["np.ndarray", "np.ndarray"]:
        aspect = W / max(H, 1)
        nx = (2. * sx / W) - 1.
        ny = 1. - (2. * sy / H)
        f  = math.tan(math.radians(self.fov) * 0.5)
        eye = self.eye()
        fwd = self.target - eye
        fwd /= np.linalg.norm(fwd)
        up  = np.array([0., 0., 1.], dtype='f4')
        right = np.cross(fwd, up)
        right /= np.linalg.norm(right)
        up2  = np.cross(right, fwd)
        dir_ = fwd + right * nx * f * aspect + up2 * ny * f
        dir_ /= np.linalg.norm(dir_)
        return eye, dir_


# ═════════════════════════════════════════════════════════════════════════════
#  GLSL Shaders  (upgraded: Phong+UV+texture, object picker, lightmap blend)
#  Deep-dive reference: Kotor.NET Assets/standard/*.glsl + picker/*.glsl
# ═════════════════════════════════════════════════════════════════════════════

# ── Flat colour-per-vertex (grid, wireframe boxes) ────────────────────────
_VERT_FLAT = """
#version 330 core
in vec3 in_position;
in vec3 in_color;
out vec3 v_color;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
    v_color = in_color;
}
"""

_FRAG_FLAT = """
#version 330 core
in vec3 v_color;
out vec4 fragColor;
void main() { fragColor = vec4(v_color, 1.0); }
"""

# ── Phong-lit with UV texture support ─────────────────────────────────────
# Based on PyKotor KOTOR_VSHADER / KOTOR_FSHADER + Kotor.NET standard.glsl
# Features:
#   - Proper normal transform (normal matrix from model)
#   - Multi-light Blinn-Phong (key + fill + rim + back + spec)
#   - Dual UV channels: diffuse UV + optional lightmap UV
#   - Alpha discard for punch-through transparency
_VERT_LIT = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
out vec3 v_normal;
out vec3 v_world_pos;
out vec2 v_uv;
uniform mat4 mvp;
uniform mat4 model;
void main() {
    vec4 world = model * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    // Correct normal transform using normal matrix
    mat3 normal_mat = transpose(inverse(mat3(model)));
    v_normal    = normalize(normal_mat * in_normal);
    v_uv        = in_uv;
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_LIT = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
in vec2 v_uv;
out vec4 fragColor;
uniform vec3  diffuse_color;
uniform vec3  light_dir;
uniform float ambient;
uniform float alpha;
uniform bool  has_texture;
uniform sampler2D tex0;
void main() {
    if (has_texture) {
        // Texture passthrough: Kotor.NET style — output texture directly
        vec4 tex_sample = texture(tex0, v_uv);
        if (tex_sample.a < 0.05) discard;
        fragColor = vec4(tex_sample.rgb, tex_sample.a * alpha);
    } else {
        // No texture: apply Phong to diffuse_color
        vec3 n = normalize(v_normal);
        vec3 key       = normalize(light_dir);
        float NdL_key  = max(dot(n, key), 0.0);
        vec3 fill      = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.4));
        float NdL_fill = max(dot(n, fill), 0.0) * 0.30;
        float NdL_rim  = max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0) * 0.10;
        float back     = max(dot(-n, key), 0.0) * 0.12;
        vec3 view_dir  = normalize(-v_world_pos);
        vec3 half_vec  = normalize(key + view_dir);
        float spec     = pow(max(dot(n, half_vec), 0.0), 48.0) * 0.04;
        float light_total = ambient + (NdL_key + NdL_fill + NdL_rim + back) * (1.0 - ambient);
        float ao       = 0.88 + 0.12 * abs(n.z);
        vec3 col       = diffuse_color * light_total * ao + vec3(spec);
        fragColor      = vec4(clamp(col, 0.0, 1.0), alpha);
    }
}
"""

# ── Lit with no UV (positions + normals only — for nodes without UVs) ─────
_VERT_LIT_NO_UV = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
out vec3 v_normal;
out vec3 v_world_pos;
uniform mat4 mvp;
uniform mat4 model;
void main() {
    vec4 world = model * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    v_normal    = normalize(mat3(transpose(inverse(model))) * in_normal);
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_LIT_NO_UV = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
out vec4 fragColor;
uniform vec3  diffuse_color;
uniform vec3  light_dir;
uniform float ambient;
uniform float alpha;
void main() {
    vec3 n = normalize(v_normal);
    vec3 key       = normalize(light_dir);
    float NdL_key  = max(dot(n, key), 0.0);
    vec3 fill      = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.4));
    float NdL_fill = max(dot(n, fill), 0.0) * 0.30;
    float NdL_rim  = max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0) * 0.10;
    float back     = max(dot(-n, key), 0.0) * 0.12;
    vec3 view_dir  = normalize(-v_world_pos);
    vec3 half_vec  = normalize(key + view_dir);
    float spec     = pow(max(dot(n, half_vec), 0.0), 48.0) * 0.04;
    float light_total = ambient + (NdL_key + NdL_fill + NdL_rim + back) * (1.0 - ambient);
    float ao       = 0.88 + 0.12 * abs(n.z);
    vec3 col       = diffuse_color * light_total * ao + vec3(spec);
    fragColor      = vec4(clamp(col, 0.0, 1.0), alpha);
}
"""

# ── Uniform-colour + alpha (walkmesh fill, selection overlay) ─────────────
_VERT_UNIFORM = """
#version 330 core
in vec3 in_position;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_UNIFORM = """
#version 330 core
out vec4 fragColor;
uniform vec4 u_color;
void main() { fragColor = u_color; }
"""

# ── Selection highlight / outline shader ─────────────────────────────────
# Renders a screen-space outline effect around selected objects via
# an additive pulsing glow (matches UE5 selection highlight style).
_VERT_OUTLINE = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
uniform mat4 mvp;
uniform float outline_scale;  // expand along normals for silhouette
void main() {
    vec3 offset = in_position + in_normal * outline_scale;
    gl_Position = mvp * vec4(offset, 1.0);
}
"""

_FRAG_OUTLINE = """
#version 330 core
out vec4 fragColor;
uniform vec4 outline_color;
uniform float time;
void main() {
    // Subtle pulse (0.6 to 1.0 alpha) for UE5-style selection glow
    float pulse = 0.6 + 0.4 * abs(sin(time * 2.5));
    fragColor = vec4(outline_color.rgb, outline_color.a * pulse);
}
"""

# ── Object ID picker (matches Kotor.NET picker/fragment.glsl) ─────────────
# Encodes entity ID into RGBA bytes for GPU readback picking.
_VERT_PICKER = """
#version 330 core
in vec3 in_position;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_PICKER = """
#version 330 core
out vec4 fragColor;
uniform uint entity_id;
void main() {
    // Kotor.NET intToColor: MSB→R, LSB→A (big-endian RGBA encoding)
    float r = float((entity_id >> 24u) & 0xFFu) / 255.0;
    float g = float((entity_id >> 16u) & 0xFFu) / 255.0;
    float b = float((entity_id >> 8u)  & 0xFFu) / 255.0;
    float a = float( entity_id         & 0xFFu)  / 255.0;
    fragColor = vec4(r, g, b, a);
}
"""

# Aliases for backward-compat and tests (canonical names are _VERT_PICKER / _FRAG_PICKER)
_VERT_PICK = _VERT_PICKER
_FRAG_PICK = _FRAG_PICKER

# ── Textured mesh shader (dual-sampler: albedo tex0 + optional lightmap tex1) ───────
# Architecture: Kotor.NET standard.glsl approach
#   - Vertex shader: separate entity + mesh matrices (like Kotor.NET entity/mesh uniforms)
#   - Fragment shader: pure texture passthrough with optional lightmap modulation
#   - When texture present: output texture directly (Kotor.NET: FragColor = diffuseColor)
#   - When lightmap present: modulate albedo by lightmap (baked lighting = realism)
#   - Minimal Phong only applied when NO texture (fallback for untextured meshes)
_VERT_TEXTURED = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
in vec2 in_uv2;
out vec3 v_normal;
out vec3 v_world_pos;
out vec2 v_uv;
out vec2 v_uv2;
uniform mat4 mvp;
uniform mat4 model;
void main() {
    vec4 world = model * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    mat3 normal_mat = transpose(inverse(mat3(model)));
    v_normal    = normalize(normal_mat * in_normal);
    v_uv        = in_uv;
    v_uv2       = in_uv2;
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_TEXTURED = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
in vec2 v_uv;
in vec2 v_uv2;
out vec4 fragColor;
uniform sampler2D tex0;        // albedo / diffuse texture
uniform sampler2D tex1;        // lightmap texture
uniform int  use_texture;      // 1 = sample tex0, 0 = use diffuse_color
uniform int  use_lightmap;     // 1 = multiply by lightmap baked light
uniform vec3 diffuse_color;
uniform vec3 light_dir;
uniform float ambient;
uniform float u_alpha;
void main() {
    vec4 albedo;
    if (use_texture == 1) {
        // Kotor.NET approach: pure texture passthrough — FragColor = texture(texture1, texCoord1)
        albedo = texture(tex0, v_uv);
        if (albedo.a < 0.05) discard;
        // Apply lightmap modulation when available (baked lighting)
        if (use_lightmap == 1) {
            vec3 lm = texture(tex1, v_uv2).rgb;
            // KotOR lightmap formula: lm * 1.8 + 0.2 to avoid pure black areas
            albedo.rgb = albedo.rgb * clamp(lm * 1.8 + 0.2, 0.0, 1.5);
        }
        // No Phong dimming on textured + lit meshes — texture IS the full colour
        // This matches exactly what Kotor.NET does: just output the texture
        fragColor = vec4(clamp(albedo.rgb, 0.0, 1.0), albedo.a * u_alpha);
    } else {
        // Untextured mesh: apply Phong lighting to diffuse_color
        vec3 n = normalize(v_normal);
        vec3 key  = normalize(light_dir);
        float NdL = max(dot(n, key), 0.0);
        vec3 fill = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.4));
        float NdF = max(dot(n, fill), 0.0) * 0.28;
        float NdR = max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0) * 0.08;
        float back = max(dot(-n, key), 0.0) * 0.10;
        float light = ambient + (NdL + NdF + NdR + back) * (1.0 - ambient);
        fragColor = vec4(clamp(diffuse_color * light, 0.0, 1.0), u_alpha);
    }
}
"""

# ── Skinned mesh vertex shader (bone matrix palette — Kotor.NET SkinmeshNode) ──────────────
# Supports up to 16 bone matrices (matching Kotor.NET MDLBinarySkinmeshHeader ushort[16]).
# Blend weight: 4 weights per vertex.
_VERT_SKINNED = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in vec2 in_uv;
in vec4 in_bone_weights;
in ivec4 in_bone_indices;
out vec3 v_normal;
out vec3 v_world_pos;
out vec2 v_uv;
uniform mat4 mvp;
uniform mat4 model;
uniform mat4 bone_matrices[16];
void main() {
    // Weighted bone transform
    mat4 skin = mat4(0.0);
    skin += in_bone_weights.x * bone_matrices[in_bone_indices.x];
    skin += in_bone_weights.y * bone_matrices[in_bone_indices.y];
    skin += in_bone_weights.z * bone_matrices[in_bone_indices.z];
    skin += in_bone_weights.w * bone_matrices[in_bone_indices.w];
    vec4 world = model * skin * vec4(in_position, 1.0);
    v_world_pos = world.xyz;
    v_normal    = normalize(mat3(model) * mat3(skin) * in_normal);
    v_uv        = in_uv;
    gl_Position = mvp * vec4((skin * vec4(in_position, 1.0)).xyz, 1.0);
}
"""

# The skinned fragment shader reuses the same KotOR two-light Phong as _FRAG_TEXTURED
# but without lightmap (too expensive to skin lightmap UVs without engine support).
_FRAG_SKINNED = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D tex0;
uniform int  use_texture;
uniform vec3 diffuse_color;
uniform vec3 light_dir;
uniform float ambient;
uniform float u_alpha;
void main() {
    vec3 n = normalize(v_normal);
    vec3 key  = normalize(light_dir);
    float NdL = max(dot(n, key), 0.0);
    vec3 fill = normalize(vec3(-key.x * 0.5, -key.y * 0.5, 0.35));
    float NdF = max(dot(n, fill), 0.0) * 0.22;
    float light = ambient + (NdL + NdF) * (1.0 - ambient);
    vec4 albedo;
    if (use_texture == 1) {
        albedo = texture(tex0, v_uv);
        if (albedo.a < 0.05) discard;
    } else {
        albedo = vec4(diffuse_color, 1.0);
    }
    fragColor = vec4(albedo.rgb * light, albedo.a * u_alpha);
}
"""

# ═════════════════════════════════════════════════════════════════════════════
#  Geometry helpers
# ═════════════════════════════════════════════════════════════════════════════

_COLOR_PLACEABLE = (0.2, 0.6, 1.0)
_COLOR_CREATURE  = (1.0, 0.4, 0.2)
_COLOR_DOOR      = (0.8, 0.7, 0.1)
_COLOR_TRIGGER   = (0.2, 1.0, 0.5)
_COLOR_WAYPOINT  = (0.8, 0.2, 0.8)
_COLOR_SOUND     = (0.2, 0.9, 0.9)
_COLOR_STORE     = (0.2, 0.9, 0.3)
_COLOR_SELECTED  = (1.0, 1.0, 0.0)


def _box_solid(cx, cy, cz, hw, hh, hd, color) -> "np.ndarray":
    """Filled box: 6 faces × 2 tri × 3 verts × 6 floats (xyz rgb)."""
    r, g, b = color
    x0, x1 = cx - hw, cx + hw
    y0, y1 = cy - hh, cy + hh
    z0, z1 = cz, cz + hd * 2
    faces = [
        (x0,y0,z0, x1,y0,z0, x1,y1,z0, x0,y0,z0, x1,y1,z0, x0,y1,z0),
        (x0,y0,z1, x1,y1,z1, x1,y0,z1, x0,y0,z1, x0,y1,z1, x1,y1,z1),
        (x0,y0,z0, x1,y0,z1, x1,y0,z0, x0,y0,z0, x0,y0,z1, x1,y0,z1),
        (x0,y1,z0, x1,y1,z0, x1,y1,z1, x0,y1,z0, x1,y1,z1, x0,y1,z1),
        (x0,y0,z0, x0,y1,z0, x0,y1,z1, x0,y0,z0, x0,y1,z1, x0,y0,z1),
        (x1,y0,z0, x1,y1,z1, x1,y1,z0, x1,y0,z0, x1,y0,z1, x1,y1,z1),
    ]
    v = []
    for face in faces:
        c = list(face)
        for i in range(0, len(c), 3):
            v.extend([c[i], c[i+1], c[i+2], r, g, b])
    return np.array(v, dtype='f4')


def _box_wire(cx, cy, cz, hw, hh, hd, color) -> "np.ndarray":
    """Wireframe box (12 edges × 2 verts × 6 floats)."""
    r, g, b = color
    xs = [cx - hw, cx + hw]
    ys = [cy - hh, cy + hh]
    zs = [cz, cz + hd * 2]
    corners = [
        (xs[0],ys[0],zs[0]),(xs[1],ys[0],zs[0]),
        (xs[1],ys[1],zs[0]),(xs[0],ys[1],zs[0]),
        (xs[0],ys[0],zs[1]),(xs[1],ys[0],zs[1]),
        (xs[1],ys[1],zs[1]),(xs[0],ys[1],zs[1]),
    ]
    edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),
             (0,4),(1,5),(2,6),(3,7)]
    v = []
    for a, b_ in edges:
        for idx in (a, b_):
            v.extend([*corners[idx], r, g, b])
    return np.array(v, dtype='f4')


# Backwards-compatible aliases (used by tests and older code)
_box_verts       = _box_wire   # wireframe variant
_box_verts_solid = _box_solid  # solid-filled variant


def _grid_verts(n: int = 25, step: float = 1.0) -> "np.ndarray":
    """
    N×N ground grid — UE5-style with major/minor lines and axis highlight.
    Slightly below Z=0 to avoid z-fighting with floor meshes.
    """
    v = []
    half = n * step * 0.5
    Z = -0.002   # push below floor to avoid z-fighting
    for i in range(-n, n + 1):
        x = i * step
        # UE5-style grid: major every 10, medium every 5, minor otherwise
        if i == 0:
            continue  # skip origin line — drawn separately as axis
        elif i % 10 == 0:
            br = 0.38   # major grid lines
        elif i % 5 == 0:
            br = 0.25   # medium grid lines
        else:
            br = 0.12   # minor grid lines
        v.extend([-half, x, Z, br, br, br + .05,
                   half, x, Z, br, br, br + .05])
        v.extend([x, -half, Z, br, br, br + .05,
                   x,  half, Z, br, br, br + .05])
    # World axis lines — X=red, Y=green, Z=blue (UE5 convention)
    ext = half
    # X axis (red) — extends full grid width
    v.extend([-ext, 0, Z, 0.70, 0.18, 0.18,
               ext, 0, Z, 0.70, 0.18, 0.18])
    # Y axis (green) — extends full grid height
    v.extend([0, -ext, Z, 0.18, 0.70, 0.18,
               0,  ext, Z, 0.18, 0.70, 0.18])
    return np.array(v, dtype='f4')


# ─── Inject geometry helpers into viewport_renderer ───────────────────────────
# Must happen AFTER _box_solid/_box_wire/_grid_verts are defined above.
if _SUBMODULES_LOADED:
    try:
        _inject_helpers(_grid_verts, _box_solid, _box_wire, _translation)
    except Exception as _e:
        log.debug("viewport: helper injection failed: %s", _e)


# ═════════════════════════════════════════════════════════════════════════════
#  Gizmo constants
# ═════════════════════════════════════════════════════════════════════════════

SNAP_UNIT, SNAP_HALF, SNAP_FINE = 1.0, 0.5, 0.25
GIZMO_LEN, GIZMO_HEAD, GIZMO_HIT = 72, 10, 14
_AX_X, _AX_Y, _AX_Z, _AX_R = 0, 1, 2, 3

if _HAS_QT:
    _GIZMO_X_COL = QColor(230,  60,  60)
    _GIZMO_Y_COL = QColor( 60, 200,  60)
    _GIZMO_Z_COL = QColor( 60, 120, 230)
    _GIZMO_R_COL = QColor(220, 220,  50)
    _GIZMO_ACT   = QColor(255, 255, 100)
else:
    _GIZMO_X_COL = _GIZMO_Y_COL = _GIZMO_Z_COL = _GIZMO_R_COL = _GIZMO_ACT = None


# ═════════════════════════════════════════════════════════════════════════════
#  EGL Renderer (offscreen moderngl)
# ═════════════════════════════════════════════════════════════════════════════
#  EGL Renderer — defined in viewport_renderer.py, re-exported here
# ═════════════════════════════════════════════════════════════════════════════
#  _EGLRenderer is imported at module top via:
#    from .viewport_renderer import _EGLRenderer, _inject_helpers
#  The class body has been moved to viewport_renderer.py (v2.0.7).


def _expand_mdl_node(node) -> tuple:
    """Indexed→per-triangle expand for _upload_textured_mesh.

    Returns (positions, normals, uvs, uvs2) or 4 empty lists.
    Ref: rebuild_room_vaos; Eberly §1 mesh representation.
    """
    verts_raw = node.vertices or []
    faces_raw = node.faces    or []
    if not verts_raw or not faces_raw:
        return [], [], [], []
    norms_raw = node.normals or []
    uvs_raw   = getattr(node, 'uvs',  []) or []
    uvs2_raw  = getattr(node, 'uvs2', []) or []
    nv = len(verts_raw)
    has_n, has_uv, has_uv2 = len(norms_raw)==nv, len(uvs_raw)==nv, len(uvs2_raw)==nv
    pos, nrm, uv, uv2 = [], [], [], []
    for f in faces_raw:
        if len(f) < 3: continue
        a, b, c = int(f[0]), int(f[1]), int(f[2])
        if a >= nv or b >= nv or c >= nv: continue
        for vi in (a, b, c):
            pos.append(verts_raw[vi])
            if has_uv:  uv.append(uvs_raw[vi])
            if has_uv2: uv2.append(uvs2_raw[vi])
            if has_n:
                nrm.append(norms_raw[vi])
            else:
                v0=verts_raw[a]; v1=verts_raw[b]; v2=verts_raw[c]
                ex=v1[0]-v0[0]; ey=v1[1]-v0[1]; ez=v1[2]-v0[2]
                fx=v2[0]-v0[0]; fy=v2[1]-v0[1]; fz=v2[2]-v0[2]
                nx_=ey*fz-ez*fy; ny_=ez*fx-ex*fz; nz_=ex*fy-ey*fx
                m=(nx_*nx_+ny_*ny_+nz_*nz_)**0.5 or 1.0
                nrm.append((nx_/m, ny_/m, nz_/m))
    return pos, nrm, uv, uv2


class ViewportWidget(_QWidget_base):
    """
    ModernGL-powered 3D viewport.

    Uses an EGL offscreen context (no display server required) and blits
    the rendered frame to the Qt widget via QPainter + QImage.
    Standalone — no external tools required.
    """

    # Signals
    object_selected       = Signal(object)
    object_placed         = Signal(object)
    camera_moved          = Signal(float, float, float)
    play_mode_changed     = Signal(bool)
    # Emitted every frame with (delta_seconds,) — animation panel subscribes
    # to this instead of using a blind 50 ms poll timer.
    frame_advanced        = Signal(float)
    # Emitted when the user clicks a walkmesh face in walkmesh-edit mode.
    # Carries (face_index: int, t: float) — face index into walk_tris list and
    # the ray-intersection distance.  Connect to a face-paint panel to let the
    # modder change the surface material of the selected face.
    # Reference: Ericson §5.3.6 (Möller-Trumbore) + Phase 2.1 roadmap.
    walkmesh_face_selected = Signal(int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        if _HAS_QT:
            self.setFocusPolicy(Qt.StrongFocus)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMinimumSize(400, 300)

        self.camera = OrbitCamera()
        self._renderer = _EGLRenderer()
        self._last_frame: Optional[QImage] = None

        # Interaction state
        self._last_mouse: Optional[QPoint] = None
        self._keys: set = set()
        self._placement_mode = False
        self._selected_obj   = None
        self._place_template: Optional[str] = None
        self._place_asset_type: str = "placeable"

        # App mode: 'module_editor' or 'level_builder'
        self._app_mode: str = "level_builder"

        # Walkmesh visibility
        self._show_walkmesh: bool = True
        self._walkmesh_loaded: bool = False

        # ── New Engine Systems ────────────────────────────────────────────────
        # Scene graph (rooms + entities with spatial culling)
        self._scene_graph: Optional[SceneGraph] = (
            SceneGraph() if _HAS_NEW_ENGINE else None
        )
        # Entity registry (Door3D, Creature3D, Placeable3D instances)
        self._entity_registry: Optional[EntityRegistry] = (
            EntityRegistry() if _HAS_NEW_ENGINE else None
        )
        # Animation set (per-entity animation players)
        self._anim_set: Optional[AnimationSet] = (
            AnimationSet() if _HAS_NEW_ENGINE else None
        )
        # Frame timer for delta time
        self._last_engine_time: float = time.time()
        self._engine_delta: float = 0.016
        # Scene stats overlay
        self._show_stats: bool = False

        # Play mode (new PlaySession using PlayModeController)
        self._play_mode      = False
        self._play_session: Optional[PlaySession] = None
        self._play_last_time = 0.0
        self._play_pitch     = 0.0
        self._game_dir: str  = ""
        # Legacy NPC registry (kept for compatibility)
        self._npc_registry   = None

        # Gizmo
        self._gizmo_axis: Optional[int]       = None
        self._gizmo_hover: Optional[int]      = None
        self._gizmo_drag_start_mouse: Optional[QPoint] = None
        self._gizmo_drag_start_pos   = None
        self._gizmo_drag_start_rot   = 0.0
        self._gizmo_tips:  Dict[int, QPoint]  = {}
        self._gizmo_origin_screen: Optional[QPoint] = None
        self._snap_enabled = False
        self._snap_size    = SNAP_UNIT

        # Room snapping state (snap_threshold in world units)
        self._room_snap_threshold: float = 0.5

        # Room instances (from RoomAssemblyPanel)
        self._room_instances: list = []

        # Walkmesh triangles
        self._walk_tris: list   = []
        self._nowalk_tris: list = []

        # Walkmesh edit mode — when True, left-click ray-casts against
        # _walk_tris using Möller-Trumbore and emits walkmesh_face_selected.
        # Reference: Phase 2.1 roadmap; Ericson §5.3.6.
        self._walkmesh_edit_mode: bool = False
        self._selected_face_idx:  int  = -1

        # VIS portal data — set by main_window after loading a module whose
        # .vis file was extracted.  Forwarded to renderer.set_vis_rooms().
        self._vis_room_names: Optional[set] = None
        if _HAS_QT:
            self._move_timer = QTimer(self)
            self._move_timer.setInterval(16)
            self._move_timer.timeout.connect(self._process_movement)

        # Redraw timer (~30 fps)
        if _HAS_QT:
            self._redraw_timer = QTimer(self)
            self._redraw_timer.setInterval(33)
            self._redraw_timer.timeout.connect(self.update)
            self._redraw_timer.start()

        # Subscribe to module state
        try:
            get_module_state().on_change(self._on_module_changed)
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────────────────

    def set_animation_panel(self, panel) -> None:
        """Bind an AnimationTimelinePanel to this viewport.

        Connects the viewport's ``frame_advanced(float)`` signal to the
        panel's ``_poll_player`` slot so the ruler updates every rendered
        frame rather than on a blind 50 ms timer.  Calling with ``panel=None``
        disconnects any previously bound panel.

        This is a convenience wrapper — the same connection can be made
        manually via ``panel.set_viewport(self)``.
        """
        if panel is None:
            return
        try:
            panel.set_viewport(self)
        except Exception as e:
            log.debug(f"set_animation_panel: {e}")

    def set_placement_mode(self, enabled: bool,
                           template_resref: str = "",
                           asset_type: str = "placeable"):
        self._placement_mode = enabled
        self._place_template  = template_resref
        self._place_asset_type = asset_type
        if _HAS_QT:
            self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def select_object(self, obj):
        self._selected_obj = obj
        self._rebuild_object_vaos()
        self.update()

    def load_rooms(self, rooms: list):
        """Called by main_window when Room Grid changes."""
        self._room_instances = list(rooms)
        if not self._renderer.ready:
            self._renderer.init()
        self._renderer.rebuild_room_vaos(self._room_instances, self._game_dir)
        # Attempt to load textures for room meshes from the game directory
        if self._game_dir:
            try:
                self.load_textures_for_rooms(self._game_dir)
            except Exception as e:
                log.debug(f"load_textures_for_rooms: {e}")

        # Update scene graph with room data
        if _HAS_NEW_ENGINE and self._scene_graph is not None:
            try:
                self._scene_graph.clear()
                for ri in rooms:
                    name = (getattr(ri, 'mdl_name', None) or
                            getattr(ri, 'resref', None) or
                            getattr(ri, 'name', 'room')).lower()
                    # Use is-not-None checks so rooms at world origin (0,0,0)
                    # are placed correctly (plain `or 0.0` would misplace them).
                    _rx = getattr(ri, 'world_x', None)
                    if _rx is None: _rx = getattr(ri, 'x', None)
                    if _rx is None: _rx = 0.0
                    _ry = getattr(ri, 'world_y', None)
                    if _ry is None: _ry = getattr(ri, 'y', None)
                    if _ry is None: _ry = 0.0
                    _rz = getattr(ri, 'world_z', None)
                    if _rz is None: _rz = getattr(ri, 'z', None)
                    if _rz is None: _rz = 0.0
                    x, y, z = float(_rx), float(_ry), float(_rz)
                    from ..engine.scene_manager import SceneRoom
                    room = SceneRoom(name=name, position=(x, y, z))
                    self._scene_graph.add_room(room)
            except Exception as e:
                log.debug(f"SceneGraph room update: {e}")

        if rooms:
            self._frame_rooms()
        self.update()

    def load_git_entities(self, git_data=None):
        """
        Load GIT entities into the engine entity registry and scene graph.
        Should be called when a module's GIT data changes.
        """
        if not _HAS_NEW_ENGINE:
            return
        if git_data is None:
            try:
                state = get_module_state()
                git_data = state.git if state else None
            except Exception:
                return
        if git_data is None:
            return

        try:
            # Populate entity registry
            if self._entity_registry is not None:
                count = self._entity_registry.populate_from_git(git_data)
                log.debug(f"EntityRegistry: {count} entities from GIT")

                # Wire AnimationSet: create a player for each entity that has
                # mesh_data (creatures / doors with loaded models).
                if _HAS_NEW_ENGINE and self._anim_set is not None:
                    self._anim_set.clear()
                    for ent in self._entity_registry.entities:
                        if ent.mesh_data is not None:
                            ent.setup_animation_player()
                            if ent._animation_player is not None:
                                # Register in AnimationSet for centralized update
                                self._anim_set._players[ent.entity_id] = \
                                    ent._animation_player
                                # Start default idle animation
                                try:
                                    from ..engine.animation_system import (
                                        get_default_idle_animation)
                                    idle = get_default_idle_animation(ent.mesh_data)
                                    ent._animation_player.play(idle, loop=True)
                                    log.debug(
                                        f"Entity {ent.entity_id}: "
                                        f"idle anim '{idle}'")
                                except Exception:
                                    pass

            # Populate scene graph entities
            if self._scene_graph is not None:
                self._scene_graph.clear_entities()
                self._scene_graph._populate_entities_from_git(git_data)

            # Update VAOs for GIT objects
            self._rebuild_object_vaos()
            self.update()
        except Exception as e:
            log.debug(f"load_git_entities: {e}")

    def set_game_dir(self, game_dir: str):
        """Set the KotOR game / extract directory and auto-reload room textures.

        Setting a new game_dir immediately re-triggers texture loading for any
        rooms already in the viewport, so callers don't need to call
        load_textures_for_rooms() separately.
        """
        self._game_dir = game_dir
        # Auto-reload textures whenever the game dir changes and rooms are loaded
        if game_dir and self._renderer.ready and self._renderer._room_vaos:
            try:
                self.load_textures_for_rooms(game_dir)
            except Exception as e:
                log.debug(f"set_game_dir auto-texture: {e}")
            self.update()

    def set_patrol_path(self, creature_tag: str, waypoints: list) -> bool:
        """
        Set or clear the patrol route for a creature entity.

        Parameters
        ----------
        creature_tag : str
            Tag of the creature to assign the patrol to.
        waypoints : list
            List of (x, y, z) tuples.  Pass an empty list to disable patrolling.

        Returns True if the entity was found and updated.
        """
        if self._entity_registry is None:
            return False
        try:
            entities = self._entity_registry.get_by_tag(creature_tag)
            if not entities:
                log.debug(f"set_patrol_path: entity '{creature_tag}' not found")
                return False
            for ent in entities:
                if hasattr(ent, 'set_patrol_route'):
                    ent.set_patrol_route(waypoints)
            log.debug(f"set_patrol_path: '{creature_tag}' → {len(waypoints)} waypoints")
            return True
        except Exception as e:
            log.debug(f"set_patrol_path error: {e}")
            return False

    def load_texture_from_tpc(self, tex_resref: str, tpc_bytes: bytes) -> bool:
        """
        Decode a TPC texture and upload it to the GL context.

        tex_resref: resref (without extension, will be lowercased).
        tpc_bytes:  raw .tpc file bytes.

        Matches Kotor.NET TPCTextureFactory.FromStream() but via our own
        TPCReader for cross-platform compatibility without S3TC hardware support
        (we decompress DXT1/DXT5 to RGBA8 using Python).

        Returns True if the texture was successfully loaded.
        """
        if not self._renderer.ready:
            return False
        try:
            from ..formats.tpc_reader import TPCReader
            tpc = TPCReader.from_bytes(tpc_bytes)
            # Decompress to RGBA8 for software GL (Mesa llvmpipe doesn't always
            # support compressed texture upload without GL_EXT_texture_compression_s3tc)
            rgba = tpc.to_rgba()          # bytes: width × height × 4
            w, h = tpc.width, tpc.height
            return self._renderer.load_texture(tex_resref, rgba, w, h)
        except Exception as e:
            log.debug(f"load_texture_from_tpc '{tex_resref}': {e}")
            return False

    def load_textures_for_rooms(self, game_dir: str = ""):
        """
        Scan room VAOs for needed texture and lightmap resrefs, find TPC/TGA files
        in game_dir, decode them and upload to GL.

        Loads both diffuse textures and lightmap textures.
        Called automatically after rebuild_room_vaos() when a game_dir is set.
        """
        if not self._renderer.ready or not game_dir:
            return
        # Collect all unique texture + lightmap names from room VAOs
        needed_diffuse: set = set()
        needed_lightmap: set = set()
        for e in self._renderer._room_vaos:
            tn = e.get("tex_name", "") or e.get("tex_resref", "")
            if tn and tn.lower() not in ("", "null"):
                needed_diffuse.add(tn.lower())
            lm = e.get("lmap_name", "")
            if lm and lm.lower() not in ("", "null"):
                needed_lightmap.add(lm.lower())

        if not needed_diffuse and not needed_lightmap:
            return

        # Build case-insensitive file index — scan the root dir AND common
        # KotOR texture subdirectories so textures are found regardless of
        # whether the user pointed at a raw extract dir or a full game install.
        # Priority order (earlier wins):
        #   1. <game_dir>/  (flat extract or Override/)
        #   2. <game_dir>/textures/
        #   3. <game_dir>/Override/
        #   4. <game_dir>/data/  (some modding setups place textures here)
        #   5. <game_dir>/texturepacks/  (KotOR 1 texture packs)
        SCAN_SUBDIRS = ("", "textures", "Override", "data", "texturepacks",
                        "Textures", "override", "Data")
        try:
            file_idx: Dict[str, str] = {}
            for subdir in SCAN_SUBDIRS:
                scan_root = os.path.join(game_dir, subdir) if subdir else game_dir
                if not os.path.isdir(scan_root):
                    continue
                for fname in os.listdir(scan_root):
                    key = fname.lower()
                    if key not in file_idx:   # earlier entries win
                        file_idx[key] = os.path.join(scan_root, fname)
        except OSError:
            return

        def _load_one(resref: str, is_lightmap: bool = False) -> bool:
            """Try to load a single texture by resref. Returns True if loaded."""
            cache = self._renderer._lmap_cache if is_lightmap else self._renderer._tex_cache
            if resref in cache:
                return True   # already loaded

            # TPC preferred over TGA (matches KotOR engine priority)
            for ext in ('.tpc', '.tga'):
                candidate = file_idx.get(resref + ext)
                if not candidate:
                    candidate = file_idx.get(resref + ext.upper())
                # Fuzzy fallback: suffix after first '_' (handles lsl_* → sle_* etc.)
                if not candidate:
                    underscore = resref.find('_')
                    if underscore > 0:
                        suffix = resref[underscore:]   # e.g. "_dirt02"
                        for idx_key, idx_path in file_idx.items():
                            if idx_key.endswith(suffix + ext) or idx_key.endswith(suffix + ext.upper()):
                                candidate = idx_path
                                break
                if candidate and os.path.exists(candidate):
                    try:
                        raw = open(candidate, 'rb').read()
                        if ext == '.tpc':
                            ok = self._load_tpc_texture(resref, raw, is_lightmap=is_lightmap)
                        else:
                            ok = self._load_tga_texture_internal(resref, raw, is_lightmap=is_lightmap)
                        if ok:
                            return True
                    except Exception as exc:
                        log.debug(f"Texture load '{candidate}': {exc}")

            # Archive fallback: query global ResourceManager (KEY/BIF archives)
            # This handles textures that are packed in chitin.key but not extracted.
            try:
                from ..formats.archives import get_resource_manager
                rm = get_resource_manager()
                if rm and rm.is_loaded:
                    # Try TPC first (type 2056), then TGA (type 3)
                    for ext_str, loader in (('tpc', self._load_tpc_texture),
                                            ('tga', self._load_tga_texture_internal)):
                        raw = rm.get_file(resref, ext_str)
                        if raw:
                            ok = loader(resref, raw, is_lightmap=is_lightmap)
                            if ok:
                                return True
            except Exception as exc:
                log.debug(f"Archive texture fallback '{resref}': {exc}")

            return False

        loaded_d = sum(1 for r in needed_diffuse if _load_one(r, False))
        loaded_l = sum(1 for r in needed_lightmap if _load_one(r, True))

        if loaded_d or loaded_l:
            log.info(f"Loaded {loaded_d}/{len(needed_diffuse)} diffuse + "
                     f"{loaded_l}/{len(needed_lightmap)} lightmap textures from '{game_dir}'")

        # Always re-attach textures to room VAOs from cache — even if all
        # textures were already cached, the VAOs still need their e['tex']
        # populated (e.g. after module reload or second call).
        reattached = 0
        for e in self._renderer._room_vaos:
            tn = (e.get("tex_name", "") or e.get("tex_resref", "")).lower()
            if tn:
                cached = self._renderer._tex_cache.get(tn)
                if cached is not None and e.get("tex") is not cached:
                    e["tex"] = cached
                    reattached += 1
            lm = e.get("lmap_name", "").lower()
            if lm:
                lmap_cached = self._renderer._lmap_cache.get(lm)
                if lmap_cached is not None and e.get("lmap_tex") is not lmap_cached:
                    e["lmap_tex"] = lmap_cached
                    reattached += 1
        if reattached:
            log.debug(f"Re-attached {reattached} texture bindings to room VAOs")
            self.update()

    def _load_tga_texture(self, tex_resref: str, tga_bytes: bytes) -> bool:
        """
        Minimal TGA loader — supports uncompressed 24-bit and 32-bit TGA.
        Converts to RGBA8 and uploads to GL.

        Uses numpy for fast vectorised BGR(A)→RGBA conversion when available;
        falls back to a pure-Python loop so the function works without numpy.
        """
        try:
            import struct as _struct
            if len(tga_bytes) < 18:
                return False
            id_len  = tga_bytes[0]
            img_type = tga_bytes[2]
            w = _struct.unpack_from('<H', tga_bytes, 12)[0]
            h = _struct.unpack_from('<H', tga_bytes, 14)[0]
            bpp = tga_bytes[16]
            descriptor = tga_bytes[17]           # bit 5 = top-left origin
            if img_type not in (2, 3) or bpp not in (24, 32):
                return False  # only uncompressed RGB/RGBA
            if w == 0 or h == 0:
                return False
            data_off = 18 + id_len
            stride   = bpp // 8
            px_count = w * h
            raw = tga_bytes[data_off: data_off + px_count * stride]

            if _HAS_NUMPY:
                # Fast path — numpy vectorised channel swap
                arr = np.frombuffer(raw, dtype=np.uint8).reshape(px_count, stride)
                rgba = np.empty((px_count, 4), dtype=np.uint8)
                rgba[:, 0] = arr[:, 2]   # R  ← B
                rgba[:, 1] = arr[:, 1]   # G  ← G
                rgba[:, 2] = arr[:, 0]   # B  ← R
                rgba[:, 3] = arr[:, 3] if bpp == 32 else 255
                # TGA origin: if bit 5 of descriptor is 0, rows are bottom-first
                if not (descriptor & 0x20):
                    rgba = rgba.reshape(h, w, 4)[::-1].reshape(px_count, 4)
                rgba_bytes = rgba.tobytes()
            else:
                # Slow path — pure Python pixel loop
                rgba = bytearray(px_count * 4)
                for i in range(px_count):
                    b, g, r = raw[i*stride], raw[i*stride+1], raw[i*stride+2]
                    a = raw[i*stride+3] if bpp == 32 else 255
                    rgba[i*4:i*4+4] = bytes([r, g, b, a])
                # Flip rows if bottom-origin (bit 5 of descriptor = 0)
                if not (descriptor & 0x20):
                    row_bytes = w * 4
                    flipped = bytearray(px_count * 4)
                    for row in range(h):
                        src = (h - 1 - row) * row_bytes
                        dst = row * row_bytes
                        flipped[dst:dst+row_bytes] = rgba[src:src+row_bytes]
                    rgba = flipped
                rgba_bytes = bytes(rgba)

            return self._renderer.load_texture(tex_resref, rgba_bytes, w, h)
        except Exception as e:
            log.debug(f"_load_tga_texture '{tex_resref}': {e}")
            return False

    def _load_tga_texture_internal(self, tex_resref: str, tga_bytes: bytes,
                                    is_lightmap: bool = False) -> bool:
        """
        Internal TGA loader with lightmap flag support.
        Wraps _load_tga_texture with is_lightmap cache routing.
        """
        try:
            import struct as _struct
            if len(tga_bytes) < 18:
                return False
            id_len  = tga_bytes[0]
            img_type = tga_bytes[2]
            w = _struct.unpack_from('<H', tga_bytes, 12)[0]
            h = _struct.unpack_from('<H', tga_bytes, 14)[0]
            bpp = tga_bytes[16]
            descriptor = tga_bytes[17]
            if img_type not in (2, 3) or bpp not in (24, 32):
                return False
            if w == 0 or h == 0:
                return False
            data_off = 18 + id_len
            stride   = bpp // 8
            px_count = w * h
            raw = tga_bytes[data_off: data_off + px_count * stride]

            if _HAS_NUMPY:
                arr = np.frombuffer(raw, dtype=np.uint8).reshape(px_count, stride)
                rgba = np.empty((px_count, 4), dtype=np.uint8)
                rgba[:, 0] = arr[:, 2]
                rgba[:, 1] = arr[:, 1]
                rgba[:, 2] = arr[:, 0]
                rgba[:, 3] = arr[:, 3] if bpp == 32 else 255
                if not (descriptor & 0x20):
                    rgba = rgba.reshape(h, w, 4)[::-1].reshape(px_count, 4)
                rgba_bytes = rgba.tobytes()
            else:
                rgba = bytearray(px_count * 4)
                for i in range(px_count):
                    b, g, r = raw[i*stride], raw[i*stride+1], raw[i*stride+2]
                    a = raw[i*stride+3] if bpp == 32 else 255
                    rgba[i*4:i*4+4] = bytes([r, g, b, a])
                if not (descriptor & 0x20):
                    row_bytes = w * 4
                    flipped = bytearray(px_count * 4)
                    for row in range(h):
                        src = (h - 1 - row) * row_bytes
                        dst = row * row_bytes
                        flipped[dst:dst+row_bytes] = rgba[src:src+row_bytes]
                    rgba = flipped
                rgba_bytes = bytes(rgba)

            return self._renderer.load_texture(tex_resref, rgba_bytes, w, h,
                                               is_lightmap=is_lightmap)
        except Exception as e:
            log.debug(f"_load_tga_texture_internal '{tex_resref}': {e}")
            return False

    def _load_tpc_texture(self, tex_resref: str, tpc_bytes: bytes,
                          is_lightmap: bool = False) -> bool:
        """Decode a KotOR TPC file and upload RGBA to renderer.

        Uses TPCReader.from_bytes() → TPCImage.rgba_bytes.
        Ref: Varcholik Ch.6 DXT; tpc_reader.py TPCImage.
        """
        try:
            from ..formats.tpc_reader import TPCReader
            img = TPCReader.from_bytes(tpc_bytes)
            if not img.is_valid or not img.rgba_bytes:
                return False
            return self._renderer.load_texture(
                tex_resref, img.rgba_bytes, img.width, img.height,
                is_lightmap=is_lightmap)
        except Exception as e:
            log.debug(f"_load_tpc_texture '{tex_resref}': {e}")
            return False

    def set_app_mode(self, mode: str):
        """Switch between 'module_editor' and 'level_builder' modes."""
        if mode in ("module_editor", "level_builder"):
            self._app_mode = mode
            self.update()

    def generate_module_thumbnail(self, size: int = 256) -> Optional["QImage"]:
        """
        Generate an isometric thumbnail of the currently loaded module geometry.

        Creates a temporary isometric camera, renders the scene at *size*×*size*,
        and returns the result as a QImage suitable for use in the content browser.

        Args:
            size: Thumbnail resolution in pixels (default 256×256).

        Returns:
            QImage on success, None if no geometry loaded or renderer not ready.
        """
        if not self._renderer.ready or not self._renderer._room_vaos:
            return None
        if not _HAS_NUMPY or not _HAS_QT:
            return None

        try:
            # Create isometric camera framed on the loaded geometry
            thumb_cam = OrbitCamera()
            thumb_cam.yaw = 35.0
            thumb_cam.pitch = 30.0
            thumb_cam.fov = 50.0

            # Use the same framing as the viewport camera if rooms are loaded
            if self._room_instances and self._game_dir and _HAS_MDL:
                # Re-use cached framing from room data
                thumb_cam.target = self.camera.target.copy()
                thumb_cam.distance = self.camera.distance
                thumb_cam.far = self.camera.far
            else:
                thumb_cam.target = self.camera.target.copy()
                thumb_cam.distance = self.camera.distance
                thumb_cam.far = self.camera.far

            # Render at the given size
            raw = self._renderer.render_thumbnail(size, size, thumb_cam)
            if raw is None:
                return None

            # Convert OpenGL RGBA (bottom-row first) to QImage (top-row first)
            if _HAS_NUMPY:
                arr = np.frombuffer(raw, dtype=np.uint8).reshape(size, size, 4)
                arr_flip = arr[::-1].copy()
                img = QImage(arr_flip.tobytes(), size, size,
                             size * 4, QImage.Format_RGBA8888)
            else:
                img = QImage(raw, size, size, size * 4, QImage.Format_RGBA8888)
                img = img.mirrored(False, True)
            return img.copy()  # detach from raw buffer
        except Exception as e:
            log.debug(f"generate_module_thumbnail: {e}")
            return None

    def toggle_walkmesh(self, visible: bool = None):
        """Show/hide walkmesh overlay."""
        if visible is None:
            self._show_walkmesh = not self._show_walkmesh
        else:
            self._show_walkmesh = bool(visible)
        self.update()

    def set_walkmesh_edit_mode(self, enabled: bool) -> None:
        """Enable/disable walkmesh face-selection mode.

        Left-click casts Möller-Trumbore ray → emits walkmesh_face_selected.
        Disabling resets selected face to -1.
        Ref: Phase 2.1; Ericson §5.3.6 Möller-Trumbore.
        """
        self._walkmesh_edit_mode = bool(enabled)
        if not enabled:
            self._selected_face_idx = -1
        self.update()

    def get_selected_face_index(self) -> int:
        """Return the currently selected walkmesh face index, or -1 if none."""
        return self._selected_face_idx

    def set_vis_rooms(self, visible_names: Optional[set]) -> None:
        """Set portal-visible room names (None = disable culling).

        Called by main_window after loading a .vis file from the module.
        Ref: Eberly §7 portal rendering; Ericson §7.6 cells & portals.
        """
        self._vis_room_names = visible_names
        if self._renderer.ready:
            self._renderer.set_vis_rooms(visible_names)

    def load_walkmesh(self, walk_tris: list, nowalk_tris: list):
        """Load walkmesh triangles for overlay rendering."""
        self._walk_tris   = list(walk_tris)
        self._nowalk_tris = list(nowalk_tris)
        self._walkmesh_loaded = bool(walk_tris or nowalk_tris)
        if not self._renderer.ready:
            self._renderer.init()
        if self._renderer.ready:
            self._renderer.rebuild_walkmesh_vaos(self._walk_tris, self._nowalk_tris)
        self.update()

    def load_walkmesh_from_rooms(self, room_instances: list,
                                  game_dir: str = "") -> bool:
        """Load and merge WOK walkmesh data from all rooms into the overlay.

        Translates each face to world space from room positions.
        Returns True if ≥ 1 walkmesh was loaded.
        """
        try:
            from ..formats.wok_parser import build_module_walkmesh
            from ..utils.resource_manager import get_resource_manager

            # Convert room_instances to iterable of objects with resref + position
            # RoomInstance has .mdl_name, .world_x, .world_y, .world_z
            class _RoomProxy:
                def __init__(self, ri):
                    self.resref   = getattr(ri, 'mdl_name',
                                    getattr(ri, 'resref',
                                    getattr(ri, 'name', 'unknown'))).lower()
                    x = getattr(ri, 'world_x', getattr(ri, 'x', 0.0))
                    y = getattr(ri, 'world_y', getattr(ri, 'y', 0.0))
                    z = getattr(ri, 'world_z', getattr(ri, 'z', 0.0))
                    self.position = (float(x), float(y), float(z))

            proxies = [_RoomProxy(ri) for ri in room_instances]
            rm = get_resource_manager()

            wm = build_module_walkmesh(proxies, resource_manager=rm,
                                       game_dir=game_dir)

            walk_tris   = [f.as_tuple() for f in wm.walkable_faces]
            nowalk_tris = [f.as_tuple() for f in wm.non_walkable_faces]

            self.load_walkmesh(walk_tris, nowalk_tris)
            log.info(f"Loaded walkmesh: {len(walk_tris)} walkable, "
                     f"{len(nowalk_tris)} non-walkable triangles")
            return bool(walk_tris or nowalk_tris)

        except Exception as e:
            log.warning(f"load_walkmesh_from_rooms: {e}", exc_info=False)
            return False

    # ── MDL → GPU mesh bridge (Phase 3.1) ────────────────────────────────────

    def load_mdl_mesh(
        self,
        mdl_path: str,
        mdx_path: str = "",
        world_x: float = 0.0,
        world_y: float = 0.0,
        world_z: float = 0.0,
        texture_dir: str = "",
    ) -> bool:
        """Parse MDL/MDX and upload renderable nodes to the GPU.

        Expands indexed geometry via _expand_mdl_node(), then calls
        _upload_textured_mesh() and appends each VAO to _room_vaos.
        Returns True if ≥ 1 node was uploaded.
        Ref: mdl_parser.py; KotorBlender mdl/reader.py; Phase 3.1.
        """
        if not mdl_path or not _HAS_MDL:
            return False
        if not self._renderer.ready:
            if not self._renderer.init():
                return False

        if not mdx_path:
            mdx_path = mdl_path.replace(".mdl", ".mdx").replace(".MDL", ".MDX")

        try:
            from ..formats.mdl_parser import MDLParser, MeshData
            mesh_data: MeshData = MDLParser.parse_files(mdl_path, mdx_path)
        except Exception as e:
            log.warning(f"load_mdl_mesh: MDL parse failed for {mdl_path}: {e}")
            return False

        renderable = mesh_data.visible_mesh_nodes()
        if not renderable:
            log.debug(f"load_mdl_mesh: no renderable nodes in {mdl_path}")
            return False

        import os
        loaded = 0
        for node in renderable:
            try:
                positions, normals, uvs_out, uvs2_out = _expand_mdl_node(node)
                if not positions:
                    continue
                px, py, pz  = node.position
                _tc = getattr(node, 'texture_clean', node.texture or "")
                _lc = getattr(node, 'lightmap_clean', node.lightmap or "")
                tex_key = (_tc if isinstance(_tc, str) else "").strip().lower()
                lm_key  = (_lc if isinstance(_lc, str) else "").strip().lower()
                col = getattr(node, 'diffuse', None)
                rc  = (tuple(max(0.15, min(1.0, v)) for v in col[:3])
                       if col and len(col) >= 3 and max(col) > 0.05 else (0.7, 0.7, 0.7))
                e = self._renderer._upload_textured_mesh(
                    positions, normals,
                    uvs_out if uvs_out else [], uvs2_out if uvs2_out else [], rc)
                if e:
                    e.update({"name": node.name,
                               "tx": world_x+px, "ty": world_y+py, "tz": world_z+pz,
                               "tex_name": tex_key, "lmap_name": lm_key,
                               "alpha": float(getattr(node, 'alpha', 1.0)),
                               "from_mdl": True})
                    self._renderer._room_vaos.append(e)
                    loaded += 1
            except Exception as e_:
                log.debug(f"load_mdl_mesh: node '{node.name}' failed: {e_}")

        if loaded:
            log.info(f"load_mdl_mesh: {loaded} node(s) from "
                     f"'{os.path.basename(mdl_path)}' @ "
                     f"({world_x:.1f},{world_y:.1f},{world_z:.1f})")
            self.update()
        return loaded > 0

    def frame_all(self):
        self._frame_all()

    def frame_selected(self):
        obj = self._selected_obj
        if obj is None:
            self._frame_all()
            return
        try:
            pos = obj.position
            self.camera.frame(
                np.array([pos.x, pos.y, pos.z], dtype='f4'), 5.0)
            self.update()
        except Exception:
            pass

    @property
    def is_play_mode(self) -> bool:
        return self._play_mode

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()

        # ── Ensure renderer is initialised ────────────────────────────────────
        if not self._renderer.ready:
            self._renderer.init()

        # ── Build render camera (orbit or play mode) ──────────────────────────
        render_camera = self.camera
        render_session = None

        if self._play_mode and self._play_session:
            render_session = self._play_session
            # For new engine: build a temporary OrbitCamera from play camera
            if _HAS_NEW_ENGINE and hasattr(self._play_session, 'camera'):
                play_cam = self._play_session.camera
                if play_cam is not None:
                    # Build a play-mode orbit camera matching the play position
                    play_orbit = OrbitCamera()
                    try:
                        eye    = play_cam.eye
                        target = play_cam.target
                        if _HAS_NUMPY:
                            eye_np    = np.array(eye, dtype='f4')
                            target_np = np.array(target, dtype='f4')
                            diff = eye_np - target_np
                            dist = float(np.linalg.norm(diff))
                            play_orbit.target   = target_np
                            play_orbit.distance = max(0.5, dist)
                            # Compute yaw/pitch from the difference vector
                            if dist > 0.01:
                                play_orbit.yaw   = math.degrees(math.atan2(diff[0], diff[1]))
                                play_orbit.pitch = math.degrees(math.asin(
                                    max(-1.0, min(1.0, diff[2] / dist))))
                            play_orbit.far = max(1000.0, dist * 4 + 50)
                        render_camera = play_orbit
                    except Exception:
                        pass

        # ── GL render ─────────────────────────────────────────────────────────
        if self._renderer.ready:
            raw = self._renderer.render(
                W, H, render_camera,
                render_session,
                show_walkmesh=self._show_walkmesh)
            if raw:
                # OpenGL reads bottom-to-top; QImage is top-to-bottom.
                # IMPORTANT: QImage(data, ...) does NOT copy the buffer.
                # We must call .copy() so Qt owns the pixel data before
                # the Python bytes object is garbage-collected.
                img = QImage(raw, W, H, W * 4,
                             QImage.Format_RGBA8888).mirrored(False, True).copy()
                p.drawImage(0, 0, img)
                self._last_frame = img
            else:
                self._draw_fallback_2d(p, W, H)
        else:
            self._draw_fallback_2d(p, W, H)

        # ── 2-D overlays ──────────────────────────────────────────────────────
        self._draw_gizmo_overlay(p)
        self._draw_selection_info(p, W, H)
        self._paint_hud(p, W, H)
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._renderer.ready:
            self._renderer.ensure_fbo(self.width(), self.height())

    # ── 2-D fallback ─────────────────────────────────────────────────────────

    def _draw_fallback_2d(self, p: "QPainter", W: int, H: int):
        """Top-down 2-D schematic when GL is unavailable."""
        p.fillRect(0, 0, W, H, QColor(20, 20, 30))

        scale    = 10.0
        origin_x = W // 2 - int(self.camera.target[0] * scale)
        origin_y = H // 2 + int(self.camera.target[1] * scale)

        step_px = max(int(scale), 4)
        grid_pen = QPen(QColor(40, 40, 55), 1)
        axis_pen = QPen(QColor(70, 70, 90), 1)

        x0 = origin_x % step_px
        while x0 < W:
            ww = (x0 - origin_x) / scale
            p.setPen(axis_pen if abs(ww) < 0.5 else grid_pen)
            p.drawLine(x0, 0, x0, H)
            x0 += step_px
        y0 = origin_y % step_px
        while y0 < H:
            wy = -(y0 - origin_y) / scale
            p.setPen(axis_pen if abs(wy) < 0.5 else grid_pen)
            p.drawLine(0, y0, W, y0)
            y0 += step_px

        room_colors = [
            QColor(80,120,160,160), QColor(80,140,90,160),
            QColor(150,100,60,160), QColor(100,80,150,160),
        ]
        p.setFont(QFont("Consolas", 7))
        for idx, ri in enumerate(self._room_instances):
            name = getattr(ri,'model_name',None) or getattr(ri,'name',f'room{idx}')
            rx = float(getattr(ri,'world_x',None) or
                       getattr(ri,'x',None) or
                       (getattr(ri,'grid_x',0)*10.0) or 0.0)
            ry = float(getattr(ri,'world_y',None) or
                       getattr(ri,'y',None) or
                       (getattr(ri,'grid_y',0)*10.0) or 0.0)
            col = room_colors[idx % len(room_colors)]
            sx = origin_x + int(rx * scale)
            sy = origin_y - int((ry + 10.0) * scale)
            pw, ph = int(10.0*scale), int(10.0*scale)
            p.fillRect(sx, sy, pw, ph, col)
            p.setPen(QPen(col.lighter(160), 2))
            p.drawRect(sx, sy, pw, ph)
            p.setPen(QPen(QColor(230,230,230)))
            p.drawText(sx+4, sy+14, name)

        # GIT dots
        try:
            state = get_module_state()
            if state and state.git:
                tc = {'placeables':QColor(80,160,255),'creatures':QColor(255,120,60),
                      'doors':QColor(220,200,40),'waypoints':QColor(200,60,200)}
                for attr, col in tc.items():
                    for obj in getattr(state.git,attr,[]):
                        pos = getattr(obj,'position',None)
                        if pos is None: continue
                        px = origin_x + int(pos.x*scale)
                        py = origin_y - int(pos.y*scale)
                        dot = QColor(255,255,0) if obj is self._selected_obj else col
                        p.setPen(Qt.NoPen)
                        p.setBrush(QBrush(dot))
                        r = 6 if obj is self._selected_obj else 4
                        p.drawEllipse(px-r, py-r, r*2, r*2)
        except Exception:
            pass

        # Status
        if not _HAS_MODERNGL:
            p.setPen(QPen(QColor(200, 100, 50)))
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.drawText(8, H-38, "3D rendering unavailable — moderngl not installed.")
            p.setPen(QPen(QColor(150, 200, 150)))
            p.drawText(8, H-24, "Run:  pip install moderngl PyOpenGL  then restart GModular.")
        elif _GL_INIT_ERROR:
            p.setPen(QPen(QColor(200, 100, 50)))
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.drawText(8, H-22, f"3D unavailable: {_GL_INIT_ERROR[:80]}")
        p.setPen(QPen(QColor(100, 100, 100)))
        p.setFont(QFont("Consolas", 7))
        p.drawText(8, H-8, "2D top-down view  |  RMB=orbit  MMB=pan  Scroll=zoom")

    # ── Selection info overlay ───────────────────────────────────────────────

    def _draw_selection_info(self, p: "QPainter", W: int, H: int):
        """
        Draw a small info tooltip above the selected object (UE5-style).
        Shows the object type, resref, and position when something is selected.
        """
        if not _HAS_QT or self._selected_obj is None or self._play_mode:
            return
        obj = self._selected_obj
        if not hasattr(obj, 'position'):
            return

        pos = obj.position
        sx, sy = self._world_to_screen(pos.x, pos.y, pos.z)
        if sx is None:
            return

        # Build info lines
        resref = getattr(obj, 'resref', '') or getattr(obj, 'tag', '')
        obj_type = type(obj).__name__.replace('GIT', '').lower()
        lines = [
            f"{obj_type.title()} — {resref}",
            f"Position: ({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})",
        ]
        bearing = getattr(obj, 'bearing', None)
        if bearing is not None:
            lines.append(f"Bearing: {bearing:.1f}°")

        # Render info card just above gizmo origin
        fn_info = QFont("Segoe UI", 8)
        p.setFont(fn_info)
        fm = p.fontMetrics()
        lh = fm.height() + 2
        bw = max(fm.horizontalAdvance(l) for l in lines) + 16
        bh = len(lines) * lh + 8
        bx = int(sx) - bw // 2
        by = int(sy) - 80 - bh
        bx = max(4, min(W - bw - 4, bx))
        by = max(4, by)

        # Background pill
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 180)))
        p.drawRoundedRect(bx, by, bw, bh, 5, 5)
        # Left accent stripe (UE5 orange selection color)
        p.setBrush(QBrush(QColor(255, 140, 0, 220)))
        p.drawRoundedRect(bx, by, 3, bh, 2, 2)

        # Text
        p.setPen(QColor(220, 220, 235))
        for i, line in enumerate(lines):
            p.drawText(bx + 8, by + 6 + (i + 1) * lh - 2, line)

    # ── HUD overlay ─────────────────────────────────────────────────────────

    def _paint_hud(self, p: "QPainter", W: int, H: int):
        """Draw a UE5-style heads-up display over the 3D viewport."""
        if not _HAS_QT:
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        fn_badge = QFont("Segoe UI", 8, QFont.Bold)
        fn_mono  = QFont("Consolas", 8)
        fn_tiny  = QFont("Segoe UI", 7)

        # ── Mode badge (top-left) ────────────────────────────────────────────────
        if self._play_mode:
            badge_bg     = QColor(20, 110, 40, 220)
            badge_border = QColor(50, 200, 80)
            badge_txt    = "▶  PLAY"
        elif self._placement_mode:
            badge_bg     = QColor(110, 60, 10, 220)
            badge_border = QColor(240, 150, 30)
            badge_txt    = f"⊕  PLACE  {self._place_asset_type.upper()}"
        elif self._app_mode == "module_editor":
            badge_bg     = QColor(15, 40, 90, 200)
            badge_border = QColor(56, 139, 253)
            badge_txt    = "✏  MODULE EDITOR"
        else:
            badge_bg     = QColor(10, 50, 40, 200)
            badge_border = QColor(50, 200, 160)
            badge_txt    = "□  LEVEL BUILDER"

        p.setFont(fn_badge)
        fm = p.fontMetrics()
        bw = fm.horizontalAdvance(badge_txt) + 20
        bh = 24
        bx, by = 8, 8
        p.fillRect(bx + 2, by + 2, bw, bh, QColor(0, 0, 0, 80))
        p.fillRect(bx, by, bw, bh, badge_bg)
        p.fillRect(bx, by, 3, bh, badge_border)
        p.setPen(badge_border)
        p.drawText(bx + 10, by + bh - 6, badge_txt)

        # ── GL backend / error (────────────────────────────────────────────────
        if not _HAS_MODERNGL:
            msg = "⚠  Install moderngl:  pip install moderngl PyOpenGL"
            p.setFont(fn_badge)
            p.fillRect(6, 38, W - 12, 22, QColor(0, 0, 0, 200))
            p.fillRect(6, 38, 3, 22, QColor(230, 80, 50))
            p.setPen(QColor(240, 100, 60))
            p.drawText(14, 54, msg)
        elif _GL_INIT_ERROR:
            p.setFont(fn_tiny)
            p.fillRect(6, 38, W - 12, 18, QColor(0, 0, 0, 190))
            p.fillRect(6, 38, 3, 18, QColor(230, 80, 50))
            p.setPen(QColor(240, 120, 60))
            p.drawText(14, 51, f"⚠ {_GL_INIT_ERROR[:80]}")
        else:
            p.setFont(fn_tiny)
            p.setPen(QColor(60, 70, 90))
            p.drawText(bx + bw + 8, by + 16, f"[{_GL_BACKEND}]")

        # ── Walkmesh / Navmesh badge (top-right) ─────────────────────────────────
        if self._walkmesh_loaded:
            wm_on  = self._show_walkmesh
            wm_txt = "◼ NAVMESH  ON" if wm_on else "◻ NAVMESH OFF"
            wm_bg  = QColor(10, 50, 20, 200) if wm_on else QColor(50, 20, 20, 180)
            wm_col = QColor(80, 220, 100) if wm_on else QColor(180, 80, 80)
            p.setFont(fn_badge)
            fw2 = p.fontMetrics().horizontalAdvance(wm_txt) + 20
            wm_x = W - fw2 - 8
            p.fillRect(wm_x, 8, fw2, 24, wm_bg)
            p.fillRect(wm_x, 8, 3, 24, wm_col)
            p.setPen(wm_col)
            p.drawText(wm_x + 10, 8 + 16, wm_txt)

        # ── Selected object info (centre stripe) ───────────────────────────────
        if self._selected_obj and not self._play_mode:
            obj    = self._selected_obj
            otype  = type(obj).__name__.replace("GIT", "")
            resref = getattr(obj, "resref", getattr(obj, "template_resref", "?"))
            pos    = getattr(obj, "position", None)
            tag    = getattr(obj, "tag", "") or ""
            if pos:
                info = (f"{otype}  ›  {tag!r}  [{resref}]"
                        f"  @  ({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})")
            else:
                info = f"{otype}  ›  {tag!r}  [{resref}]"
            p.setFont(fn_badge)
            fw = p.fontMetrics().horizontalAdvance(info) + 24
            ix = max(8, (W - fw) // 2)
            p.fillRect(ix - 4, 40, fw + 8, 24, QColor(0, 0, 0, 180))
            p.fillRect(ix - 4, 40, 3, 24, QColor(255, 200, 50))
            p.setPen(QColor(255, 220, 80))
            p.drawText(ix + 4, 40 + 16, info)

        # ── Camera info (bottom-right) ────────────────────────────────────────────────
        if self._play_mode and _HAS_NEW_ENGINE and self._play_session:
            # Show play camera info in play mode
            try:
                cam = self._play_session.camera
                player = self._play_session.player
                if cam and player:
                    eye = cam.eye
                    cam_lines = [
                        f"PLAY MODE  │  {getattr(cam, 'mode', '').replace('_',' ').upper()}",
                        f"Pos  X {player.x:.2f}  Y {player.y:.2f}  Z {player.z:.2f}",
                        f"Eye  X {eye[0]:.2f}  Y {eye[1]:.2f}  Z {eye[2]:.2f}",
                        f"Yaw {math.degrees(player.yaw):.0f}°  "
                        f"{'RUN' if player.is_running else 'WALK'}",
                    ]
                else:
                    cam_lines = ["PLAY MODE"]
            except Exception:
                cam_lines = ["PLAY MODE"]
        else:
            t = self.camera.target
            eye = self.camera.eye()
            cam_lines = [
                f"Az {self.camera.azimuth:.0f}°  El {self.camera.elevation:.0f}°  "
                f"D {self.camera.distance:.1f}",
                f"Target  X {t[0]:.2f}  Y {t[1]:.2f}  Z {t[2]:.2f}",
                f"Eye     X {eye[0]:.2f}  Y {eye[1]:.2f}  Z {eye[2]:.2f}",
            ]

            # Add engine stats if enabled
            if self._show_stats and _HAS_NEW_ENGINE and self._scene_graph:
                stats = self._scene_graph.stats
                cam_lines.append(str(stats))

        p.setFont(fn_mono)
        fm2  = p.fontMetrics()
        lh   = fm2.height() + 2
        th   = len(cam_lines) * lh + 10
        mw   = max(fm2.horizontalAdvance(l) for l in cam_lines) + 16
        rx   = W - mw - 8
        ry   = H - th - 22
        p.fillRect(rx - 4, ry, mw + 4, th, QColor(0, 0, 0, 160))
        p.fillRect(rx - 4, ry, 3, th,
                   QColor(80, 220, 100, 200) if self._play_mode else QColor(56, 139, 253, 180))
        p.setPen(QColor(140, 220, 140) if self._play_mode else QColor(140, 170, 200))
        for i, line in enumerate(cam_lines):
            p.drawText(rx + 4, ry + 6 + (i + 1) * lh - 1, line)

        # ── Controls hint bar (bottom) ───────────────────────────────────────────────
        if self._play_mode:
            # Show play mode hints + interaction hint from engine
            interaction_hint = ""
            if _HAS_NEW_ENGINE and self._play_session:
                try:
                    interaction_hint = self._play_session.interaction_hint
                except Exception:
                    pass
            if interaction_hint:
                hint = f"WASD=move  │  Shift=sprint  │  {interaction_hint}  │  Esc=exit"
            else:
                hint = "WASD=move  │  Mouse=look  │  Shift=sprint  │  E=interact  │  Esc=exit"
            hint_col = QColor(80, 220, 100)
        elif self._placement_mode:
            hint     = f"LMB = place {self._place_template or ''!r}  │  Esc = cancel"
            hint_col = QColor(240, 180, 60)
        else:
            hint     = ("RMB/Alt+LMB = orbit  │  MMB = pan  │  Scroll = zoom  │  "
                        "WASD = fly  │  Shift = sprint  │  Ctrl = precise  │  "
                        "F = frame  │  W = navmesh  │  Del = delete")
            hint_col = QColor(70, 80, 100)

        p.setFont(fn_tiny)
        p.fillRect(0, H - 18, W, 18, QColor(0, 0, 0, 130))
        p.setPen(hint_col)
        p.drawText(8, H - 5, hint)

        # ── Snap indicator ────────────────────────────────────────────────────────────
        if getattr(self, "_snap_enabled", False) and not self._play_mode:
            snap_txt = f"⊞ SNAP  {self._snap_size:.2f}u"
            p.setFont(fn_badge)
            sw = p.fontMetrics().horizontalAdvance(snap_txt)
            p.fillRect(W - sw - 20, H - 42, sw + 16, 20, QColor(0, 0, 0, 160))
            p.setPen(QColor(255, 220, 50))
            p.drawText(W - sw - 12, H - 27, snap_txt)

    # ── Gizmo overlay ─────────────────────────────────────────────────────────

    def _draw_gizmo_overlay(self, p: "QPainter"):
        obj = self._selected_obj
        if obj is None or self._play_mode or self._placement_mode:
            return
        if not hasattr(obj, 'position'):
            return
        pos = obj.position
        sx, sy = self._world_to_screen(pos.x, pos.y, pos.z)
        if sx is None:
            return

        origin = QPoint(int(sx), int(sy))
        self._gizmo_origin_screen = origin

        ax_tips_raw = {
            _AX_X: self._world_to_screen(pos.x+1.,pos.y,pos.z),
            _AX_Y: self._world_to_screen(pos.x,pos.y+1.,pos.z),
            _AX_Z: self._world_to_screen(pos.x,pos.y,pos.z+1.),
        }
        tips: Dict[int, QPoint] = {}
        for ax, (tx, ty) in ax_tips_raw.items():
            if tx is None: continue
            raw_dx, raw_dy = tx-sx, ty-sy
            length = math.hypot(raw_dx, raw_dy) or 1.
            tips[ax] = QPoint(int(sx + raw_dx/length*GIZMO_LEN),
                              int(sy + raw_dy/length*GIZMO_LEN))
        tips[_AX_R] = origin
        self._gizmo_tips = tips

        p.setRenderHint(QPainter.Antialiasing)
        ax_colors = {_AX_X:_GIZMO_X_COL, _AX_Y:_GIZMO_Y_COL, _AX_Z:_GIZMO_Z_COL}
        labels    = {_AX_X:"X", _AX_Y:"Y", _AX_Z:"Z"}

        for ax, tip in tips.items():
            if ax == _AX_R: continue
            col = _GIZMO_ACT if (self._gizmo_axis==ax or self._gizmo_hover==ax) \
                else ax_colors[ax]
            p.setPen(QPen(col, 3 if self._gizmo_axis==ax else 2))
            p.drawLine(origin, tip)
            dx, dy = tip.x()-origin.x(), tip.y()-origin.y()
            length = math.hypot(dx,dy) or 1.
            ux, uy = dx/length, dy/length
            px, py = -uy, ux
            poly = QPolygon([
                QPoint(tip.x(), tip.y()),
                QPoint(int(tip.x()-ux*GIZMO_HEAD+px*GIZMO_HEAD*.4),
                       int(tip.y()-uy*GIZMO_HEAD+py*GIZMO_HEAD*.4)),
                QPoint(int(tip.x()-ux*GIZMO_HEAD-px*GIZMO_HEAD*.4),
                       int(tip.y()-uy*GIZMO_HEAD-py*GIZMO_HEAD*.4)),
            ])
            p.setBrush(QBrush(col)); p.setPen(Qt.NoPen)
            p.drawPolygon(poly)
            p.setPen(QPen(col))
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.drawText(tip.x()+4, tip.y()-4, labels[ax])

        ring_col = _GIZMO_ACT if (self._gizmo_axis==_AX_R or
                                   self._gizmo_hover==_AX_R) else _GIZMO_R_COL
        p.setPen(QPen(ring_col, 2, Qt.DashLine)); p.setBrush(Qt.NoBrush)
        ring_r = GIZMO_LEN//2
        p.drawEllipse(origin.x()-ring_r, origin.y()-ring_r, ring_r*2, ring_r*2)
        p.setPen(QPen(ring_col)); p.setFont(QFont("Consolas", 7))
        p.drawText(origin.x()+ring_r+2, origin.y()+4, "R")

        if self._snap_enabled:
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.setPen(QPen(QColor(255,220,50)))
            p.drawText(self.width()-120, self.height()-10,
                       f"SNAP  {self._snap_size:.2f}u")

        p.setFont(QFont("Consolas", 7))
        p.setPen(QPen(QColor(100,100,100)))
        p.drawText(6, self.height()-6,
                   "Gimbal: LMB drag axis  |  Ctrl=1u  Shift=0.25u  Ctrl+Shift=0.5u")

    # ── World→screen projection ───────────────────────────────────────────────

    def _world_to_screen(self, wx, wy, wz):
        if not _HAS_NUMPY:
            return None, None
        try:
            W, H = self.width(), self.height()
            vp = self.camera.projection_matrix(W/max(H,1)) @ self.camera.view_matrix()
            clip = vp @ np.array([wx, wy, wz, 1.], dtype='f4')
            if clip[3] <= 0:
                return None, None
            ndc_x = clip[0]/clip[3]
            ndc_y = clip[1]/clip[3]
            sx = (ndc_x+1.)*0.5*W
            sy = (1.-(ndc_y+1.)*0.5)*H
            return sx, sy
        except Exception:
            return None, None

    # ── Gizmo hit test ────────────────────────────────────────────────────────

    def _hit_gizmo(self, mx, my):
        if self._gizmo_origin_screen is None:
            return None
        origin = self._gizmo_origin_screen
        ring_r = GIZMO_LEN // 2
        d = math.hypot(mx-origin.x(), my-origin.y())
        if abs(d-ring_r) < GIZMO_HIT:
            return _AX_R
        for ax in (_AX_X, _AX_Y, _AX_Z):
            tip = self._gizmo_tips.get(ax)
            if tip is None: continue
            dx = tip.x()-origin.x(); dy = tip.y()-origin.y()
            seg2 = dx*dx+dy*dy
            if seg2 < 1: continue
            t = max(0., min(1., ((mx-origin.x())*dx+(my-origin.y())*dy)/seg2))
            cx = origin.x()+t*dx; cy = origin.y()+t*dy
            if math.hypot(mx-cx, my-cy) < GIZMO_HIT:
                return ax
        return None

    def _apply_snap(self, v):
        s = self._snap_size
        return round(v/s)*s

    # ── Module changed ────────────────────────────────────────────────────────

    def _on_module_changed(self):
        self._rebuild_object_vaos()

    def _rebuild_object_vaos(self):
        if not self._renderer.ready:
            if not self._renderer.init():
                return
        try:
            state = get_module_state()
        except Exception:
            return
        self._renderer.rebuild_object_vaos(state, self._selected_obj)
        # Also rebuild entity MDL VAOs when entity registry has models
        if _HAS_NEW_ENGINE and self._entity_registry:
            try:
                self._renderer.rebuild_entity_vaos(self._entity_registry)
            except Exception as e:
                log.debug(f"rebuild_entity_vaos: {e}")
        self.update()

    # ── Room framing ─────────────────────────────────────────────────────────

    def _frame_rooms(self):
        """
        Frame the camera to show all loaded room geometry.

        Priority order:
          1. Bounding box computed from parsed MDL vertices (via the model cache
             or direct parse) — most accurate for any room placement.
          2. Room instance positions from LYT (fallback when no MDL found).
        """
        if not _HAS_NUMPY:
            return

        pts = []

        # Strategy 1: collect bounds from already-parsed MDL meshes
        # The room VAOs were built from MDLParser data; re-parse the same MDL
        # to get the vertex bounds used for camera framing.
        if self._game_dir and self._room_instances and _HAS_MDL:
            try:
                from ..formats.mdl_parser import MDLParser, get_model_cache
                cache = get_model_cache()
                for ri in self._room_instances:
                    name = (getattr(ri, 'mdl_name', None) or
                            getattr(ri, 'model_name', None) or
                            getattr(ri, 'name', '') or '').lower()
                    if not name:
                        continue
                    # Try from model cache first (no re-parse needed)
                    for ext in ('.mdl', '.MDL'):
                        mdl_path = os.path.join(self._game_dir, name + ext)
                        if os.path.exists(mdl_path):
                            try:
                                mesh = cache.get(mdl_path)
                                if mesh is None:
                                    mdl_b = open(mdl_path, 'rb').read()
                                    mdx_p = os.path.splitext(mdl_path)[0] + '.mdx'
                                    mdx_b = open(mdx_p, 'rb').read() if os.path.exists(mdx_p) else b''
                                    mesh = MDLParser(mdl_b, mdx_b).parse()
                                if mesh:
                                    for node in mesh.visible_mesh_nodes():
                                        pts.extend(node.vertices)
                            except Exception:
                                pass
                            break
            except Exception:
                pass

        if pts:
            arr = np.array(pts, dtype='f4')
            xs = arr[:, 0]; ys = arr[:, 1]; zs = arr[:, 2]
            center = np.array([(xs.min()+xs.max())*0.5,
                               (ys.min()+ys.max())*0.5,
                               (zs.min()+zs.max())*0.5], dtype='f4')
            radius = float(np.linalg.norm(arr - center, axis=1).max())
            self.camera.frame(center, max(radius * 0.6, 5.0))
            return

        # Strategy 2: fallback to room instance positions (may be origin for synthesized LYT)
        if not self._room_instances:
            return
        lyt_pts = []
        for ri in self._room_instances:
            x = float(getattr(ri,'world_x',None) or getattr(ri,'x',None) or
                      (getattr(ri,'grid_x',0)*10.0) or 0.0)
            y = float(getattr(ri,'world_y',None) or getattr(ri,'y',None) or
                      (getattr(ri,'grid_y',0)*10.0) or 0.0)
            z = float(getattr(ri,'world_z',0.) or getattr(ri,'z',0.) or 0.)
            lyt_pts.append([x, y, z])
        arr    = np.array(lyt_pts, dtype='f4')
        center = arr.mean(axis=0)
        radius = float(np.linalg.norm(arr - center, axis=1).max()) if len(lyt_pts) > 1 else 10.
        self.camera.frame(center, max(radius, 15.))

    def _frame_all(self):
        try:
            state = get_module_state()
            if not state.git: return
            positions = []
            for obj in state.git.iter_all():
                pos = getattr(obj,'position',None)
                if pos: positions.append([pos.x,pos.y,pos.z])
            if not positions: return
            pts    = np.array(positions, dtype='f4')
            center = pts.mean(axis=0)
            radius = float(np.linalg.norm(pts-center,axis=1).max())
            self.camera.frame(center, max(radius, 1.))
            self.update()
        except Exception as e:
            log.debug(f"frame_all: {e}")

    # ── Pick ──────────────────────────────────────────────────────────────────

    def _pick_object(self, sx, sy):
        W, H = self.width(), self.height()
        try:
            state = get_module_state()
            if not state.git: return None
            origin, direction = self.camera.ray_from_screen(sx, sy, W, H)
            best_t, best_obj = float("inf"), None

            def slab(pos, hw, hh, hd):
                bmin = np.array([pos.x-hw, pos.y-hh, pos.z],       dtype='f4')
                bmax = np.array([pos.x+hw, pos.y+hh, pos.z+hd*2],  dtype='f4')
                t1 = (bmin-origin)/(direction+1e-20)
                t2 = (bmax-origin)/(direction+1e-20)
                tN = np.minimum(t1,t2).max()
                tF = np.maximum(t1,t2).min()
                if tN<=tF and tF>0: return tN if tN>0 else tF
                return None

            tests = [
                (state.git.placeables, .3, .3, .3),
                (state.git.creatures,  .35,.35,.7),
                (state.git.doors,      .5, .15,.9),
                (state.git.waypoints,  .15,.15,.5),
                (state.git.triggers,   .5, .5, .05),
                (state.git.sounds,     .2, .2, .2),
                (state.git.stores,     .3, .3, .4),
            ]
            for lst, hw,hh,hd in tests:
                for obj in lst:
                    t = slab(obj.position, hw,hh,hd)
                    if t and t < best_t:
                        best_t = t; best_obj = obj
            return best_obj
        except Exception as e:
            log.debug(f"pick: {e}"); return None

    def _pick_walkmesh_face(self, sx: int, sy: int):
        """
        Cast a ray from screen pixel (sx, sy) against the loaded walkmesh
        triangles and return (face_index, t) for the closest hit, or None.

        Uses the renderer's hit_test_walkmesh() which implements the
        Möller-Trumbore algorithm (Ericson §5.3.6, p.190-194).

        This is the backend for walkmesh-edit-mode face selection.
        The selected face index is also stored in self._selected_face_idx
        so the viewport can highlight it on the next render.
        """
        W, H = self.width(), self.height()
        try:
            origin, direction = self.camera.ray_from_screen(sx, sy, W, H)
            result = self._renderer.hit_test_walkmesh(
                tuple(origin), tuple(direction), self._walk_tris
            )
            return result  # (face_index, t) or None
        except Exception as e:
            log.debug(f"_pick_walkmesh_face: {e}")
            return None

    def _ray_ground_intersect(self, sx, sy):
        W, H = self.width(), self.height()
        try:
            origin, direction = self.camera.ray_from_screen(sx, sy, W, H)
            if abs(direction[2]) < 1e-9: return None
            t = -origin[2]/direction[2]
            if t < 0: return None
            from ..formats.gff_types import Vector3
            pt = origin + direction*t
            return Vector3(float(pt[0]), float(pt[1]), 0.)
        except Exception:
            return None

    # ── Play mode ─────────────────────────────────────────────────────────────

    def start_play_mode(self, camera_mode: str = "third_person"):
        """
        Enter interactive play mode.

        Uses the new PlayModeController for walkmesh-based movement,
        door interaction, and camera (TPS/FPS/free orbit).

        Args:
            camera_mode: 'third_person', 'first_person', or 'free_orbit'
        """
        if not _HAS_ENGINE:
            log.warning("Engine unavailable — play mode disabled")
            return
        try:
            state = get_module_state()
            git   = state.git if state else None

            # Determine start position from camera target or first waypoint
            start_x = float(self.camera.target[0]) if _HAS_NUMPY else 0.0
            start_y = float(self.camera.target[1]) if _HAS_NUMPY else 0.0
            start_z = 0.0

            # Try to find a waypoint or creature position as start
            if git:
                for attr in ('waypoints', 'creatures'):
                    items = getattr(git, attr, [])
                    if items:
                        pos = getattr(items[0], 'position', None)
                        if pos:
                            start_x, start_y = float(pos.x), float(pos.y)
                        break

            # Build walkmesh for collision
            walkmesh = None
            try:
                from ..formats.wok_parser import build_module_walkmesh
                from ..utils.resource_manager import get_resource_manager

                class _RoomProxy:
                    def __init__(self, ri):
                        self.resref = getattr(ri, 'mdl_name',
                                      getattr(ri, 'resref',
                                      getattr(ri, 'name', 'unknown'))).lower()
                        x = getattr(ri, 'world_x', getattr(ri, 'x', 0.0))
                        y = getattr(ri, 'world_y', getattr(ri, 'y', 0.0))
                        z = getattr(ri, 'world_z', getattr(ri, 'z', 0.0))
                        self.position = (float(x), float(y), float(z))

                if self._room_instances:
                    proxies = [_RoomProxy(ri) for ri in self._room_instances]
                    rm  = get_resource_manager()
                    wm  = build_module_walkmesh(proxies, resource_manager=rm,
                                               game_dir=self._game_dir)
                    if wm:
                        walkmesh = wm
                        log.debug("PlayMode: walkmesh built for collision")
            except Exception as e:
                log.debug(f"PlayMode walkmesh: {e}")

            # Create new PlaySession
            if _HAS_NEW_ENGINE:
                from ..engine.play_mode import PlaySession, CameraMode
                mode_map = {
                    "third_person": CameraMode.THIRD_PERSON,
                    "first_person": CameraMode.FIRST_PERSON,
                    "free_orbit":   CameraMode.FREE_ORBIT,
                    "overhead":     CameraMode.OVERHEAD,
                }
                cam_mode = mode_map.get(camera_mode, CameraMode.THIRD_PERSON)
                self._play_session = PlaySession()
                self._play_session.start(
                    walkmesh=walkmesh,
                    entities=self._entity_registry,
                    start_pos=(start_x, start_y, start_z),
                    start_yaw=math.radians(self.camera.yaw),
                    camera_mode=cam_mode,
                )
            else:
                # Legacy fallback
                walk_tris = self._collect_walkmesh_triangles()
                self._play_session = PlaySession.start(
                    git_data=git, walkmesh_triangles=walk_tris)

            self._play_mode = True
            self._play_pitch = 0.
            self._play_last_time = time.time()
            if _HAS_QT:
                self.setMouseTracking(True)
                self.grabMouse()
                self.setCursor(Qt.BlankCursor)
            self._move_timer.setInterval(16)
            if not self._move_timer.isActive():
                self._move_timer.start()
            self.play_mode_changed.emit(True)
            log.info(f"PlayMode started: {camera_mode}, pos=({start_x:.1f}, {start_y:.1f})")
        except Exception as e:
            log.error(f"start_play_mode: {e}")

    def stop_play_mode(self):
        if not self._play_mode: return
        try:
            if self._play_session:
                self._play_session.stop()
            self._play_session = None
            self._npc_registry = None
            self._play_mode    = False
            self.releaseMouse()
            self.setCursor(Qt.ArrowCursor)
            self.setMouseTracking(False)
            self._move_timer.stop()
            self.play_mode_changed.emit(False)
        except Exception as e:
            log.debug(f"stop_play_mode: {e}")

    def _collect_walkmesh_triangles(self):
        try:
            state = get_module_state()
            tris = getattr(state, 'wok_triangles', None)
            if tris: return tris
        except Exception as e:
            log.debug(f"_collect_walkmesh_triangles: {e}")
        return []

    # ── Mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        self._last_mouse = event.pos()
        if self._play_mode:
            return
        if event.button() == Qt.LeftButton:
            # ── Walkmesh edit mode: ray-cast against walkmesh triangles ──────
            # When walkmesh_edit_mode is active the left-click fires a
            # Möller-Trumbore ray against self._walk_tris and emits
            # walkmesh_face_selected(face_index, t) so face-paint panels can
            # react.  Normal object picking is skipped.
            # Reference: Ericson §5.3.6; Phase 2.1 roadmap.
            if self._walkmesh_edit_mode and self._walk_tris:
                result = self._pick_walkmesh_face(event.x(), event.y())
                if result is not None:
                    face_idx, t = result
                    self._selected_face_idx = face_idx
                    self.walkmesh_face_selected.emit(face_idx, t)
                    self.update()
                return

            if self._selected_obj is not None and not self._placement_mode:
                axis = self._hit_gizmo(event.x(), event.y())
                if axis is not None:
                    self._gizmo_axis = axis
                    self._gizmo_drag_start_mouse = event.pos()
                    obj = self._selected_obj
                    if hasattr(obj, 'position'):
                        from ..formats.gff_types import Vector3
                        p = obj.position
                        self._gizmo_drag_start_pos = Vector3(p.x, p.y, p.z)
                    if hasattr(obj, 'bearing'):
                        self._gizmo_drag_start_rot = getattr(obj,'bearing',0.)
                    return
            if self._placement_mode:
                pos = self._ray_ground_intersect(event.x(), event.y())
                if pos:
                    self._place_object_at(pos)
            else:
                obj = self._pick_object(event.x(), event.y())
                self._selected_obj = obj
                self._gizmo_axis   = None
                self._rebuild_object_vaos()
                self.object_selected.emit(obj)

    def mouseMoveEvent(self, event):
        if self._play_mode and self._play_session:
            cx, cy = self.width()//2, self.height()//2
            if hasattr(self, '_play_mouse_last') and self._play_mouse_last:
                dx = event.x() - self._play_mouse_last.x()
                dy = event.y() - self._play_mouse_last.y()
                if _HAS_NEW_ENGINE and hasattr(self._play_session, 'controller'):
                    # New engine: update camera yaw/pitch via controller
                    ctrl = self._play_session.controller
                    if ctrl and ctrl.active:
                        ctrl.camera.yaw   -= dx * 0.003
                        ctrl.camera.pitch -= dy * 0.003
                        ctrl.camera.clamp_pitch()
                        if hasattr(ctrl, 'player'):
                            ctrl.player.yaw = ctrl.camera.yaw
                else:
                    # Legacy
                    if hasattr(self._play_session, 'player'):
                        self._play_session.player.yaw -= dx * 0.20
                    self._play_pitch = max(-80., min(80., self._play_pitch - dy * 0.20))
            self._play_mouse_last = QPoint(cx, cy)
            QCursor.setPos(self.mapToGlobal(QPoint(cx, cy)))
            self.update()
            return

        if (self._gizmo_axis is not None and
                self._gizmo_drag_start_mouse is not None and
                self._selected_obj is not None and
                hasattr(self._selected_obj,'position') and
                self._gizmo_drag_start_pos is not None):
            self._handle_gizmo_drag(event)
            return

        if self._selected_obj is not None and not self._placement_mode:
            old = self._gizmo_hover
            self._gizmo_hover = self._hit_gizmo(event.x(), event.y())
            if self._gizmo_hover != old:
                self.setCursor(Qt.SizeAllCursor if self._gizmo_hover is not None
                               else Qt.ArrowCursor)
                self.update()

        if self._last_mouse is None:
            self._last_mouse = event.pos(); return
        dx = event.x()-self._last_mouse.x()
        dy = event.y()-self._last_mouse.y()
        self._last_mouse = event.pos()
        if event.buttons() & Qt.RightButton:
            # Orbit sensitivity: 0.35°/px feels natural (UE5 default ~0.3)
            self.camera.orbit(-dx * 0.35, -dy * 0.35)
        elif event.buttons() & Qt.MiddleButton:
            self.camera.pan(dx, dy)
        elif (event.buttons() & Qt.LeftButton) and (event.modifiers() & Qt.AltModifier):
            # Alt+LMB = orbit (Maya-style, also supported in UE5)
            self.camera.orbit(-dx * 0.35, -dy * 0.35)
        t = self.camera.target
        self.camera_moved.emit(float(t[0]), float(t[1]), float(t[2]))
        self.update()

    def _handle_gizmo_drag(self, event):
        obj   = self._selected_obj
        axis  = self._gizmo_axis
        sm    = self._gizmo_drag_start_mouse
        sp    = self._gizmo_drag_start_pos
        total_dx = event.x()-sm.x()
        total_dy = event.y()-sm.y()

        if axis == _AX_R:
            raw = total_dx*1.5
            if self._snap_enabled:
                snap_deg = 45. if self._snap_size>=1. else 15.
                raw = round(raw/snap_deg)*snap_deg
            if hasattr(obj,'bearing'):
                obj.bearing = (self._gizmo_drag_start_rot+raw) % 360.
            self._rebuild_object_vaos(); self.update(); return

        ox, oy = self._world_to_screen(sp.x, sp.y, sp.z)
        if ox is None: return
        world_axes = {_AX_X:(1.,0.,0.), _AX_Y:(0.,1.,0.), _AX_Z:(0.,0.,1.)}
        wx,wy,wz = world_axes[axis]
        tx,ty = self._world_to_screen(sp.x+wx, sp.y+wy, sp.z+wz)
        if tx is None: return
        sdx = tx-ox; sdy = ty-oy
        slen = math.hypot(sdx,sdy) or 1.
        dot = (total_dx*sdx+total_dy*sdy)/slen
        delta = dot/max(slen, 0.1)
        nx = sp.x+wx*delta; ny = sp.y+wy*delta; nz = sp.z+wz*delta
        if self._snap_enabled:
            if wx: nx = self._apply_snap(nx)
            if wy: ny = self._apply_snap(ny)
            if wz: nz = self._apply_snap(nz)
        obj.position.x, obj.position.y, obj.position.z = nx, ny, nz
        self._rebuild_object_vaos(); self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._gizmo_axis is not None:
            self._gizmo_axis = None
            self._gizmo_drag_start_mouse = None
            self._gizmo_drag_start_pos   = None
            self._gizmo_hover = None
            self.setCursor(Qt.ArrowCursor)
            self.update()

    def wheelEvent(self, event):
        if self._play_mode: return
        # Smooth exponential zoom — angleDelta() returns 120 per scroll notch
        delta = event.angleDelta().y() / 120.0
        self.camera.zoom(delta)
        self.update()

    # ── Keyboard events ───────────────────────────────────────────────────────

    def _update_snap(self):
        ctrl  = Qt.Key_Control in self._keys
        shift = Qt.Key_Shift   in self._keys
        if ctrl and shift:   self._snap_enabled=True;  self._snap_size=SNAP_HALF
        elif ctrl:           self._snap_enabled=True;  self._snap_size=SNAP_UNIT
        elif shift:          self._snap_enabled=True;  self._snap_size=SNAP_FINE
        else:                self._snap_enabled=False; self._snap_size=SNAP_UNIT
        self.update()

    def keyPressEvent(self, event):
        self._keys.add(event.key())
        if self._play_mode:
            if event.key() == Qt.Key_Escape:
                self.stop_play_mode()
            elif event.key() in (Qt.Key_Return, Qt.Key_Space):
                # E/Space/Enter = interact in play mode
                if _HAS_NEW_ENGINE and self._play_session:
                    try:
                        self._play_session.controller.interact()
                    except Exception:
                        pass
            elif event.key() == Qt.Key_F:
                # F = toggle fly mode in play mode
                if _HAS_NEW_ENGINE and self._play_session:
                    try:
                        ctrl = self._play_session.controller
                        if ctrl:
                            ctrl.player.fly_mode = not ctrl.player.fly_mode
                    except Exception:
                        pass
            elif event.key() == Qt.Key_C:
                # C = cycle camera mode
                if _HAS_NEW_ENGINE and self._play_session:
                    try:
                        ctrl = self._play_session.controller
                        from ..engine.play_mode import CameraMode
                        modes = [CameraMode.THIRD_PERSON, CameraMode.FIRST_PERSON,
                                 CameraMode.OVERHEAD]
                        current = ctrl.camera_mode
                        idx = modes.index(current) if current in modes else 0
                        new_mode = modes[(idx + 1) % len(modes)]
                        ctrl.set_camera_mode(new_mode)
                    except Exception:
                        pass
            if not self._move_timer.isActive():
                self._move_timer.start()
            return
        if event.key() in (Qt.Key_Control, Qt.Key_Shift):
            self._update_snap()

        # ── UE5-style viewport hotkeys ──────────────────────────────────────
        if event.key() == Qt.Key_F:
            # F = frame selected / frame all
            if self._selected_obj is not None:
                self.frame_selected()
            else:
                if self._room_instances:
                    self._frame_rooms()
                else:
                    self._frame_all()
        elif event.key() == Qt.Key_Home:
            # Home = frame all rooms
            if self._room_instances:
                self._frame_rooms()
            else:
                self._frame_all()
        elif event.key() == Qt.Key_W and not (Qt.Key_Control in self._keys):
            # W alone toggles walkmesh; but not during movement
            if not self._keys.intersection({Qt.Key_A, Qt.Key_S, Qt.Key_D,
                                            Qt.Key_Q, Qt.Key_E}):
                self.toggle_walkmesh()
        elif event.key() == Qt.Key_G:
            # G = toggle grid (UE5 style)
            try:
                self._renderer._show_grid = not getattr(self._renderer, '_show_grid', True)
                self.update()
            except Exception:
                pass
        elif event.key() == Qt.Key_QuoteLeft:
            # ` / ~ = toggle engine stats overlay
            self._show_stats = not getattr(self, '_show_stats', False)
            self.update()
        elif event.key() == Qt.Key_Delete:
            self._delete_selected()
        elif event.key() == Qt.Key_Escape:
            self._placement_mode = False
            self._gizmo_axis = None
            self._gizmo_drag_start_mouse = None
            self._gizmo_drag_start_pos   = None
            self.setCursor(Qt.ArrowCursor)
            self.update()
        elif event.key() == Qt.Key_1:
            # 1 = Front view (looking North)
            self.camera.yaw = 90.0
            self.camera.pitch = 0.0
            self.update()
        elif event.key() == Qt.Key_3:
            # 3 = Right side view
            self.camera.yaw = 0.0
            self.camera.pitch = 0.0
            self.update()
        elif event.key() == Qt.Key_7:
            # 7 = Top-down view
            self.camera.pitch = 89.9
            self.update()
        elif event.key() == Qt.Key_4 and event.modifiers() & Qt.KeypadModifier:
            # Numpad 4 = orbit left
            self.camera.orbit(15.0, 0.0)
            self.update()
        elif event.key() == Qt.Key_6 and event.modifiers() & Qt.KeypadModifier:
            # Numpad 6 = orbit right
            self.camera.orbit(-15.0, 0.0)
            self.update()
        elif event.key() == Qt.Key_8 and event.modifiers() & Qt.KeypadModifier:
            # Numpad 8 = orbit up
            self.camera.orbit(0.0, 15.0)
            self.update()
        elif event.key() == Qt.Key_2 and event.modifiers() & Qt.KeypadModifier:
            # Numpad 2 = orbit down
            self.camera.orbit(0.0, -15.0)
            self.update()

        if not self._move_timer.isActive():
            self._move_timer.start()

    def keyReleaseEvent(self, event):
        self._keys.discard(event.key())
        if event.key() in (Qt.Key_Control, Qt.Key_Shift):
            self._update_snap()
        if not self._play_mode and not self._keys:
            self._move_timer.stop()

    def _process_movement(self):
        if self._play_mode and self._play_session:
            now = time.time()
            dt  = min(now - self._play_last_time, 0.1)
            self._play_last_time = now

            fwd = right = up = turn = pitch_inp = 0.
            sprint = False

            if _HAS_QT:
                if Qt.Key_W in self._keys: fwd   += 1.
                if Qt.Key_S in self._keys: fwd   -= 1.
                if Qt.Key_A in self._keys: right -= 1.
                if Qt.Key_D in self._keys: right += 1.
                if Qt.Key_Q in self._keys: up    -= 1.
                if Qt.Key_E in self._keys: up    += 1.
                sprint = Qt.Key_Shift in self._keys
                interact = Qt.Key_Return in self._keys or Qt.Key_Space in self._keys

            if _HAS_NEW_ENGINE and hasattr(self._play_session, 'update'):
                # New PlaySession API
                self._play_session.update(
                    forward=fwd, right=right, up=up,
                    turn=turn, pitch=pitch_inp,
                    sprint=sprint, delta=dt,
                )
            else:
                # Legacy API
                self._play_session.update(dt, {
                    "move_forward": fwd, "move_right": right,
                    "turn_left": -turn,
                    "running": sprint,
                })

            # Update animation system each frame
            if _HAS_NEW_ENGINE and self._anim_set:
                self._anim_set.update_all(dt)
            if _HAS_NEW_ENGINE and self._entity_registry:
                self._entity_registry.update_all(dt)

            # Notify animation panel (and any other subscriber) of the frame advance
            try:
                self.frame_advanced.emit(dt)
            except Exception:
                pass

            self.update()
            return

        # ── Editor orbit camera: advance animations even in edit mode ────────
        now = time.time()
        dt  = min(now - self._last_engine_time, 0.1)
        self._last_engine_time = now
        self._engine_delta = dt

        # Update entity animations in editor mode too (for preview)
        if _HAS_NEW_ENGINE and self._anim_set:
            try:
                self._anim_set.update_all(dt)
            except Exception:
                pass
        if _HAS_NEW_ENGINE and self._entity_registry:
            try:
                self._entity_registry.update_all(dt)
            except Exception:
                pass

        # Notify animation panel of frame advance (editor mode)
        try:
            self.frame_advanced.emit(dt)
        except Exception:
            pass

        # ── Editor fly-through (WASD + QE) ───────────────────────────────────
        speed = max(0.05, min(2.0, self.camera.distance * 0.035))
        if _HAS_QT:
            if Qt.Key_Shift in self._keys:
                speed *= 3.0
            elif Qt.Key_Control in self._keys:
                speed *= 0.25

        moved = False
        if _HAS_QT:
            if Qt.Key_W in self._keys:
                self.camera.walk(speed, 0., 0.); moved = True
            if Qt.Key_S in self._keys:
                self.camera.walk(-speed, 0., 0.); moved = True
            if Qt.Key_A in self._keys:
                self.camera.walk(0., -speed, 0.); moved = True
            if Qt.Key_D in self._keys:
                self.camera.walk(0., speed, 0.); moved = True
            if Qt.Key_Q in self._keys:
                self.camera.walk(0., 0., -speed); moved = True
            if Qt.Key_E in self._keys:
                self.camera.walk(0., 0., speed); moved = True
        if moved:
            t = self.camera.target
            self.camera_moved.emit(float(t[0]),float(t[1]),float(t[2]))
            self.update()

    # ── Object actions ────────────────────────────────────────────────────────

    def _place_object_at(self, pos):
        try:
            from ..core.module_state import get_module_state, PlaceObjectCommand
            from ..formats.gff_types import (
                GITPlaceable, GITCreature, GITDoor, GITWaypoint,
                GITTrigger, GITSoundObject, GITStoreObject)
            state = get_module_state()
            if not state.git: return
            resref = (self._place_template or "obj_default")[:16]
            cls = {"placeable":GITPlaceable,"creature":GITCreature,
                   "door":GITDoor,"waypoint":GITWaypoint,"trigger":GITTrigger,
                   "sound":GITSoundObject,"store":GITStoreObject
                   }.get(getattr(self,'_place_asset_type','placeable'), GITPlaceable)
            obj = cls()
            obj.resref = obj.template_resref = obj.tag = resref
            obj.position = pos
            state.execute(PlaceObjectCommand(state.git, obj))
            self.object_placed.emit(obj)
        except Exception as e:
            log.debug(f"place: {e}")

    def _delete_selected(self):
        if self._selected_obj is None: return
        try:
            from ..core.module_state import get_module_state, DeleteObjectCommand
            state = get_module_state()
            if not state.git: return
            state.execute(DeleteObjectCommand(state.git, self._selected_obj))
            self._selected_obj = None
            self.object_selected.emit(None)
        except Exception as e:
            log.debug(f"delete: {e}")
