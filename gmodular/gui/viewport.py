"""
GModular — 3D Viewport Widget
PyQt5 + ModernGL OpenGL viewport for module editing.

Features:
- Orbit camera (RMB drag), pan (MMB drag), zoom (scroll)
- WASD first-person movement (when in playtest mode)
- Walkmesh flat ground plane (grid floor)
- GIT object billboards (placeables/creatures/doors as colored boxes)
- Object selection via raycasting
- Object placement via left-click on ground
- Gizmos for selected object (translate handles)
- Coordinate system: Z-up, right-handed (matches KotOR/Odyssey)
"""
from __future__ import annotations
import math
import time
import logging
from typing import Optional, List, Tuple, Callable, Dict

import numpy as np
from PyQt5.QtWidgets import QOpenGLWidget, QSizePolicy
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt5.QtGui import QKeyEvent, QMouseEvent, QWheelEvent, QSurfaceFormat

log = logging.getLogger(__name__)

try:
    import moderngl
    _HAS_MODERNGL = True
    _GL_BACKEND = "moderngl"
except ImportError:
    _HAS_MODERNGL = False
    _GL_BACKEND = "none"
    # Try PyOpenGL as a pure-Python fallback (no C++ compiler needed)
    try:
        import OpenGL.GL as _GL  # noqa: F401
        _GL_BACKEND = "pyopengl"
        log.info("ModernGL not available — using PyOpenGL fallback (pure Python)")
    except ImportError:
        log.warning(
            "Neither ModernGL nor PyOpenGL is available. "
            "The 3D viewport will show a placeholder grid. "
            "Install PyOpenGL:  pip install PyOpenGL"
        )

try:
    from ..formats.gff_types import GITPlaceable, GITCreature, GITDoor, Vector3
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
    from ..formats.mdl_parser import get_model_cache, MeshData
    _HAS_MDL = True
except ImportError:
    _HAS_MDL = False


# ─────────────────────────────────────────────────────────────────────────────
#  Math helpers
# ─────────────────────────────────────────────────────────────────────────────

def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    d = near - far
    return np.array([
        [f / aspect, 0,  0,  0],
        [0,          f,  0,  0],
        [0,          0,  (far + near) / d, (2 * far * near) / d],
        [0,          0, -1,  0],
    ], dtype='f4')


