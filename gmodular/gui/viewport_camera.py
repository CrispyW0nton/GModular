"""
GModular — OrbitCamera
======================
Maya-style orbit camera (Z-up right-handed) extracted from viewport.py.

This module exposes ``OrbitCamera`` for direct import by tests, other GUI
modules, and scripts that need camera math without pulling in the full
Qt + ModernGL viewport.
"""
from __future__ import annotations

import math
from typing import Tuple

# ─── numpy ────────────────────────────────────────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None  # type: ignore


# ═════════════════════════════════════════════════════════════════════════════
#  Math helpers (shared with viewport.py)
# ═════════════════════════════════════════════════════════════════════════════

def _perspective(fov_deg: float, aspect: float,
                 near: float, far: float) -> "np.ndarray":
    """Standard perspective projection matrix (column-major, row-major stored)."""
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    return np.array([
        [f / aspect, 0,  0,                              0],
        [0,          f,  0,                              0],
        [0,          0, (far + near) / (near - far),    -1],
        [0,          0, (2 * far * near) / (near - far), 0],
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


# ═════════════════════════════════════════════════════════════════════════════
#  OrbitCamera
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
        self.yaw       = 45.0    # azimuth in degrees
        self.pitch     = 30.0    # elevation in degrees
        self.distance  = 15.0   # Kotor.NET: Distance
        self.target    = np.array([0., 0., 0.], dtype='f4') if _HAS_NUMPY else [0., 0., 0.]
        self.fov       = 60.0   # degrees
        self.near      = 0.001  # Kotor.NET: Near = 0.001f
        self.far       = 1000.0 # Kotor.NET: Far = 1000.0f

    # ── Backward-compatible property aliases ─────────────────────────────────

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

    # ── Core math ────────────────────────────────────────────────────────────

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
        fwd_h = np.array([math.cos(az), math.sin(az), 0.], dtype='f4')
        rt    = np.array([-math.sin(az), math.cos(az), 0.], dtype='f4')
        up_v  = np.array([0., 0., 1.], dtype='f4')
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
        self.far  = max(1000.0, self.distance * 4.0 + radius * 2.0)
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
