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
    from PyQt5.QtWidgets import QWidget, QSizePolicy
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPoint
    from PyQt5.QtGui import (
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
    class pyqtSignal:  # type: ignore
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
    from ..engine.player_controller import PlaySession
    from ..engine.npc_instance import NPCRegistry
    _HAS_ENGINE = True
except ImportError:
    _HAS_ENGINE = False

try:
    from ..formats.mdl_parser import get_model_cache
    _HAS_MDL = True
except ImportError:
    _HAS_MDL = False


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
    """Maya-style orbit camera (Z-up, right-handed — matches KotOR)."""

    def __init__(self):
        self.azimuth   = 45.0
        self.elevation = 30.0
        self.distance  = 15.0
        self.target    = np.array([0., 0., 0.], dtype='f4')
        self.fov       = 60.0
        self._near, self._far = 0.1, 1000.0

    def eye(self) -> "np.ndarray":
        az = math.radians(self.azimuth)
        el = math.radians(self.elevation)
        x  = self.distance * math.cos(el) * math.cos(az)
        y  = self.distance * math.cos(el) * math.sin(az)
        z  = self.distance * math.sin(el)
        return self.target + np.array([x, y, z], dtype='f4')

    def view_matrix(self) -> "np.ndarray":
        return _look_at(self.eye(), self.target,
                        np.array([0., 0., 1.], dtype='f4'))

    def projection_matrix(self, aspect: float) -> "np.ndarray":
        return _perspective(self.fov, aspect, self._near, self._far)

    def orbit(self, d_az: float, d_el: float):
        self.azimuth = (self.azimuth + d_az) % 360.
        self.elevation = max(-85., min(85., self.elevation + d_el))

    def zoom(self, delta: float):
        self.distance = max(0.5, self.distance * (0.9 ** delta))

    def pan(self, dx: float, dy: float):
        az  = math.radians(self.azimuth)
        right = np.array([-math.sin(az), math.cos(az), 0.], dtype='f4')
        fwd = self.target - self.eye()
        fwd_len = np.linalg.norm(fwd)
        if fwd_len < 1e-9:
            return
        fwd /= fwd_len
        up = np.cross(right, fwd)
        up_len = np.linalg.norm(up)
        if up_len < 1e-9:
            up = np.array([0., 1., 0.], dtype='f4')
        else:
            up /= up_len
        scale = self.distance * 0.002
        self.target += right * dx * scale
        self.target -= up    * dy * scale

    def frame(self, center: "np.ndarray", radius: float):
        self.target = center.copy()
        self.distance = max(1., radius * 2.5)

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
#  GLSL Shaders
# ═════════════════════════════════════════════════════════════════════════════

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

_VERT_LIT = """
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
    v_normal = normalize(mat3(model) * in_normal);
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_FRAG_LIT = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
out vec4 fragColor;
uniform vec3 diffuse_color;
uniform vec3 light_dir;
uniform float ambient;
void main() {
    vec3 n = normalize(v_normal);
    // Key light
    float diff = max(dot(n, normalize(light_dir)), 0.0);
    // Two-sided: dim backfaces rather than discard
    float back = max(dot(-n, normalize(light_dir)), 0.0) * 0.3;
    // Rim fill light from below (bounce)
    float fill = max(dot(n, vec3(0.0, 0.0, -1.0)), 0.0) * 0.12;
    vec3 col = diffuse_color * (ambient + (diff + back + fill) * (1.0 - ambient));
    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""

# Uniform-colour + alpha (walkmesh, selection highlight overlay)
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


def _grid_verts(n: int = 20, step: float = 1.0) -> "np.ndarray":
    """N×N ground grid on the Z=0 plane."""
    v = []
    half = n * step * 0.5
    for i in range(-n, n + 1):
        x = i * step
        br = 0.35 if (i % 5 == 0) else 0.16
        v.extend([-half, x, 0, br, br, br + .08,
                   half, x, 0, br, br, br + .08])
        v.extend([x, -half, 0, br, br, br + .08,
                   x,  half, 0, br, br, br + .08])
    v.extend([0, -half, 0, .55,.18,.18,  half*2,-half, 0, .55,.18,.18])
    v.extend([-half, 0, 0, .18,.55,.18, -half, half*2, 0, .18,.55,.18])
    return np.array(v, dtype='f4')


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

class _EGLRenderer:
    """
    Manages the ModernGL EGL context, shaders, VAOs and FBO.
    Lives inside ViewportWidget and is created lazily on first resize/paint.
    """

    def __init__(self):
        self.ctx = None          # moderngl.Context
        self._prog_flat    = None   # colour-per-vertex shader
        self._prog_lit     = None   # Phong-lit shader
        self._prog_uniform = None   # uniform colour + alpha (overlay)
        self._fbo = None         # current FBO
        self._fbo_size = (0, 0)  # (W, H) of current FBO
        self._grid_vao   = None
        self._grid_count = 0
        self._object_vaos: List[dict] = []
        self._room_vaos: List[dict]   = []
        self._walk_vaos: List[dict]   = []   # walkable tris (green overlay)
        self._nowalk_vaos: List[dict] = []   # non-walkable tris (red overlay)
        self._mdl_vaos: List[dict]    = []   # play-mode only
        self._show_walkmesh = True
        self.ready = False

    # ── Initialisation ────────────────────────────────────────────────────────

    def init(self) -> bool:
        """
        Create a ModernGL standalone context + compile shaders.
        Tries multiple backends in order:
          1. EGL (Linux/headless — surfaceless, no display required)
          2. Default (Windows WGL / macOS CGL — uses whatever is available)
        Returns True on success.
        """
        if self.ready:
            return True
        if not _HAS_MODERNGL:
            log.warning("moderngl not installed — 3D rendering unavailable")
            return False

        ctx = None
        backend_used = "none"

        # ── Attempt 1: EGL (Linux headless) ──────────────────────────────────
        if os.name == "posix":
            try:
                import moderngl
                os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
                os.environ.setdefault("MESA_GL_VERSION_OVERRIDE", "3.3")
                os.environ.setdefault("MESA_GLSL_VERSION_OVERRIDE", "330")
                os.environ.setdefault("EGL_PLATFORM", "surfaceless")
                ctx = moderngl.create_standalone_context(backend="egl")
                backend_used = "egl"
                log.debug("GL: EGL backend initialised")
            except Exception as e:
                log.debug(f"EGL init failed ({e}), trying default backend")
                ctx = None

        # ── Attempt 2: Default backend (Windows WGL / macOS / fallback) ─────
        if ctx is None:
            try:
                import moderngl
                ctx = moderngl.create_standalone_context()
                backend_used = "default"
                log.debug("GL: default backend initialised")
            except Exception as e:
                log.error(f"GL default backend failed: {e}")
                return False

        try:
            import moderngl
            self.ctx = ctx
            self.ctx.enable(moderngl.DEPTH_TEST)
            self.ctx.enable(moderngl.CULL_FACE)
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self._prog_flat = self.ctx.program(
                vertex_shader=_VERT_FLAT, fragment_shader=_FRAG_FLAT)
            try:
                self._prog_lit = self.ctx.program(
                    vertex_shader=_VERT_LIT, fragment_shader=_FRAG_LIT)
            except Exception as e:
                log.warning(f"Lit shader failed, using flat fallback: {e}")
                self._prog_lit = None
            try:
                self._prog_uniform = self.ctx.program(
                    vertex_shader=_VERT_UNIFORM, fragment_shader=_FRAG_UNIFORM)
            except Exception as e:
                log.warning(f"Uniform shader failed: {e}")
                self._prog_uniform = None
            self._build_grid()
            self.ready = True
            info = self.ctx.info
            global _GL_BACKEND
            _GL_BACKEND = backend_used
            log.info(f"GL renderer ready [{backend_used}]: {info['GL_RENDERER']} "
                     f"({info['GL_VERSION']})")
            return True
        except Exception as e:
            log.error(f"GL shader/grid init failed: {e}")
            try:
                self.ctx.release()
            except Exception:
                pass
            self.ctx = None
            return False

    def ensure_fbo(self, W: int, H: int):
        """Resize FBO if dimensions changed."""
        if not self.ctx:
            return
        W, H = max(W, 1), max(H, 1)
        if (W, H) == self._fbo_size:
            return
        if self._fbo:
            try:
                self._fbo.release()
            except Exception:
                pass
        self._fbo = self.ctx.simple_framebuffer((W, H), components=4)
        self._fbo_size = (W, H)

    # ── Grid ──────────────────────────────────────────────────────────────────

    def _build_grid(self):
        verts = _grid_verts(n=20, step=1.0)
        vbo = self.ctx.buffer(verts.tobytes())
        self._grid_vao = self.ctx.vertex_array(
            self._prog_flat, [(vbo, "3f 3f", "in_position", "in_color")])
        self._grid_count = len(verts) // 6

    # ── VAO helpers ───────────────────────────────────────────────────────────

    def _upload_flat(self, verts: "np.ndarray") -> dict:
        vbo = self.ctx.buffer(verts.tobytes())
        vao = self.ctx.vertex_array(
            self._prog_flat, [(vbo, "3f 3f", "in_position", "in_color")])
        return {"vao": vao, "vbo": vbo, "count": len(verts) // 6}

    def _upload_positions_only(self, positions: list) -> Optional[dict]:
        """Upload raw position-only triangles for uniform-colour overlay."""
        if not positions or not self._prog_uniform:
            return None
        arr = np.array(positions, dtype='f4').flatten()
        vbo = self.ctx.buffer(arr.tobytes())
        vao = self.ctx.vertex_array(
            self._prog_uniform, [(vbo, "3f", "in_position")])
        return {"vao": vao, "vbo": vbo, "count": len(positions)}

    def _upload_lit_or_flat(self, positions, normals, color: tuple) -> Optional[dict]:
        """Upload mesh triangles with normals (lit) or baked colour (flat)."""
        if not positions:
            return None
        r, g, b = color
        if self._prog_lit and normals:
            v = []
            for (px,py,pz),(nx,ny,nz) in zip(positions, normals):
                v.extend([px,py,pz,nx,ny,nz])
            arr = np.array(v, dtype='f4')
            vbo = self.ctx.buffer(arr.tobytes())
            vao = self.ctx.vertex_array(
                self._prog_lit, [(vbo, "3f 3f", "in_position", "in_normal")])
            return {"vao": vao, "vbo": vbo, "count": len(v)//6,
                    "lit": True, "color": color}
        else:
            v = []
            for (px,py,pz) in positions:
                v.extend([px,py,pz, r,g,b])
            arr = np.array(v, dtype='f4')
            vbo = self.ctx.buffer(arr.tobytes())
            vao = self.ctx.vertex_array(
                self._prog_flat, [(vbo, "3f 3f", "in_position", "in_color")])
            return {"vao": vao, "vbo": vbo, "count": len(v)//6,
                    "lit": False, "color": color}

    def _release_list(self, lst: list):
        for e in lst:
            try: e["vbo"].release()
            except Exception: pass
            try: e["vao"].release()
            except Exception: pass
        lst.clear()

    # ── Object VAOs ───────────────────────────────────────────────────────────

    def rebuild_object_vaos(self, state, selected_obj):
        self._release_list(self._object_vaos)
        if not self.ctx or not state or not state.git:
            return

        def _add(obj, hw, hh, hd, base_color):
            col = _COLOR_SELECTED if (obj is selected_obj) else base_color
            verts = _box_solid(obj.position.x, obj.position.y,
                               obj.position.z, hw, hh, hd, col)
            e = self._upload_flat(verts)
            e["obj"] = obj
            self._object_vaos.append(e)

        for p in state.git.placeables: _add(p, .30,.30,.30, _COLOR_PLACEABLE)
        for c in state.git.creatures:  _add(c, .35,.35,.70, _COLOR_CREATURE)
        for d in state.git.doors:      _add(d, .50,.15,.90, _COLOR_DOOR)
        for w in state.git.waypoints:  _add(w, .15,.15,.50, _COLOR_WAYPOINT)
        for t in state.git.triggers:   _add(t, .50,.50,.05, _COLOR_TRIGGER)
        for s in state.git.sounds:     _add(s, .20,.20,.20, _COLOR_SOUND)
        for st in state.git.stores:    _add(st,.30,.30,.40, _COLOR_STORE)

    def rebuild_walkmesh_vaos(self, walk_tris: list, nowalk_tris: list):
        """
        Upload walkmesh triangles as position-only geometry for overlay.
        walk_tris  : list of ((x1,y1,z1),(x2,y2,z2),(x3,y3,z3)) walkable
        nowalk_tris: list of ((x1,y1,z1),(x2,y2,z2),(x3,y3,z3)) blocked
        """
        self._release_list(self._walk_vaos)
        self._release_list(self._nowalk_vaos)
        if not self.ctx or not self._prog_uniform:
            return

        def _tris_to_pos(tris):
            pts = []
            for tri in tris:
                for v in tri:
                    pts.append(list(v))
            return pts

        walk_pos   = _tris_to_pos(walk_tris)
        nowalk_pos = _tris_to_pos(nowalk_tris)

        if walk_pos:
            e = self._upload_positions_only(walk_pos)
            if e:
                self._walk_vaos.append(e)
        if nowalk_pos:
            e = self._upload_positions_only(nowalk_pos)
            if e:
                self._nowalk_vaos.append(e)

        log.debug(f"Walkmesh VAOs: {len(walk_pos)//3} walk, "
                  f"{len(nowalk_pos)//3} no-walk triangles")

    # ── Room VAOs ─────────────────────────────────────────────────────────────

    def rebuild_room_vaos(self, room_instances: list, game_dir: str):
        self._release_list(self._room_vaos)
        if not self.ctx:
            return

        PALETTE = [
            (0.45, 0.42, 0.38), (0.38, 0.42, 0.45),
            (0.42, 0.45, 0.38), (0.45, 0.38, 0.42),
        ]

        import moderngl
        for idx, ri in enumerate(room_instances):
            name   = getattr(ri, 'model_name', None) or getattr(ri, 'name', f'room{idx}')
            tx     = float(getattr(ri, 'world_x', None) or
                           getattr(ri, 'x', None) or
                           (getattr(ri, 'grid_x', 0) * 10.0) or 0.0)
            ty     = float(getattr(ri, 'world_y', None) or
                           getattr(ri, 'y', None) or
                           (getattr(ri, 'grid_y', 0) * 10.0) or 0.0)
            tz     = float(getattr(ri, 'world_z', None) or
                           getattr(ri, 'z', 0.0) or 0.0)
            color  = PALETTE[idx % len(PALETTE)]
            mdl_path = getattr(ri, 'mdl_path', '') or ''

            # Try to load actual MDL geometry
            loaded = False
            if not mdl_path and game_dir:
                for candidate in [
                    os.path.join(game_dir, 'models', name.lower() + '.mdl'),
                    os.path.join(game_dir, name.lower() + '.mdl'),
                ]:
                    if os.path.exists(candidate):
                        mdl_path = candidate
                        break

            if mdl_path and _HAS_MDL:
                try:
                    mesh = get_model_cache().load(mdl_path)
                    if mesh:
                        nodes = (list(mesh.visible_mesh_nodes())
                                 if hasattr(mesh, 'visible_mesh_nodes')
                                 else mesh.mesh_nodes())
                        for node in nodes:
                            verts_raw  = getattr(node, 'vertices', [])
                            faces_raw  = getattr(node, 'faces', [])
                            norms_raw  = getattr(node, 'normals', [])
                            if not verts_raw or not faces_raw:
                                continue
                            has_n = len(norms_raw) == len(verts_raw)
                            positions, normals = [], []
                            for f in faces_raw:
                                if max(f) >= len(verts_raw):
                                    continue
                                for vi in f:
                                    positions.append(verts_raw[vi])
                                    normals.append(norms_raw[vi] if has_n
                                                   else (0.,0.,1.))
                            e = self._upload_lit_or_flat(positions, normals, color)
                            if e:
                                e.update({"name": name, "tx": tx, "ty": ty, "tz": tz})
                                self._room_vaos.append(e)
                                loaded = True
                except Exception as exc:
                    log.debug(f"room MDL load error ({name}): {exc}")

            if not loaded:
                # Placeholder box: 10×10×4 wu centred at world origin of room
                w, h = 10.0, 10.0
                verts = _box_wire(tx + w/2, ty + h/2, tz,
                                  w/2, h/2, 2.0, color)
                e = self._upload_flat(verts)
                # tx/ty/tz=0 because world coords are baked into the vertices
                e.update({"name": name, "tx": 0.0, "ty": 0.0, "tz": 0.0,
                          "primitive": "lines"})
                self._room_vaos.append(e)
                log.debug(f"Room '{name}' @ ({tx:.0f},{ty:.0f}) — placeholder box")

        log.info(f"Room VAOs: {len(self._room_vaos)} from {len(room_instances)} rooms")

    # ── Render frame ──────────────────────────────────────────────────────────

    def render(self, W: int, H: int, camera: OrbitCamera,
               play_session=None, show_walkmesh: bool = True) -> Optional[bytes]:
        """
        Render one frame into the FBO and return the raw RGBA pixel bytes
        (bottom-row first, i.e. OpenGL convention — caller must flip).
        Returns None if not ready.
        """
        if not self.ctx or not self._prog_flat:
            return None
        import moderngl

        self.ensure_fbo(W, H)
        if not self._fbo:
            return None

        self._fbo.use()
        self.ctx.viewport = (0, 0, W, H)
        # Deep navy-blue background
        self.ctx.clear(0.07, 0.08, 0.14, 1.0)

        aspect = W / max(H, 1)
        if play_session:
            proj = camera.projection_matrix(aspect)
            try:
                eye  = play_session.player_eye
                look = play_session.player.look_at_target(0.0)
            except Exception:
                eye  = camera.eye().tolist()
                look = camera.target.tolist()
            view = _look_at(
                np.array(eye,  dtype='f4'),
                np.array(look, dtype='f4'),
                np.array([0., 0., 1.], dtype='f4'),
            )
        else:
            proj = camera.projection_matrix(aspect)
            view = camera.view_matrix()

        vp = proj @ view

        # ── Ground grid ───────────────────────────────────────────────────────
        if self._grid_vao:
            self._prog_flat["mvp"].write(vp.T.astype('f4').tobytes())
            self._grid_vao.render(moderngl.LINES, vertices=self._grid_count)

        # ── Room geometry ─────────────────────────────────────────────────────
        # Disable back-face culling so interior faces show
        self.ctx.disable(moderngl.CULL_FACE)
        light_dir = np.array([0.6, 0.4, 0.8], dtype='f4')
        light_dir = light_dir / np.linalg.norm(light_dir)
        for e in self._room_vaos:
            vao, count = e["vao"], e["count"]
            if not vao or count == 0:
                continue
            tx, ty, tz = e.get("tx", 0.), e.get("ty", 0.), e.get("tz", 0.)
            model_m = _translation(tx, ty, tz)
            mvp_m   = proj @ view @ model_m
            color   = e.get("color", (.55,.52,.48))
            primitive_hint = e.get("primitive", "triangles")
            if e.get("lit") and self._prog_lit:
                try:
                    self._prog_lit["mvp"].write(mvp_m.T.astype('f4').tobytes())
                    self._prog_lit["model"].write(model_m.T.astype('f4').tobytes())
                    self._prog_lit["diffuse_color"].write(
                        np.array(color, dtype='f4').tobytes())
                    self._prog_lit["light_dir"].write(light_dir.tobytes())
                    self._prog_lit["ambient"].value = 0.40
                    vao.render(moderngl.TRIANGLES, vertices=count)
                except Exception as ex:
                    log.debug(f"room lit render: {ex}")
            else:
                try:
                    self._prog_flat["mvp"].write(mvp_m.T.astype('f4').tobytes())
                    prim = moderngl.LINES if primitive_hint == "lines" else moderngl.TRIANGLES
                    vao.render(prim, vertices=count)
                except Exception as ex:
                    log.debug(f"room flat render: {ex}")

        self.ctx.enable(moderngl.CULL_FACE)

        # ── Walkmesh overlay (semi-transparent, depth-write off) ──────────────
        if show_walkmesh and self._prog_uniform:
            self.ctx.depth_mask = False
            self.ctx.enable(moderngl.BLEND)
            # Walkable: green
            if self._walk_vaos:
                try:
                    self._prog_uniform["mvp"].write(vp.T.astype('f4').tobytes())
                    self._prog_uniform["u_color"].write(
                        np.array([0.1, 0.9, 0.3, 0.35], dtype='f4').tobytes())
                    for e in self._walk_vaos:
                        if e["vao"] and e["count"]:
                            e["vao"].render(moderngl.TRIANGLES, vertices=e["count"])
                except Exception as ex:
                    log.debug(f"walk overlay: {ex}")
            # Non-walkable: red
            if self._nowalk_vaos:
                try:
                    self._prog_uniform["u_color"].write(
                        np.array([0.9, 0.1, 0.1, 0.30], dtype='f4').tobytes())
                    for e in self._nowalk_vaos:
                        if e["vao"] and e["count"]:
                            e["vao"].render(moderngl.TRIANGLES, vertices=e["count"])
                except Exception as ex:
                    log.debug(f"nowalk overlay: {ex}")
            self.ctx.depth_mask = True

        # ── GIT object boxes ──────────────────────────────────────────────────
        self._prog_flat["mvp"].write(vp.T.astype('f4').tobytes())
        self.ctx.disable(moderngl.CULL_FACE)
        for e in self._object_vaos:
            try:
                e["vao"].render(moderngl.TRIANGLES, vertices=e["count"])
            except Exception:
                pass
        self.ctx.enable(moderngl.CULL_FACE)

        # ── Play-mode MDL models ──────────────────────────────────────────────
        if play_session and self._mdl_vaos and self._prog_lit:
            ident = np.eye(4, dtype='f4')
            try:
                self._prog_lit["mvp"].write(vp.T.astype('f4').tobytes())
                self._prog_lit["model"].write(ident.T.tobytes())
                self._prog_lit["light_dir"].write(light_dir.tobytes())
                self._prog_lit["ambient"].value = 0.3
            except Exception:
                pass
            self.ctx.disable(moderngl.CULL_FACE)
            for e in self._mdl_vaos:
                vao, count, color = e["vao"], e["count"], e.get("color", (.6,.6,.6))
                if vao and count:
                    try:
                        self._prog_lit["diffuse_color"].write(
                            np.array(color, dtype='f4').tobytes())
                        vao.render(moderngl.TRIANGLES, vertices=count)
                    except Exception:
                        pass
            self.ctx.enable(moderngl.CULL_FACE)

        return self._fbo.read(components=4)

    def release(self):
        if not self.ctx:
            return
        self._release_list(self._object_vaos)
        self._release_list(self._room_vaos)
        self._release_list(self._walk_vaos)
        self._release_list(self._nowalk_vaos)
        self._release_list(self._mdl_vaos)
        try: self.ctx.release()
        except Exception: pass
        self.ctx = None
        self.ready = False


# ═════════════════════════════════════════════════════════════════════════════
#  ViewportWidget
# ═════════════════════════════════════════════════════════════════════════════

class ViewportWidget(_QWidget_base):
    """
    ModernGL-powered 3D viewport.

    Uses an EGL offscreen context (no display server required) and blits
    the rendered frame to the Qt widget via QPainter + QImage.
    Standalone — no external tools required.
    """

    # Signals
    object_selected   = pyqtSignal(object)
    object_placed     = pyqtSignal(object)
    camera_moved      = pyqtSignal(float, float, float)
    play_mode_changed = pyqtSignal(bool)

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

        # Play mode
        self._play_mode      = False
        self._play_session   = None
        self._npc_registry   = None
        self._play_last_time = 0.0
        self._play_pitch     = 0.0
        self._game_dir: str  = ""

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

        # Move timer (WASD)
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
        if rooms:
            self._frame_rooms()
        self.update()

    def set_game_dir(self, game_dir: str):
        self._game_dir = game_dir

    def set_app_mode(self, mode: str):
        """Switch between 'module_editor' and 'level_builder' modes."""
        if mode in ("module_editor", "level_builder"):
            self._app_mode = mode
            self.update()

    def toggle_walkmesh(self, visible: bool = None):
        """Show/hide walkmesh overlay."""
        if visible is None:
            self._show_walkmesh = not self._show_walkmesh
        else:
            self._show_walkmesh = bool(visible)
        self.update()

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

        # ── GL render ─────────────────────────────────────────────────────────
        if self._renderer.ready:
            raw = self._renderer.render(
                W, H, self.camera,
                self._play_session if self._play_mode else None,
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

    # ── HUD overlay ───────────────────────────────────────────────────────────

    def _paint_hud(self, p: "QPainter", W: int, H: int):
        """Draw the heads-up display over the 3D viewport."""
        if not _HAS_QT:
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        fn_small = QFont("Consolas", 8)
        fn_badge = QFont("Consolas", 9, QFont.Bold)
        fn_tiny  = QFont("Consolas", 7)

        # ── Mode badge (top-left) ─────────────────────────────────────────────
        if self._play_mode:
            badge_col = QColor(50, 200, 80)
            badge_txt = "▶  PLAY MODE"
        elif self._placement_mode:
            badge_col = QColor(230, 140, 20)
            badge_txt = f"✚  PLACE  [{self._place_asset_type.upper()}]"
        elif self._app_mode == "module_editor":
            badge_col = QColor(100, 160, 240)
            badge_txt = "✏  MODULE EDITOR"
        else:
            badge_col = QColor(80, 200, 180)
            badge_txt = "⬛  LEVEL BUILDER"

        p.setFont(fn_badge)
        fm = p.fontMetrics()
        bw = fm.horizontalAdvance(badge_txt) + 18
        bh = 22
        p.fillRect(8, 8, bw, bh, QColor(0, 0, 0, 160))
        p.setPen(QPen(badge_col, 2))
        p.drawRect(8, 8, bw, bh)
        p.setPen(badge_col)
        p.drawText(17, 8 + bh - 6, badge_txt)

        # ── GL backend / error badge ──────────────────────────────────────────
        if not _HAS_MODERNGL:
            msg = "⚠ moderngl not installed — pip install moderngl"
            p.setFont(fn_small)
            p.setPen(QColor(240, 100, 60))
            p.fillRect(8, 36, W-16, 20, QColor(0, 0, 0, 180))
            p.drawText(12, 51, msg)
        elif _GL_INIT_ERROR:
            p.setFont(fn_tiny)
            p.setPen(QColor(240, 120, 60))
            p.fillRect(8, 36, W-16, 16, QColor(0, 0, 0, 180))
            p.drawText(12, 49, f"⚠ GL: {_GL_INIT_ERROR[:90]}")
        else:
            # Show GL backend in corner
            p.setFont(fn_tiny)
            p.setPen(QColor(80, 80, 100))
            p.drawText(bw + 16, 24, f"[{_GL_BACKEND}]")

        # ── Camera info (bottom-right) ────────────────────────────────────────
        t = self.camera.target
        cam_lines = [
            f"Az {self.camera.azimuth:.0f}°  El {self.camera.elevation:.0f}°  "
            f"Dist {self.camera.distance:.1f}",
            f"Target  X{t[0]:.2f}  Y{t[1]:.2f}  Z{t[2]:.2f}",
        ]
        p.setFont(fn_small)
        fm2 = p.fontMetrics()
        line_h = fm2.height() + 2
        total_h = len(cam_lines) * line_h + 6
        max_w = max(fm2.horizontalAdvance(l) for l in cam_lines) + 12
        rx = W - max_w - 6
        ry = H - total_h - 6
        p.fillRect(rx, ry, max_w, total_h, QColor(0, 0, 0, 150))
        p.setPen(QColor(160, 180, 200))
        for i, line in enumerate(cam_lines):
            p.drawText(rx + 6, ry + 6 + (i+1) * line_h - 2, line)

        # ── Selected object info ──────────────────────────────────────────────
        if self._selected_obj and not self._play_mode:
            obj = self._selected_obj
            otype = type(obj).__name__.replace("GIT", "")
            resref = getattr(obj, "resref", getattr(obj, "template_resref", "?"))
            pos = getattr(obj, "position", None)
            if pos:
                info = (f"{otype}  [{resref}]  "
                        f"@  {pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f}")
            else:
                info = f"{otype}  [{resref}]"
            p.setFont(fn_small)
            fw = p.fontMetrics().horizontalAdvance(info) + 16
            bx = (W - fw) // 2
            p.fillRect(bx, H-32, fw, 20, QColor(0, 0, 0, 180))
            p.setPen(QColor(255, 220, 60))
            p.drawText(bx + 8, H - 16, info)

        # ── Controls hint (bottom-left) ───────────────────────────────────────
        if self._play_mode:
            hint = "WASD=move  Mouse=look  Esc=exit"
        elif self._placement_mode:
            hint = "LMB=place  Esc=cancel"
        else:
            hint = ("RMB=orbit  MMB=pan  Scroll=zoom  "
                    "WASD=fly  F=frame  W=walkmesh  Del=delete")

        p.setFont(fn_tiny)
        p.setPen(QColor(100, 110, 130))
        p.fillRect(0, H-16, W, 16, QColor(0, 0, 0, 100))
        p.drawText(8, H - 4, hint)

        # ── Walkmesh indicator ────────────────────────────────────────────────
        if self._walkmesh_loaded:
            wm_txt = ("WALKMESH ON" if self._show_walkmesh else "WALKMESH OFF")
            wm_col = QColor(80, 220, 120) if self._show_walkmesh else QColor(160, 80, 80)
            p.setFont(fn_tiny)
            p.setPen(wm_col)
            fw2 = p.fontMetrics().horizontalAdvance(wm_txt)
            p.drawText(W - fw2 - 8, H - 20, wm_txt)

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
        self.update()

    # ── Room framing ─────────────────────────────────────────────────────────

    def _frame_rooms(self):
        if not _HAS_NUMPY or not self._room_instances:
            return
        pts = []
        for ri in self._room_instances:
            x = float(getattr(ri,'world_x',None) or getattr(ri,'x',None) or
                      (getattr(ri,'grid_x',0)*10.0) or 0.0)
            y = float(getattr(ri,'world_y',None) or getattr(ri,'y',None) or
                      (getattr(ri,'grid_y',0)*10.0) or 0.0)
            z = float(getattr(ri,'world_z',0.) or getattr(ri,'z',0.) or 0.)
            pts.append([x, y, z])
        arr    = np.array(pts, dtype='f4')
        center = arr.mean(axis=0)
        radius = float(np.linalg.norm(arr-center,axis=1).max()) if len(pts)>1 else 10.
        self.camera.frame(center, max(radius, 5.))

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

    def start_play_mode(self):
        if not _HAS_ENGINE:
            log.warning("Engine unavailable — play mode disabled")
            return
        try:
            state = get_module_state()
            git   = state.git if state else None
            walk_tris = self._collect_walkmesh_triangles()
            self._play_session = PlaySession.start(
                git_data=git, walkmesh_triangles=walk_tris)
            self._npc_registry = NPCRegistry()
            if git:
                self._npc_registry.populate_from_git(git)
            self._play_mode = True
            self._play_pitch = 0.
            self._play_last_time = time.time()
            self.setMouseTracking(True)
            self.grabMouse()
            self.setCursor(Qt.BlankCursor)
            self._move_timer.setInterval(16)
            if not self._move_timer.isActive():
                self._move_timer.start()
            self.play_mode_changed.emit(True)
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
        except Exception: pass
        return []

    # ── Mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        self._last_mouse = event.pos()
        if self._play_mode:
            return
        if event.button() == Qt.LeftButton:
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
                dx = event.x()-self._play_mouse_last.x()
                dy = event.y()-self._play_mouse_last.y()
                self._play_session.player.yaw -= dx*0.20
                self._play_pitch = max(-80., min(80., self._play_pitch-dy*0.20))
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
            self.camera.orbit(-dx*0.4, -dy*0.4)
        elif event.buttons() & Qt.MiddleButton:
            self.camera.pan(dx, dy)
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
        self.camera.zoom(-event.angleDelta().y()/120.)
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
            if not self._move_timer.isActive():
                self._move_timer.start()
            return
        if event.key() in (Qt.Key_Control, Qt.Key_Shift):
            self._update_snap()
        if event.key() == Qt.Key_F:
            self._frame_all()
        elif event.key() == Qt.Key_W and not (Qt.Key_Control in self._keys):
            # W alone toggles walkmesh; Ctrl+W is reserved (would be used for movement)
            if not self._keys.intersection({Qt.Key_A, Qt.Key_S, Qt.Key_D}):
                self.toggle_walkmesh()
        elif event.key() == Qt.Key_Delete:
            self._delete_selected()
        elif event.key() == Qt.Key_Escape:
            self._placement_mode = False
            self._gizmo_axis = None
            self._gizmo_drag_start_mouse = None
            self._gizmo_drag_start_pos   = None
            self.setCursor(Qt.ArrowCursor)
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
            dt  = min(now-self._play_last_time, 0.1)
            self._play_last_time = now
            fwd=right=turn=0.
            if Qt.Key_W in self._keys: fwd   += 1.
            if Qt.Key_S in self._keys: fwd   -= 1.
            if Qt.Key_A in self._keys: turn  += 1.
            if Qt.Key_D in self._keys: turn  -= 1.
            if Qt.Key_Q in self._keys: right -= 1.
            if Qt.Key_E in self._keys: right += 1.
            self._play_session.update(dt, {
                "move_forward": fwd, "move_right": right,
                "turn_left": turn,
                "running": Qt.Key_Shift in self._keys})
            self.update(); return

        speed = max(0.08, min(0.8, self.camera.distance * 0.04))
        # Shift doubles speed
        if Qt.Key_Shift in self._keys:
            speed *= 2.5
        az  = math.radians(self.camera.azimuth)
        fwd   = np.array([math.cos(az), math.sin(az), 0.], dtype='f4')
        right = np.array([-math.sin(az), math.cos(az), 0.], dtype='f4')
        up    = np.array([0., 0., 1.], dtype='f4')
        moved = False
        if Qt.Key_W in self._keys: self.camera.target+=fwd*speed;   moved=True
        if Qt.Key_S in self._keys: self.camera.target-=fwd*speed;   moved=True
        if Qt.Key_A in self._keys: self.camera.target-=right*speed; moved=True
        if Qt.Key_D in self._keys: self.camera.target+=right*speed; moved=True
        if Qt.Key_Q in self._keys: self.camera.target-=up*speed;    moved=True
        if Qt.Key_E in self._keys: self.camera.target+=up*speed;    moved=True
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
