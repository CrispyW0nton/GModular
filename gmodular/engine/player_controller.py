"""
GModular — Player Controller (preview walk mode)
=================================================
Simulates a capsule character controller that walks on a walkmesh or flat
ground plane.  No combat or game mechanics are simulated — pure locomotion
for level layout preview.

Design
------
* Player has a capsule: radius R, height H.
* Gravity pulls player down; the walkmesh (or Z=0 ground) provides a floor.
* WASD + mouse look: W/S move forward/back, A/D strafe.
* The controller projects the player's horizontal movement onto the walkmesh
  using triangle raycasting, then snaps Z to the floor.

Coordinate system: Z-up, right-handed (KotOR / GModular convention).
"""

from __future__ import annotations
import math
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

GRAVITY        = -12.0   # units/s² downward (negative Z)
WALK_SPEED     = 5.0     # units/s
RUN_SPEED      = 10.0    # units/s (Shift held)
TURN_SPEED     = 120.0   # degrees/s for keyboard turning
CAPSULE_RADIUS = 0.35    # metres
CAPSULE_HEIGHT = 1.8     # metres (eye height ≈ 1.6)
STEP_HEIGHT    = 0.30    # max step the player can auto-climb
GRAVITY_MAX    = -20.0   # terminal velocity


# ─────────────────────────────────────────────────────────────────────────────
#  Walkmesh triangle types (subset of KotOR walk types)
# ─────────────────────────────────────────────────────────────────────────────

WALKABLE_TYPES = frozenset([
    1,   # Walkable
    7,   # Walkable grass
    8,   # Walkable stone
    9,   # Walkable wood
])


# ─────────────────────────────────────────────────────────────────────────────
#  Ray / geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ray_triangle_intersect(
        origin: Tuple[float, float, float],
        direction: Tuple[float, float, float],
        v0: Tuple[float, float, float],
        v1: Tuple[float, float, float],
        v2: Tuple[float, float, float],
) -> Optional[float]:
    """
    Möller–Trumbore ray/triangle intersection.
    Returns t (distance along ray) if hit and t > 0, else None.
    """
    EPSILON = 1e-8
    ox, oy, oz = origin
    dx, dy, dz = direction
    ax, ay, az = v0
    bx, by, bz = v1
    cx, cy, cz = v2

    e1x = bx - ax; e1y = by - ay; e1z = bz - az
    e2x = cx - ax; e2y = cy - ay; e2z = cz - az

    # h = direction × e2
    hx = dy*e2z - dz*e2y
    hy = dz*e2x - dx*e2z
    hz = dx*e2y - dy*e2x

    a = e1x*hx + e1y*hy + e1z*hz
    if abs(a) < EPSILON:
        return None   # parallel

    inv_a = 1.0 / a
    sx = ox - ax; sy = oy - ay; sz = oz - az

    u = inv_a * (sx*hx + sy*hy + sz*hz)
    if u < 0.0 or u > 1.0:
        return None

    # q = s × e1
    qx = sy*e1z - sz*e1y
    qy = sz*e1x - sx*e1z
    qz = sx*e1y - sy*e1x

    v = inv_a * (dx*qx + dy*qy + dz*qz)
    if v < 0.0 or u + v > 1.0:
        return None

    t = inv_a * (e2x*qx + e2y*qy + e2z*qz)
    return t if t > EPSILON else None


