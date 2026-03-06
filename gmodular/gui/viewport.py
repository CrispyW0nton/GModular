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
import logging
from typing import Optional, List, Tuple, Callable

import numpy as np
from PyQt5.QtWidgets import QOpenGLWidget, QSizePolicy
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt5.QtGui import QKeyEvent, QMouseEvent, QWheelEvent, QSurfaceFormat

log = logging.getLogger(__name__)

try:
    import moderngl
    _HAS_MODERNGL = True
except ImportError:
    _HAS_MODERNGL = False
    log.warning("ModernGL not available — viewport will be disabled")

try:
    from ..formats.gff_types import GITPlaceable, GITCreature, GITDoor, Vector3
    from ..core.module_state import get_module_state
except ImportError:
    pass


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
        """Pan in screen space."""
        az = math.radians(self.azimuth)
        el = math.radians(self.elevation)
        # Right vector
        right = np.array([-math.sin(az), math.cos(az), 0.0], dtype='f4')
        # Up-ish vector (in view space, not world up)
        fwd = self.target - self.eye()
        fwd /= np.linalg.norm(fwd)
        up  = np.cross(right, fwd)
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

# Colors for different object types (RGB 0-1)
_COLOR_PLACEABLE = (0.2, 0.6, 1.0)    # blue
_COLOR_CREATURE  = (1.0, 0.4, 0.2)    # orange
_COLOR_DOOR      = (0.8, 0.7, 0.1)    # yellow
_COLOR_TRIGGER   = (0.2, 1.0, 0.5)    # green
_COLOR_WAYPOINT  = (0.8, 0.2, 0.8)    # purple
_COLOR_SELECTED  = (1.0, 1.0, 0.0)    # bright yellow
_COLOR_GRID      = (0.2, 0.2, 0.3)
_COLOR_GRID_AXIS = (0.5, 0.5, 0.6)
_COLOR_GROUND    = (0.12, 0.12, 0.18)


def _box_verts(cx: float, cy: float, cz: float, hw: float, hh: float, hd: float,
               color: Tuple) -> np.ndarray:
    """Generate a solid-colored wireframe box (12 lines, 24 verts)."""
    r, g, b = color
    xs = [cx - hw, cx + hw]
    ys = [cy - hw, cy + hw]
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
            verts.extend([*corners[idx], r, g, b_if_not_same := b])
    return np.array(verts, dtype='f4')