def _look_at(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r_len = np.linalg.norm(r)
    if r_len < 1e-9:
        r = np.array([1.0, 0.0, 0.0], dtype='f4')
    else:
        r /= r_len
    u = np.cross(r, f)
    m = np.eye(4, dtype='f4')
    m[0, :3] = r
    m[1, :3] = u
    m[2, :3] = -f
    m[3, 0] = -np.dot(r, eye)
    m[3, 1] = -np.dot(u, eye)
    m[3, 2] =  np.dot(f, eye)
    # Transpose to get column-major
    return m.T


def _translation(tx: float, ty: float, tz: float) -> np.ndarray:
    m = np.eye(4, dtype='f4')
    m[0, 3] = tx; m[1, 3] = ty; m[2, 3] = tz
    return m


def _scale_mat(s: float) -> np.ndarray:
    m = np.eye(4, dtype='f4')
    m[0, 0] = m[1, 1] = m[2, 2] = s
    return m


def _rot_z(angle_rad: float) -> np.ndarray:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    m = np.eye(4, dtype='f4')
    m[0, 0] =  c; m[0, 1] = -s
    m[1, 0] =  s; m[1, 1] =  c
    return m


# ─────────────────────────────────────────────────────────────────────────────
#  Orbit Camera
# ─────────────────────────────────────────────────────────────────────────────

class OrbitCamera:
    """
    Maya-style orbit camera.
    KotOR / GModular uses Z-up, right-handed coordinate system.
    """

    def __init__(self):
        self.azimuth   = 45.0   # degrees around Z axis
        self.elevation = 30.0   # degrees up from ground plane
        self.distance  = 15.0
        self.target    = np.array([0.0, 0.0, 0.0], dtype='f4')
        self.fov       = 60.0
        self._near     = 0.1
        self._far      = 1000.0

    def eye(self) -> np.ndarray:
        az = math.radians(self.azimuth)
        el = math.radians(self.elevation)
        x  = self.distance * math.cos(el) * math.cos(az)
        y  = self.distance * math.cos(el) * math.sin(az)
        z  = self.distance * math.sin(el)
        return self.target + np.array([x, y, z], dtype='f4')

    def view_matrix(self) -> np.ndarray:
        eye = self.eye()
        up  = np.array([0.0, 0.0, 1.0], dtype='f4')
        return _look_at(eye, self.target, up)

    def projection_matrix(self, aspect: float) -> np.ndarray:
        return _perspective(self.fov, aspect, self._near, self._far)

    def orbit(self, d_az: float, d_el: float):
        self.azimuth    = (self.azimuth + d_az) % 360.0
        self.elevation  = max(-85.0, min(85.0, self.elevation + d_el))

    def zoom(self, delta: float):
        self.distance = max(0.5, self.distance * (0.9 ** delta))

    def pan(self, dx: float, dy: float):
        """Pan in screen space.

        Derives the screen-up vector from the true camera up so that pan
        works correctly even when the camera is near-vertical (elevation
        close to ±90°).  A zero-length guard prevents NaN at the degenerate
        case of looking straight down/up.
        """
        az = math.radians(self.azimuth)
        el = math.radians(self.elevation)
        # Horizontal right vector (always well-defined, perpendicular to Z)
        right = np.array([-math.sin(az), math.cos(az), 0.0], dtype='f4')
        # Forward vector toward target
        fwd = self.target - self.eye()
        fwd_len = np.linalg.norm(fwd)
        if fwd_len < 1e-9:
            return   # degenerate: eye == target, nothing to pan
        fwd /= fwd_len
        # Screen-up = cross(right, fwd); normalise with zero-length guard
        up = np.cross(right, fwd)
        up_len = np.linalg.norm(up)
        if up_len < 1e-9:
            # Near-vertical look: fall back to world-right as a safe second vector
            right2 = np.array([1.0, 0.0, 0.0], dtype='f4')
            up = np.cross(right2, fwd)
            up_len = np.linalg.norm(up)
            if up_len < 1e-9:
                up = np.array([0.0, 1.0, 0.0], dtype='f4')
            else:
                up /= up_len
        else:
            up /= up_len
        scale = self.distance * 0.002
        self.target += right * dx * scale
        self.target -= up    * dy * scale

    def frame(self, center: np.ndarray, radius: float):
        self.target = center.copy()
        self.distance = max(1.0, radius * 2.5)

    def ray_from_screen(self, sx: int, sy: int, W: int, H: int) -> Tuple[np.ndarray, np.ndarray]:
        """Return (origin, direction) ray from screen pixel (sx, sy)."""
        aspect = W / max(H, 1)
        # NDC
        nx = (2.0 * sx / W) - 1.0
        ny = 1.0 - (2.0 * sy / H)
        f  = math.tan(math.radians(self.fov) * 0.5)
        # View-space direction
        eye = self.eye()
        fwd = self.target - eye
        fwd /= np.linalg.norm(fwd)
        up  = np.array([0.0, 0.0, 1.0], dtype='f4')
        right = np.cross(fwd, up)
        right /= np.linalg.norm(right)
        up2  = np.cross(right, fwd)
        dir_ = fwd + right * nx * f * aspect + up2 * ny * f
        dir_ /= np.linalg.norm(dir_)
        return eye, dir_


# ─────────────────────────────────────────────────────────────────────────────
#  OpenGL Viewport Widget
# ─────────────────────────────────────────────────────────────────────────────

_VERT_SHADER = """
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

_FRAG_SHADER = """
#version 330 core
in vec3 v_color;
out vec4 fragColor;
void main() {
    fragColor = vec4(v_color, 1.0);
}
"""

# Lit mesh shader (for MDL geometry rendering in play mode)
_VERT_MESH_SHADER = """
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

_FRAG_MESH_SHADER = """
#version 330 core
in vec3 v_normal;
in vec3 v_world_pos;
out vec4 fragColor;
uniform vec3 diffuse_color;
uniform vec3 light_dir;      // normalised, world space
uniform float ambient;
void main() {
    float diff = max(dot(normalize(v_normal), normalize(light_dir)), 0.0);
    vec3 col = diffuse_color * (ambient + diff * (1.0 - ambient));
    fragColor = vec4(col, 1.0);
}
"""

# Colors for different object types (RGB 0-1)
_COLOR_PLACEABLE = (0.2, 0.6, 1.0)    # blue
_COLOR_CREATURE  = (1.0, 0.4, 0.2)    # orange
_COLOR_DOOR      = (0.8, 0.7, 0.1)    # yellow
_COLOR_TRIGGER   = (0.2, 1.0, 0.5)    # green
_COLOR_WAYPOINT  = (0.8, 0.2, 0.8)    # purple
_COLOR_SOUND     = (0.2, 0.9, 0.9)    # cyan
_COLOR_STORE     = (0.2, 0.9, 0.3)    # bright green
_COLOR_SELECTED  = (1.0, 1.0, 0.0)    # bright yellow
_COLOR_GRID      = (0.2, 0.2, 0.3)
_COLOR_GRID_AXIS = (0.5, 0.5, 0.6)
_COLOR_GROUND    = (0.12, 0.12, 0.18)


def _box_verts(cx: float, cy: float, cz: float, hw: float, hh: float, hd: float,
               color: Tuple) -> np.ndarray:
    """Generate a solid-colored wireframe box (12 lines, 24 verts)."""
    r, g, b = color
    xs = [cx - hw, cx + hw]
    ys = [cy - hh, cy + hh]
    zs = [cz,      cz + hd * 2]

    corners = [
        (xs[0], ys[0], zs[0]), (xs[1], ys[0], zs[0]),
        (xs[1], ys[1], zs[0]), (xs[0], ys[1], zs[0]),
        (xs[0], ys[0], zs[1]), (xs[1], ys[0], zs[1]),
        (xs[1], ys[1], zs[1]), (xs[0], ys[1], zs[1]),
    ]
    edges = [
        (0,1),(1,2),(2,3),(3,0),
        (4,5),(5,6),(6,7),(7,4),
        (0,4),(1,5),(2,6),(3,7),
    ]
    verts = []
    for a, b_ in edges:
        for idx in (a, b_):
            verts.extend([*corners[idx], r, g, b])
    return np.array(verts, dtype='f4')


def _box_verts_solid(cx, cy, cz, hw, hh, hd, color):
    """Filled box: 6 faces × 2 triangles × 3 verts × 6 floats."""
    r, g, b = color
    x0, x1 = cx - hw, cx + hw
    y0, y1 = cy - hh, cy + hh
    z0, z1 = cz, cz + hd * 2

    faces = [
        # Bottom
        (x0,y0,z0, x1,y0,z0, x1,y1,z0, x0,y0,z0, x1,y1,z0, x0,y1,z0),
        # Top
        (x0,y0,z1, x1,y1,z1, x1,y0,z1, x0,y0,z1, x0,y1,z1, x1,y1,z1),
        # Front (-Y)
        (x0,y0,z0, x1,y0,z1, x1,y0,z0, x0,y0,z0, x0,y0,z1, x1,y0,z1),
        # Back (+Y)
        (x0,y1,z0, x1,y1,z0, x1,y1,z1, x0,y1,z0, x1,y1,z1, x0,y1,z1),
        # Left (-X)
        (x0,y0,z0, x0,y1,z0, x0,y1,z1, x0,y0,z0, x0,y1,z1, x0,y0,z1),
        # Right (+X)
        (x1,y0,z0, x1,y1,z1, x1,y1,z0, x1,y0,z0, x1,y0,z1, x1,y1,z1),
    ]
    verts = []
    for face in faces:
        coords = list(face)
        for i in range(0, len(coords), 3):
            verts.extend([coords[i], coords[i+1], coords[i+2], r, g, b])
    return np.array(verts, dtype='f4')


def _grid_verts(n: int = 20, step: float = 1.0) -> np.ndarray:
    """Generate an N×N grid on the Z=0 plane."""
    verts = []
    half  = n * step * 0.5
    for i in range(-n, n + 1):
        x = i * step
        br = 0.4 if (i % 5 == 0) else 0.18
        # Along X axis
        verts.extend([-half, x, 0, br, br, br + 0.1,
                       half, x, 0, br, br, br + 0.1])
        # Along Y axis
        verts.extend([x, -half, 0, br, br, br + 0.1,
                       x,  half, 0, br, br, br + 0.1])
    # Axis lines
    verts.extend([0, -half, 0, 0.6, 0.2, 0.2,    # X axis (red)
                   half*2, -half, 0, 0.6, 0.2, 0.2])
    verts.extend([-half, 0, 0, 0.2, 0.6, 0.2,    # Y axis (green)
                  -half, half*2, 0, 0.2, 0.6, 0.2])
    return np.array(verts, dtype='f4')


class ViewportWidget(QOpenGLWidget):
    """
    ModernGL-powered 3D viewport for GModular.
    Supports both editor mode (orbit camera) and play/preview mode
    (first-person walk with capsule controller on walkmesh).
    """

    # Qt signals
    object_selected    = pyqtSignal(object)   # Emits selected GIT object or None
    object_placed      = pyqtSignal(object)   # Emits newly placed GIT object
    camera_moved       = pyqtSignal(float, float, float)  # x, y, z of target
    play_mode_changed  = pyqtSignal(bool)     # True = play mode started

    def __init__(self, parent=None):
        # Force OpenGL 3.3 Core Profile
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setSamples(4)
        QSurfaceFormat.setDefaultFormat(fmt)

        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(400, 300)

        self.camera = OrbitCamera()
        self._ctx: Optional["moderngl.Context"] = None
        self._prog: Optional["moderngl.Program"] = None
        self._prog_mesh: Optional["moderngl.Program"] = None   # lit mesh shader
        self._grid_vao: Optional["moderngl.VertexArray"] = None
        self._grid_verts_count: int = 0
        self._object_vaos: List[dict] = []   # list of {vao, count, obj}
        self._mdl_vaos: List[dict] = []      # list of {vao, count, resref, color}

        # Interaction state
        self._last_mouse: Optional[QPoint] = None
        self._mouse_button: Optional[int]  = None
        self._keys: set = set()
        self._placement_mode: bool = False
        self._selected_obj = None
        self._place_template: Optional[str] = None  # ResRef to place
        self._place_asset_type: str = "placeable"   # GIT type to place

        # ── Play mode state ───────────────────────────────────────────────────
        self._play_mode: bool = False
        self._play_session: Optional[object] = None   # PlaySession
        self._npc_registry: Optional[object] = None   # NPCRegistry
        self._play_last_time: float = 0.0
        self._play_mouse_last: Optional[QPoint] = None
        self._play_pitch: float = 0.0    # camera pitch in degrees
        self._game_dir: str = ""         # set from main_window

        # Camera movement timer (WASD in editor mode, also used in play mode)
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(16)  # ~60 fps
        self._move_timer.timeout.connect(self._process_movement)

        # Refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(33)  # ~30 fps
        self._refresh_timer.timeout.connect(self.update)
        self._refresh_timer.start()

        # Module state subscription
        try:
            get_module_state().on_change(self._on_module_changed)
        except Exception:
            pass

    # ── Placement mode ────────────────────────────────────────────────────────

    def set_placement_mode(self, enabled: bool, template_resref: str = "",
                           asset_type: str = "placeable"):
        self._placement_mode = enabled
        self._place_template = template_resref
        self._place_asset_type = asset_type
        if enabled:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def select_object(self, obj):
        self._selected_obj = obj
        self._rebuild_object_vaos()
        self.update()

    # ── OpenGL Lifecycle ─────────────────────────────────────────────────────

    def initializeGL(self):
        if not _HAS_MODERNGL:
            return
        try:
            self._ctx = moderngl.create_context()
            self._ctx.enable(moderngl.DEPTH_TEST)
            self._ctx.enable(moderngl.CULL_FACE)

            self._prog = self._ctx.program(
                vertex_shader=_VERT_SHADER,
                fragment_shader=_FRAG_SHADER,
            )
            try:
                self._prog_mesh = self._ctx.program(
                    vertex_shader=_VERT_MESH_SHADER,
                    fragment_shader=_FRAG_MESH_SHADER,
                )
            except Exception as e:
                log.warning(f"Mesh shader compile failed (using fallback): {e}")
                self._prog_mesh = None
            self._build_grid()
        except Exception as e:
            log.error(f"GL init error: {e}")

    def resizeGL(self, w: int, h: int):
        if self._ctx:
            self._ctx.viewport = (0, 0, w, h)

    def paintGL(self):
        if not self._ctx or not self._prog:
            return
        try:
            self._ctx.clear(0.08, 0.08, 0.12, 1.0)
            W, H = self.width(), self.height()
            aspect = W / max(H, 1)

            # ── View/projection matrix ────────────────────────────────────────
            if self._play_mode and self._play_session:
                # First-person camera from player controller
                proj = self.camera.projection_matrix(aspect)
                eye  = self._play_session.player_eye
                look = self._play_session.player.look_at_target(self._play_pitch)
                view = _look_at(
                    np.array(eye,  dtype='f4'),
                    np.array(look, dtype='f4'),
                    np.array([0.0, 0.0, 1.0], dtype='f4'),
                )
                vp = proj @ view
            else:
                proj = self.camera.projection_matrix(aspect)
                view = self.camera.view_matrix()
                vp   = proj @ view

            self._prog["mvp"].write(vp.astype('f4').tobytes())

            # Grid
            if self._grid_vao:
                self._grid_vao.render(moderngl.LINES,
                                      vertices=self._grid_verts_count)

            # GIT object boxes (always shown in both modes)
            for entry in self._object_vaos:
                vao   = entry.get("vao")
                count = entry.get("count", 0)
                if vao and count > 0:
                    try:
                        vao.render(moderngl.TRIANGLES, vertices=count)
                    except Exception:
                        pass

            # MDL mesh geometry (play mode)
            if self._play_mode and self._mdl_vaos and self._prog_mesh:
                ident = np.eye(4, dtype='f4')
                try:
                    self._prog_mesh["mvp"].write(vp.astype('f4').tobytes())
                    self._prog_mesh["model"].write(ident.tobytes())
                    self._prog_mesh["light_dir"].write(
                        np.array([0.5, 0.5, 1.0], dtype='f4').tobytes())
                    self._prog_mesh["ambient"].value = 0.3
                except Exception:
                    pass
                for entry in self._mdl_vaos:
                    vao   = entry.get("vao")
                    count = entry.get("count", 0)
                    color = entry.get("color", (0.6, 0.6, 0.6))
                    if vao and count > 0:
                        try:
                            if self._prog_mesh:
                                self._prog_mesh["diffuse_color"].write(
                                    np.array(color, dtype='f4').tobytes())
                            vao.render(moderngl.TRIANGLES, vertices=count)
                        except Exception:
                            pass

        except Exception as e:
            log.debug(f"paintGL error: {e}")

    # ── Grid ─────────────────────────────────────────────────────────────────

    def _build_grid(self):
        if not self._ctx or not self._prog:
            return
        verts = _grid_verts(n=20, step=1.0)
        vbo = self._ctx.buffer(verts.tobytes())
        self._grid_vao = self._ctx.vertex_array(
            self._prog,
            [(vbo, "3f 3f", "in_position", "in_color")]
        )
        self._grid_verts_count = len(verts) // 6

    # ── Object VAOs ──────────────────────────────────────────────────────────

    def _on_module_changed(self):
        self._rebuild_object_vaos()

    def _rebuild_object_vaos(self):
        """Rebuild vertex arrays for all GIT objects (all 7 types)."""
        if not self._ctx or not self._prog:
            return

        # Release old VAOs
        for entry in self._object_vaos:
            try:
                entry["vbo"].release()
                entry["vao"].release()
            except Exception:
                pass
        self._object_vaos.clear()

        try:
            from ..core.module_state import get_module_state
            state = get_module_state()
            if not state.git:
                return

            def _add_box(obj, hw, hh, hd, base_color):
                is_sel = (obj is self._selected_obj)
                color  = _COLOR_SELECTED if is_sel else base_color
                verts  = _box_verts_solid(obj.position.x, obj.position.y,
                                          obj.position.z, hw, hh, hd, color)
                vbo = self._ctx.buffer(verts.tobytes())
                vao = self._ctx.vertex_array(
                    self._prog, [(vbo, "3f 3f", "in_position", "in_color")]
                )
                self._object_vaos.append({"vao": vao, "vbo": vbo,
                                          "count": len(verts) // 6, "obj": obj})

            # Placeables  — medium cube, blue
            for p in state.git.placeables:
                _add_box(p, 0.30, 0.30, 0.30, _COLOR_PLACEABLE)

            # Creatures   — tall box, orange
            for c in state.git.creatures:
                _add_box(c, 0.35, 0.35, 0.70, _COLOR_CREATURE)

            # Doors       — wide thin slab, yellow
            for d in state.git.doors:
                _add_box(d, 0.50, 0.15, 0.90, _COLOR_DOOR)

            # Waypoints   — small pillar, purple
            for w in state.git.waypoints:
                _add_box(w, 0.15, 0.15, 0.50, _COLOR_WAYPOINT)

            # Triggers    — flat wide diamond (rendered as thin box), green
            for t in state.git.triggers:
                _add_box(t, 0.50, 0.50, 0.05, _COLOR_TRIGGER)

            # Sounds      — small sphere-ish cube, cyan
            for s in state.git.sounds:
                _add_box(s, 0.20, 0.20, 0.20, _COLOR_SOUND)

            # Stores      — medium cube, bright green
            for st in state.git.stores:
                _add_box(st, 0.30, 0.30, 0.40, _COLOR_STORE)

        except Exception as e:
            log.debug(f"VAO rebuild error: {e}")

        self.update()

    # ── Play Mode ─────────────────────────────────────────────────────────────

    def set_game_dir(self, game_dir: str):
        """Called by main_window when the user sets/changes the game directory."""
        self._game_dir = game_dir

    def start_play_mode(self):
        """
        Enter first-person preview / walk mode.
        Builds walkmesh from WalkmeshPanel data or flat ground,
        spawns the player at the first waypoint / centroid,
        populates NPC registry, and switches to FPS camera.
        """
        if not _HAS_ENGINE:
            log.warning("Engine module not available — play mode disabled")
            return

        try:
            from ..core.module_state import get_module_state
            state = get_module_state()
            git = state.git if state else None

            # ── Build walkmesh triangles ───────────────────────────────────────
            walk_tris = self._collect_walkmesh_triangles()

            # ── Start play session ─────────────────────────────────────────────
            self._play_session = PlaySession.start(
                git_data=git,
                walkmesh_triangles=walk_tris,
            )

            # ── Populate NPC registry ──────────────────────────────────────────
            self._npc_registry = NPCRegistry()
            if git:
                count = self._npc_registry.populate_from_git(git)
                log.info(f"Play mode: {count} NPCs registered")
                # Try to load NPC models from game dir
                if self._game_dir:
                    self._npc_registry.try_load_models(self._game_dir)

            # ── Build MDL VAOs for scene geometry ─────────────────────────────
            self._build_mdl_vaos_from_game()

            # ── Switch mode ───────────────────────────────────────────────────
            self._play_mode  = True
            self._play_pitch = 0.0
            self._play_last_time = time.time()
            self._play_mouse_last = None

            # Capture mouse for FPS look
            self.setMouseTracking(True)
            self.grabMouse()
            self.setCursor(Qt.BlankCursor)

            # Start movement timer at higher rate for smooth play
            self._move_timer.setInterval(16)
            if not self._move_timer.isActive():
                self._move_timer.start()

            self.play_mode_changed.emit(True)
            log.info("Play mode started")
        except Exception as e:
            log.error(f"start_play_mode error: {e}")
            import traceback; log.debug(traceback.format_exc())

    def stop_play_mode(self):
        """Exit play mode and return to orbit camera editor mode."""
        if not self._play_mode:
            return
        try:
            if self._play_session:
                self._play_session.stop()
            self._play_session = None
            self._npc_registry = None
            self._play_mode    = False
            self._play_pitch   = 0.0

            # Release mouse
            self.releaseMouse()
            self.setCursor(Qt.ArrowCursor)
            self.setMouseTracking(False)

            # Release MDL VAOs
            self._release_mdl_vaos()

            self._move_timer.stop()
            self.play_mode_changed.emit(False)
            log.info("Play mode stopped")
            self.update()
        except Exception as e:
            log.debug(f"stop_play_mode error: {e}")

    @property
    def is_play_mode(self) -> bool:
        return self._play_mode

    def _collect_walkmesh_triangles(self) -> List:
        """
        Collect walkable triangles from WalkmeshPanel data (if loaded)
        or fall back to an empty list (flat Z=0 ground is used automatically).
        """
        try:
            # Try to get WOK data from the walkmesh panel via module state
            from ..core.module_state import get_module_state
            state = get_module_state()
            # The walkmesh panel exposes parsed triangles via state.wok_triangles
            # (set by WalkmeshPanel when a WOK is loaded)
            wok_tris = getattr(state, 'wok_triangles', None)
            if wok_tris:
                log.debug(f"Walk mode: using {len(wok_tris)} WOK triangles")
                return wok_tris
        except Exception:
            pass
        log.debug("Walk mode: no WOK triangles found, using flat Z=0 ground")
        return []

    def _build_mdl_vaos_from_game(self):
        """
        Build OpenGL VAOs from MDL models in the game directory.
        Only processes the tile/environment MDLs, not character models.
        Falls back gracefully if no MDLs are found.
        """
        self._release_mdl_vaos()
        if not _HAS_MDL or not self._game_dir:
            return
        if not self._ctx or not self._prog:
            return
        # We load area room models if available; otherwise just use the
        # existing GIT box placeholders for preview
        try:
            import os
            models_dir = os.path.join(self._game_dir, 'models')
            if not os.path.isdir(models_dir):
                return

            from ..core.module_state import get_module_state
            state = get_module_state()
            are = getattr(state, 'are', None) if state else None
            room_name = ""
            if are:
                # AREData typically has 'room_name' / 'tileset' info
                room_name = getattr(are, 'tileset', '') or ""

            cache = get_model_cache()
            loaded = 0
            # Try to load the room MDL (e.g. ebo_m01aa.mdl for Endar Spire)
            if room_name:
                candidates = [
                    room_name.lower() + '.mdl',
                    room_name.lower() + 'a.mdl',
                ]
                for cand in candidates:
                    mdl_path = os.path.join(models_dir, cand)
                    if os.path.exists(mdl_path):
                        mesh = cache.load(mdl_path)
                        if mesh:
                            self._upload_mesh_to_gl(mesh, (0.45, 0.42, 0.38))
                            loaded += 1
                            break
            log.debug(f"MDL VAOs built: {loaded} models, {len(self._mdl_vaos)} VAOs")
        except Exception as e:
            log.debug(f"_build_mdl_vaos_from_game error: {e}")

    def _upload_mesh_to_gl(self, mesh_data, color: Tuple):
        """Upload a MeshData object's geometry to GL as indexed triangle VAOs."""
        if not self._ctx or not self._prog:
            return
        try:
            prog = self._prog_mesh if self._prog_mesh else self._prog
            for node in mesh_data.visible_mesh_nodes():
                if not node.vertices or not node.faces:
                    continue
                # Build flat vertex/normal array for triangles
                verts_out = []
                has_normals = len(node.normals) == len(node.vertices)
                for f in node.faces:
                    if max(f) >= len(node.vertices):
                        continue
                    for vi in f:
                        vx, vy, vz = node.vertices[vi]
                        if has_normals:
                            nx, ny, nz = node.normals[vi]
                        else:
                            nx, ny, nz = 0.0, 0.0, 1.0
                        verts_out.extend([vx, vy, vz, nx, ny, nz])
                if not verts_out:
                    continue
                arr = np.array(verts_out, dtype='f4')
                vbo = self._ctx.buffer(arr.tobytes())
                if self._prog_mesh:
                    vao = self._ctx.vertex_array(
                        self._prog_mesh,
                        [(vbo, "3f 3f", "in_position", "in_normal")]
                    )
                else:
                    # Fallback: use flat-colour shader with diffuse color baked in
                    r, g, b = color
                    flat = []
                    for i in range(0, len(verts_out), 6):
                        flat.extend(verts_out[i:i+3] + [r, g, b])
                    arr2 = np.array(flat, dtype='f4')
                    vbo  = self._ctx.buffer(arr2.tobytes())
                    vao  = self._ctx.vertex_array(
                        self._prog,
                        [(vbo, "3f 3f", "in_position", "in_color")]
                    )
                self._mdl_vaos.append({
                    "vao":   vao,
                    "vbo":   vbo,
                    "count": len(verts_out) // 6,
                    "color": color,
                    "resref": getattr(mesh_data, 'name', ''),
                })
        except Exception as e:
            log.debug(f"_upload_mesh_to_gl error: {e}")

    def _release_mdl_vaos(self):
        for entry in self._mdl_vaos:
            try:
                entry["vbo"].release()
                entry["vao"].release()
            except Exception:
                pass
        self._mdl_vaos.clear()

    # ── Hit Testing ──────────────────────────────────────────────────────────

    def _pick_object(self, sx: int, sy: int):
        """Raycast against object bounding boxes. Returns best hit or None."""
        W, H = self.width(), self.height()
        try:
            from ..core.module_state import get_module_state
            state = get_module_state()
            if not state.git:
                return None

            origin, direction = self.camera.ray_from_screen(sx, sy, W, H)

            best_t   = float("inf")
            best_obj = None

            def ray_box(pos, hw, hh, hd):
                """Slab test against AABB.

                hw = half-width  (X axis)
                hh = half-height (Y axis, must use hh not hw)
                hd = half-depth  (Z, height/2 of the box above pos.z)
                """
                bmin = np.array([pos.x - hw, pos.y - hh, pos.z],       dtype='f4')
                bmax = np.array([pos.x + hw, pos.y + hh, pos.z + hd*2], dtype='f4')
                t_min = (bmin - origin) / (direction + 1e-20)
                t_max = (bmax - origin) / (direction + 1e-20)
                t_near = np.minimum(t_min, t_max)
                t_far  = np.maximum(t_min, t_max)
                t_enter = t_near.max()
                t_exit  = t_far.min()
                if t_enter <= t_exit and t_exit > 0:
                    return t_enter if t_enter > 0 else t_exit
                return None

            for p in state.git.placeables:
                t = ray_box(p.position, 0.3, 0.3, 0.3)
                if t and t < best_t:
                    best_t = t; best_obj = p

            for c in state.git.creatures:
                t = ray_box(c.position, 0.35, 0.35, 0.7)
                if t and t < best_t:
                    best_t = t; best_obj = c

            for d in state.git.doors:
                t = ray_box(d.position, 0.5, 0.15, 0.9)
                if t and t < best_t:
                    best_t = t; best_obj = d

            for w in state.git.waypoints:
                t = ray_box(w.position, 0.15, 0.15, 0.5)
                if t and t < best_t:
                    best_t = t; best_obj = w

            for tr in state.git.triggers:
                t = ray_box(tr.position, 0.5, 0.5, 0.05)
                if t and t < best_t:
                    best_t = t; best_obj = tr

            for so in state.git.sounds:
                t = ray_box(so.position, 0.2, 0.2, 0.2)
                if t and t < best_t:
                    best_t = t; best_obj = so

            for st in state.git.stores:
                t = ray_box(st.position, 0.3, 0.3, 0.4)
                if t and t < best_t:
                    best_t = t; best_obj = st

            return best_obj
        except Exception as e:
            log.debug(f"Pick error: {e}")
            return None

    def _ray_ground_intersect(self, sx: int, sy: int) -> Optional[Vector3]:
        """Find where mouse ray intersects Z=0 ground plane."""
        W, H = self.width(), self.height()
        try:
            origin, direction = self.camera.ray_from_screen(sx, sy, W, H)
            if abs(direction[2]) < 1e-9:
                return None
            t = -origin[2] / direction[2]
            if t < 0:
                return None
            from ..formats.gff_types import Vector3
            pt = origin + direction * t
            return Vector3(float(pt[0]), float(pt[1]), 0.0)
        except Exception:
            return None

    # ── Mouse Events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        self._last_mouse = event.pos()

        if self._play_mode:
            # In play mode: mouse clicks don't select objects
            self._mouse_button = event.button()
            return

        if event.button() == Qt.LeftButton:
            if self._placement_mode:
                # Place a new object
                pos = self._ray_ground_intersect(event.x(), event.y())
                if pos:
                    self._place_object_at(pos)
            else:
                # Select object
                obj = self._pick_object(event.x(), event.y())
                self._selected_obj = obj
                self._rebuild_object_vaos()
                self.object_selected.emit(obj)

        self._mouse_button = event.button()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._play_mode and self._play_session:
            # FPS mouse look: update player yaw and pitch
            if self._play_mouse_last is not None:
                dx = event.x() - self._play_mouse_last.x()
                dy = event.y() - self._play_mouse_last.y()
                sensitivity = 0.20
                self._play_session.player.yaw -= dx * sensitivity
                self._play_pitch = max(-80.0, min(80.0,
                                       self._play_pitch - dy * sensitivity))
            self._play_mouse_last = event.pos()
            # Warp cursor to centre to allow infinite rotation
            cx, cy = self.width() // 2, self.height() // 2
            from PyQt5.QtGui import QCursor
            QCursor.setPos(self.mapToGlobal(QPoint(cx, cy)))
            self._play_mouse_last = QPoint(cx, cy)
            self.update()
            return

        if self._last_mouse is None:
            self._last_mouse = event.pos()
            return

        dx = event.x() - self._last_mouse.x()
        dy = event.y() - self._last_mouse.y()
        self._last_mouse = event.pos()

        if event.buttons() & Qt.RightButton:
            # Orbit
            self.camera.orbit(-dx * 0.4, -dy * 0.4)
        elif event.buttons() & Qt.MiddleButton:
            # Pan
            self.camera.pan(dx, dy)

        target = self.camera.target
        self.camera_moved.emit(float(target[0]), float(target[1]), float(target[2]))
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._mouse_button = None

    def wheelEvent(self, event: QWheelEvent):
        if self._play_mode:
            return   # no zoom in play mode
        delta = event.angleDelta().y() / 120.0
        self.camera.zoom(-delta)
        self.update()

    # ── Keyboard Events ───────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        self._keys.add(event.key())
        if self._play_mode:
            if event.key() == Qt.Key_Escape:
                self.stop_play_mode()
            # WASD handled in _process_movement; start timer
            if not self._move_timer.isActive():
                self._move_timer.start()
            return

        if event.key() == Qt.Key_F:
            self._frame_all()
        elif event.key() == Qt.Key_Delete:
            self._delete_selected()
        elif event.key() == Qt.Key_Escape:
            self._placement_mode = False
            self.setCursor(Qt.ArrowCursor)
        if not self._move_timer.isActive():
            self._move_timer.start()

    def keyReleaseEvent(self, event: QKeyEvent):
        self._keys.discard(event.key())
        if not self._play_mode and not self._keys:
            self._move_timer.stop()

    def _process_movement(self):
        """WASD camera movement (editor) or player locomotion (play mode)."""
        if self._play_mode and self._play_session:
            # ── Play mode locomotion ──────────────────────────────────────────
            now = time.time()
            dt  = min(now - self._play_last_time, 0.1)   # cap at 100ms
            self._play_last_time = now

            fwd   = 0.0
            right = 0.0
            turn  = 0.0
            running = (Qt.Key_Shift in self._keys)

            if Qt.Key_W in self._keys:    fwd   += 1.0
            if Qt.Key_S in self._keys:    fwd   -= 1.0
            if Qt.Key_A in self._keys:    turn  += 1.0   # turn left
            if Qt.Key_D in self._keys:    turn  -= 1.0   # turn right
            if Qt.Key_Q in self._keys:    right -= 1.0   # strafe left
            if Qt.Key_E in self._keys:    right += 1.0   # strafe right

            self._play_session.update(dt, {
                "move_forward": fwd,
                "move_right":   right,
                "turn_left":    turn,
                "running":      running,
            })
            self.update()
            return

        # ── Editor WASD ───────────────────────────────────────────────────────
        speed = 0.15
        az = math.radians(self.camera.azimuth)
        fwd   = np.array([math.cos(az), math.sin(az), 0.0], dtype='f4')
        right = np.array([-math.sin(az), math.cos(az), 0.0], dtype='f4')
        up    = np.array([0.0, 0.0, 1.0], dtype='f4')

        moved = False
        if Qt.Key_W in self._keys:
            self.camera.target += fwd * speed;   moved = True
        if Qt.Key_S in self._keys:
            self.camera.target -= fwd * speed;   moved = True
        if Qt.Key_A in self._keys:
            self.camera.target -= right * speed; moved = True
        if Qt.Key_D in self._keys:
            self.camera.target += right * speed; moved = True
        if Qt.Key_Q in self._keys:
            self.camera.target -= up * speed;    moved = True
        if Qt.Key_E in self._keys:
            self.camera.target += up * speed;    moved = True
        if moved:
            t = self.camera.target
            self.camera_moved.emit(float(t[0]), float(t[1]), float(t[2]))
            self.update()

    # ── Actions ──────────────────────────────────────────────────────────────

    def _frame_all(self):
        """Zoom/pan to frame all visible objects (all 7 GIT types)."""
        try:
            from ..core.module_state import get_module_state
            state = get_module_state()
            if not state.git:
                return
            positions = []
            for obj in state.git.iter_all():
                pos = getattr(obj, "position", None)
                if pos is not None:
                    positions.append([pos.x, pos.y, pos.z])
            if not positions:
                return
            pts = np.array(positions, dtype='f4')
            center = pts.mean(axis=0)
            radius = float(np.linalg.norm(pts - center, axis=1).max())
            self.camera.frame(center, max(radius, 1.0))
            self.update()
        except Exception as e:
            log.debug(f"Frame all error: {e}")

    def _place_object_at(self, pos):
        """Place a new GIT object of the correct type at the given world position."""
        try:
            from ..core.module_state import get_module_state, PlaceObjectCommand
            from ..formats.gff_types import (
                GITPlaceable, GITCreature, GITDoor, GITWaypoint,
                GITTrigger, GITSoundObject, GITStoreObject,
            )
            state = get_module_state()
            if not state.git:
                return

            resref = (self._place_template or "obj_default")[:16]
            atype  = getattr(self, "_place_asset_type", "placeable")

            _constructors = {
                "placeable": GITPlaceable,
                "creature":  GITCreature,
                "door":      GITDoor,
                "waypoint":  GITWaypoint,
                "trigger":   GITTrigger,
                "sound":     GITSoundObject,
                "store":     GITStoreObject,
            }
            cls = _constructors.get(atype, GITPlaceable)
            obj = cls()
            obj.resref          = resref
            obj.template_resref = resref
            obj.tag             = resref
            obj.position        = pos

            state.execute(PlaceObjectCommand(state.git, obj))
            self.object_placed.emit(obj)
            log.info(f"Placed {atype}: {resref} at ({pos.x:.2f},{pos.y:.2f},{pos.z:.2f})")
        except Exception as e:
            log.debug(f"Place error: {e}")

    def _delete_selected(self):
        """Delete the currently selected object (any GIT type)."""
        if self._selected_obj is None:
            return
        try:
            from ..core.module_state import get_module_state, DeleteObjectCommand
            state = get_module_state()
            if not state.git:
                return
            obj = self._selected_obj
            state.execute(DeleteObjectCommand(state.git, obj))
            self._selected_obj = None
            self.object_selected.emit(None)
        except Exception as e:
            log.debug(f"Delete error: {e}")

    def frame_selected(self):
        """Move camera to look at selected object, or frame all if nothing selected."""
        obj = self._selected_obj
        if obj is None:
            self._frame_all()
            return
        try:
            pos = obj.position
            center = np.array([pos.x, pos.y, pos.z], dtype='f4')
            self.camera.frame(center, 5.0)
            self.update()
        except Exception:
            pass

    def frame_all(self):
        """Public alias: frame all GIT objects regardless of selection."""
        self._frame_all()