def _floor_height_at(
        x: float,
        y: float,
        triangles: List,
        search_z_start: float = 10.0,
        search_z_range: float = 30.0,
) -> Optional[float]:
    """
    Cast a downward ray at (x, y, z_start) and return the Z-height of the
    first walkable triangle hit below that point, or None if nothing found.

    `triangles` is a list of (tri_verts_tuple, normal_tuple) as produced by
    MeshData.flat_triangle_array().
    """
    origin    = (x, y, search_z_start)
    direction = (0.0, 0.0, -1.0)
    best_t    = None

    for tri_verts, normal in triangles:
        if len(tri_verts) != 3:
            continue
        # Only accept triangles that face up-ish (normal.z > 0.1)
        if normal[2] < 0.1:
            continue
        t = _ray_triangle_intersect(origin, direction, *tri_verts)
        if t is not None and (best_t is None or t < best_t):
            best_t = t

    if best_t is not None:
        return search_z_start - best_t
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Player Controller
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerController:
    """
    Capsule character controller for GModular preview walk mode.

    The controller does NOT use a physics engine — it uses analytic
    ray-against-walkmesh queries to find the floor height each frame,
    then applies velocity and snaps the capsule base to the floor.

    Attributes
    ----------
    x, y, z     : World-space position of the **capsule base** (feet).
    yaw         : Horizontal facing angle in degrees (0 = +Y direction).
    vel_z       : Vertical velocity (for falling/gravity).
    on_ground   : True when capsule is resting on a surface.
    """
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0       # degrees, 0 = facing +Y

    vel_z: float   = 0.0
    on_ground: bool = True

    capsule_radius: float = CAPSULE_RADIUS
    capsule_height: float = CAPSULE_HEIGHT

    # Walk triangles (set from walkmesh or MDL floor geometry)
    _triangles: List = field(default_factory=list, repr=False)

    def set_walkmesh(self, triangles: List):
        """
        Supply walkable geometry as a list of (tri_verts, normal) tuples.
        Call this whenever the map changes.
        """
        self._triangles = triangles
        log.debug(f"PlayerController: {len(triangles)} walkmesh triangles loaded")

    def teleport(self, x: float, y: float, z: Optional[float] = None):
        """
        Move player to (x, y) and optionally a specific Z.
        If Z is None, the controller will drop to the floor on the next update.
        """
        self.x = x
        self.y = y
        if z is not None:
            self.z = z
        else:
            # Try to snap to floor
            fz = self._floor_z()
            self.z = fz if fz is not None else 0.0
        self.vel_z = 0.0
        self.on_ground = True
        log.debug(f"Teleport → ({self.x:.2f}, {self.y:.2f}, {self.z:.2f})")

    def snap_to_floor(self):
        """Force a floor snap without horizontal movement."""
        fz = self._floor_z()
        if fz is not None:
            self.z = fz
            self.vel_z = 0.0
            self.on_ground = True

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(self, dt: float,
               move_forward: float,    # +1 / 0 / -1
               move_right: float,      # +1 / 0 / -1
               turn_left: float,       # +1 / 0 / -1
               running: bool = False):
        """
        Advance simulation by dt seconds.

        Parameters
        ----------
        dt            : Delta time in seconds.
        move_forward  : Desired forward movement (-1 back, 0 still, +1 fwd).
        move_right    : Desired strafe (−1 left, 0, +1 right).
        turn_left     : Keyboard turning (−1 right, 0, +1 left).
        running       : If True, use RUN_SPEED.
        """
        if dt <= 0:
            return

        # ── Rotate ────────────────────────────────────────────────────────────
        self.yaw += turn_left * TURN_SPEED * dt
        self.yaw  = self.yaw % 360.0

        # ── Horizontal movement ───────────────────────────────────────────────
        speed = RUN_SPEED if running else WALK_SPEED
        yaw_r = math.radians(self.yaw)

        # +Y is forward in KotOR coordinate system, yaw=0 faces +Y
        fwd_x = -math.sin(yaw_r)
        fwd_y =  math.cos(yaw_r)
        rgt_x =  math.cos(yaw_r)
        rgt_y =  math.sin(yaw_r)

        dx = (fwd_x * move_forward + rgt_x * move_right) * speed * dt
        dy = (fwd_y * move_forward + rgt_y * move_right) * speed * dt

        new_x = self.x + dx
        new_y = self.y + dy

        # ── Gravity / vertical ────────────────────────────────────────────────
        if not self.on_ground:
            self.vel_z = max(GRAVITY_MAX, self.vel_z + GRAVITY * dt)
        else:
            self.vel_z = 0.0

        new_z = self.z + self.vel_z * dt

        # ── Floor collision ───────────────────────────────────────────────────
        floor_z = _floor_height_at(new_x, new_y, self._triangles,
                                   search_z_start=new_z + 2.0,
                                   search_z_range=6.0)
        if floor_z is None:
            # Fall back to flat Z=0 ground
            floor_z = 0.0

        if new_z <= floor_z + STEP_HEIGHT:
            new_z       = floor_z
            self.vel_z  = 0.0
            self.on_ground = True
        else:
            self.on_ground = False

        self.x = new_x
        self.y = new_y
        self.z = new_z

    # ── Camera helper ─────────────────────────────────────────────────────────

    def eye_position(self) -> Tuple[float, float, float]:
        """World-space camera eye position (feet + eye-height offset)."""
        eye_z = self.z + self.capsule_height * 0.88
        return (self.x, self.y, eye_z)

    def look_at_target(self, pitch_deg: float = 0.0) -> Tuple[float, float, float]:
        """
        World-space look-at target for first-person camera.

        Parameters
        ----------
        pitch_deg : Camera pitch in degrees (+up / -down).
        """
        yaw_r   = math.radians(self.yaw)
        pitch_r = math.radians(pitch_deg)
        ex, ey, ez = self.eye_position()

        fwd_x = -math.sin(yaw_r) * math.cos(pitch_r)
        fwd_y =  math.cos(yaw_r) * math.cos(pitch_r)
        fwd_z =  math.sin(pitch_r)
        return (ex + fwd_x, ey + fwd_y, ez + fwd_z)

    def _floor_z(self) -> Optional[float]:
        """Query floor Z at current XY."""
        return _floor_height_at(
            self.x, self.y, self._triangles,
            search_z_start=self.z + 5.0,
            search_z_range=20.0,
        )

    # ── Serialization ─────────────────────────────────────────────────────────

    def save_state(self) -> dict:
        return {"x": self.x, "y": self.y, "z": self.z, "yaw": self.yaw}

    def load_state(self, state: dict):
        self.x   = float(state.get("x", 0.0))
        self.y   = float(state.get("y", 0.0))
        self.z   = float(state.get("z", 0.0))
        self.yaw = float(state.get("yaw", 0.0))