def _box_verts_solid(cx, cy, cz, hw, hh, hd, color):
    """Filled box: 6 faces × 2 triangles × 3 verts × 6 floats."""
    r, g, b = color
    x0, x1 = cx - hw, cx + hw
    y0, y1 = cy - hw, cy + hw
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
    """

    # Qt signals
    object_selected    = pyqtSignal(object)   # Emits selected GIT object or None
    object_placed      = pyqtSignal(object)   # Emits newly placed GIT object
    camera_moved       = pyqtSignal(float, float, float)  # x, y, z of target

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
        self._grid_vao: Optional["moderngl.VertexArray"] = None
        self._grid_verts_count: int = 0
        self._object_vaos: List[dict] = []   # list of {vao, count, obj}

        # Interaction state
        self._last_mouse: Optional[QPoint] = None
        self._mouse_button: Optional[int]  = None
        self._keys: set = set()
        self._placement_mode: bool = False
        self._selected_obj = None
        self._place_template: Optional[str] = None  # ResRef to place

        # Camera movement timer (WASD)
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

    def set_placement_mode(self, enabled: bool, template_resref: str = ""):
        self._placement_mode = enabled
        self._place_template = template_resref
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

            proj = self.camera.projection_matrix(aspect)
            view = self.camera.view_matrix()
            vp   = proj @ view

            self._prog["mvp"].write(vp.astype('f4').tobytes())

            # Grid
            if self._grid_vao:
                self._grid_vao.render(moderngl.LINES, vertices=self._grid_verts_count)

            # Objects
            for entry in self._object_vaos:
                vao   = entry.get("vao")
                count = entry.get("count", 0)
                if vao and count > 0:
                    try:
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
        """Rebuild vertex arrays for all GIT objects."""
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

            # Placeables
            for p in state.git.placeables:
                is_sel = (p is self._selected_obj)
                color  = _COLOR_SELECTED if is_sel else _COLOR_PLACEABLE
                verts  = _box_verts_solid(p.position.x, p.position.y, p.position.z,
                                          0.3, 0.3, 0.3, color)
                vbo = self._ctx.buffer(verts.tobytes())
                vao = self._ctx.vertex_array(
                    self._prog, [(vbo, "3f 3f", "in_position", "in_color")]
                )
                self._object_vaos.append({"vao": vao, "vbo": vbo,
                                          "count": len(verts) // 6, "obj": p})

            # Creatures
            for c in state.git.creatures:
                is_sel = (c is self._selected_obj)
                color  = _COLOR_SELECTED if is_sel else _COLOR_CREATURE
                verts  = _box_verts_solid(c.position.x, c.position.y, c.position.z,
                                          0.35, 0.35, 0.7, color)
                vbo = self._ctx.buffer(verts.tobytes())
                vao = self._ctx.vertex_array(
                    self._prog, [(vbo, "3f 3f", "in_position", "in_color")]
                )
                self._object_vaos.append({"vao": vao, "vbo": vbo,
                                          "count": len(verts) // 6, "obj": c})

            # Doors
            for d in state.git.doors:
                is_sel = (d is self._selected_obj)
                color  = _COLOR_SELECTED if is_sel else _COLOR_DOOR
                verts  = _box_verts_solid(d.position.x, d.position.y, d.position.z,
                                          0.5, 0.15, 0.9, color)
                vbo = self._ctx.buffer(verts.tobytes())
                vao = self._ctx.vertex_array(
                    self._prog, [(vbo, "3f 3f", "in_position", "in_color")]
                )
                self._object_vaos.append({"vao": vao, "vbo": vbo,
                                          "count": len(verts) // 6, "obj": d})

            # Waypoints
            for w in state.git.waypoints:
                is_sel = (w is self._selected_obj)
                color  = _COLOR_SELECTED if is_sel else _COLOR_WAYPOINT
                verts  = _box_verts_solid(w.position.x, w.position.y, w.position.z,
                                          0.15, 0.15, 0.5, color)
                vbo = self._ctx.buffer(verts.tobytes())
                vao = self._ctx.vertex_array(
                    self._prog, [(vbo, "3f 3f", "in_position", "in_color")]
                )
                self._object_vaos.append({"vao": vao, "vbo": vbo,
                                          "count": len(verts) // 6, "obj": w})

        except Exception as e:
            log.debug(f"VAO rebuild error: {e}")

        self.update()

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
                """Slab test against AABB."""
                bmin = np.array([pos.x - hw, pos.y - hw, pos.z],       dtype='f4')
                bmax = np.array([pos.x + hw, pos.y + hw, pos.z + hd*2], dtype='f4')
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
        delta = event.angleDelta().y() / 120.0
        self.camera.zoom(-delta)
        self.update()

    # ── Keyboard Events ───────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        self._keys.add(event.key())
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
        if not self._keys:
            self._move_timer.stop()

    def _process_movement(self):
        """WASD camera movement (runs on timer)."""
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
        """Zoom/pan to frame all visible objects."""
        try:
            from ..core.module_state import get_module_state
            state = get_module_state()
            if not state.git:
                return
            positions = []
            for p in state.git.placeables:
                positions.append([p.position.x, p.position.y, p.position.z])
            for c in state.git.creatures:
                positions.append([c.position.x, c.position.y, c.position.z])
            for d in state.git.doors:
                positions.append([d.position.x, d.position.y, d.position.z])
            if not positions:
                return
            pts = np.array(positions, dtype='f4')
            center = pts.mean(axis=0)
            radius = float(np.linalg.norm(pts - center, axis=1).max())
            self.camera.frame(center, radius)
            self.update()
        except Exception as e:
            log.debug(f"Frame all error: {e}")

    def _place_object_at(self, pos):
        """Place a new placeable at the given world position."""
        try:
            from ..core.module_state import get_module_state
            from ..formats.gff_types import GITPlaceable
            from ..core.module_state import PlaceObjectCommand
            state = get_module_state()
            if not state.git:
                return
            p = GITPlaceable()
            p.resref          = (self._place_template or "plc_chair01")[:16]
            p.template_resref = p.resref
            p.tag             = p.resref
            p.position        = pos
            state.execute(PlaceObjectCommand(state.git, p))
            self.object_placed.emit(p)
            log.info(f"Placed: {p.resref} at ({pos.x:.2f},{pos.y:.2f},{pos.z:.2f})")
        except Exception as e:
            log.debug(f"Place error: {e}")

    def _delete_selected(self):
        """Delete the currently selected object."""
        if self._selected_obj is None:
            return
        try:
            from ..core.module_state import get_module_state, DeleteObjectCommand
            from ..formats.gff_types import GITPlaceable
            state = get_module_state()
            obj   = self._selected_obj
            if isinstance(obj, GITPlaceable) and obj in state.git.placeables:
                state.execute(DeleteObjectCommand(state.git, obj))
                self._selected_obj = None
                self.object_selected.emit(None)
        except Exception as e:
            log.debug(f"Delete error: {e}")

    def frame_selected(self):
        """Move camera to look at selected object."""
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