# ─────────────────────────────────────────────────────────────────────────────
#  Play Session
# ─────────────────────────────────────────────────────────────────────────────

class PlaySession:
    """
    High-level coordinator for a single play-mode session in GModular.

    Lifecycle
    ---------
    1. Create via PlaySession.start(git_data, walkmesh_triangles).
    2. Each frame: call session.update(dt, input_state).
    3. Query session.player for camera position/look_at.
    4. Call session.stop() to end.
    """

    def __init__(self):
        self.active:  bool = False
        self.player:  PlayerController = PlayerController()
        self._walkmesh_triangles: List = []

    @classmethod
    def start(cls, git_data=None, walkmesh_triangles: Optional[List] = None,
              spawn_pos: Optional[Tuple[float, float, float]] = None) -> 'PlaySession':
        """Create and begin a new play session."""
        session = cls()
        session._walkmesh_triangles = walkmesh_triangles or []
        session.player.set_walkmesh(session._walkmesh_triangles)

        # Find spawn position: first waypoint named 'wp_start' or 'start',
        # else centroid of all GIT objects, else origin.
        if spawn_pos:
            sx, sy, sz = spawn_pos
        elif git_data:
            sx, sy, sz = _find_spawn(git_data)
        else:
            sx, sy, sz = 0.0, 0.0, 0.0

        session.player.teleport(sx, sy, sz)
        session.active = True
        log.info(f"PlaySession started at ({sx:.2f}, {sy:.2f}, {sz:.2f}), "
                 f"{len(session._walkmesh_triangles)} walk triangles")
        return session

    def update(self, dt: float, input_state: dict):
        """
        Advance play session by dt seconds.

        input_state keys (all optional, default 0/False):
          move_forward, move_right, turn_left, running
        """
        if not self.active:
            return
        self.player.update(
            dt=dt,
            move_forward=float(input_state.get("move_forward", 0)),
            move_right=float(input_state.get("move_right", 0)),
            turn_left=float(input_state.get("turn_left", 0)),
            running=bool(input_state.get("running", False)),
        )

    def stop(self):
        self.active = False
        log.info("PlaySession stopped")

    @property
    def player_eye(self) -> Tuple[float, float, float]:
        return self.player.eye_position()

    @property
    def player_look_at(self) -> Tuple[float, float, float]:
        return self.player.look_at_target()


def _find_spawn(git_data) -> Tuple[float, float, float]:
    """Find the best spawn position from GIT data."""
    try:
        # Prefer waypoint named 'wp_start' or 'start'
        for wp in git_data.waypoints:
            tag = getattr(wp, 'tag', '').lower()
            if 'start' in tag or 'spawn' in tag:
                p = wp.position
                return (p.x, p.y, p.z + 0.1)
        # First waypoint
        if git_data.waypoints:
            p = git_data.waypoints[0].position
            return (p.x, p.y, p.z + 0.1)
        # Centroid of all objects
        positions = []
        for obj in git_data.iter_all():
            pos = getattr(obj, 'position', None)
            if pos:
                positions.append((pos.x, pos.y, pos.z))
        if positions:
            cx = sum(p[0] for p in positions) / len(positions)
            cy = sum(p[1] for p in positions) / len(positions)
            return (cx, cy, 0.1)
    except Exception as e:
        log.debug(f"_find_spawn error: {e}")
    return (0.0, 0.0, 0.1)
