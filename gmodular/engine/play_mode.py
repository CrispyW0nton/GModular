"""
GModular — Play Mode Controller
================================
Interactive first/third-person walk-through mode for the module editor.
This replaces the basic PlayerController with a full engine-quality
implementation supporting:

  - Walkmesh-based movement (height queries, collision)
  - WASD + mouse-look FPS / TPS camera
  - Door interaction (auto-open on proximity)
  - Object interaction (E key)
  - Dynamic camera switching (free orbit ↔ play camera)
  - Sprint (Shift), walk, sneak modes
  - Head bobbing (optional)
  - Ground-snap (Z = walkmesh height)
  - NPC detection radius + dialogue trigger

Architecture matches KotOR.js GameState + ModulePlayer + ModuleCamera.

Coordinate system: Z-up right-handed (matches KotOR/GModular convention).
"""

from __future__ import annotations
import math
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, List

log = logging.getLogger(__name__)

# ─── Types ───────────────────────────────────────────────────────────────────
Vec3 = Tuple[float, float, float]


def _len3(v: Vec3) -> float:
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])

def _norm3(v: Vec3) -> Vec3:
    n = _len3(v)
    if n < 1e-8: return (1.0, 0.0, 0.0)
    return (v[0]/n, v[1]/n, v[2]/n)

def _add3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0]+b[0], a[1]+b[1], a[2]+b[2])

def _sub3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])

def _scale3(v: Vec3, s: float) -> Vec3:
    return (v[0]*s, v[1]*s, v[2]*s)

def _dot3(a: Vec3, b: Vec3) -> float:
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

def _cross3(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ─────────────────────────────────────────────────────────────────────────────
#  Movement Input
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MovementInput:
    """Input state for one frame of movement."""
    forward:  float = 0.0    # -1 = backward, +1 = forward
    right:    float = 0.0    # -1 = left,     +1 = right
    up:       float = 0.0    # -1 = down,     +1 = up (fly/jump)
    turn:     float = 0.0    # camera yaw delta this frame (radians)
    pitch:    float = 0.0    # camera pitch delta this frame (radians)
    sprint:   bool  = False
    crouch:   bool  = False
    jump:     bool  = False
    interact: bool  = False  # E key
    toggle_run: bool = False


# ─────────────────────────────────────────────────────────────────────────────
#  Camera Mode
# ─────────────────────────────────────────────────────────────────────────────

class CameraMode:
    FREE_ORBIT   = "free_orbit"    # Editor orbit camera (default)
    FIRST_PERSON = "first_person"  # FPS (player eye level)
    THIRD_PERSON = "third_person"  # TPS (over shoulder, KotOR default)
    OVERHEAD     = "overhead"      # Isometric overhead (RTS view)
    DIALOG       = "dialog"        # Cinematic dialog camera


# ─────────────────────────────────────────────────────────────────────────────
#  Play Camera
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayCamera:
    """
    The in-game camera matching KotOR.js ModuleCamera.

    Defaults to KotOR's standard over-shoulder TPS camera.
    """
    mode:    str   = CameraMode.THIRD_PERSON

    # TPS settings (matching KotOR defaults)
    tps_distance: float = 3.5      # m behind player
    tps_height:   float = 1.4      # m above ground
    tps_shoulder: float = 0.6      # lateral offset (right shoulder)
    tps_pitch:    float = -0.20    # fixed pitch offset (radians)

    # FPS settings
    fps_height:   float = 1.7      # m above feet (eye level)

    # Camera state
    yaw:    float = 0.0            # horizontal look angle (radians)
    pitch:  float = 0.0            # vertical look angle (radians, clamped)
    fov:    float = 60.0           # degrees

    # Smoothing
    smooth_factor: float = 8.0     # position smoothing speed

    # Internal computed values
    eye:    Vec3  = field(default_factory=lambda: (0.0, 0.0, 1.7))
    target: Vec3  = field(default_factory=lambda: (0.0, 1.0, 1.7))

    # Min/max pitch (radians)
    min_pitch: float = -1.2
    max_pitch: float =  1.2

    def clamp_pitch(self) -> None:
        self.pitch = max(self.min_pitch, min(self.max_pitch, self.pitch))

    def compute_eye(self, player_pos: Vec3) -> Vec3:
        """Compute camera eye position from player position."""
        px, py, pz = player_pos

        if self.mode == CameraMode.FIRST_PERSON:
            return (px, py, pz + self.fps_height)

        elif self.mode == CameraMode.THIRD_PERSON:
            # Offset: behind + up + shoulder
            sin_y = math.sin(self.yaw)
            cos_y = math.cos(self.yaw)
            # Back vector (away from player's forward)
            back_x = -sin_y * self.tps_distance
            back_y = -cos_y * self.tps_distance
            # Right vector for shoulder offset
            right_x =  cos_y * self.tps_shoulder
            right_y = -sin_y * self.tps_shoulder
            # Pitch elevation
            pitch_elev = self.tps_distance * math.sin(-self.tps_pitch)
            return (
                px + back_x + right_x,
                py + back_y + right_y,
                pz + self.tps_height + pitch_elev,
            )

        elif self.mode == CameraMode.OVERHEAD:
            return (px, py - 2.0, pz + 15.0)

        else:
            return (px, py, pz + self.fps_height)

    def compute_target(self, player_pos: Vec3) -> Vec3:
        """Compute camera look-at target from player position."""
        px, py, pz = player_pos
        if self.mode == CameraMode.FIRST_PERSON:
            sin_y = math.sin(self.yaw)
            cos_y = math.cos(self.yaw)
            cos_p = math.cos(self.pitch)
            sin_p = math.sin(self.pitch)
            return (
                px + sin_y * cos_p,
                py + cos_y * cos_p,
                pz + self.fps_height + sin_p,
            )
        else:
            # TPS: look at player
            return (px, py, pz + 1.0)

    def update_from_player(self, player_pos: Vec3,
                            player_yaw: float, dt: float) -> None:
        """Update camera to follow player (with smoothing)."""
        if self.mode != CameraMode.FREE_ORBIT:
            # Match player yaw for TPS
            if self.mode != CameraMode.FIRST_PERSON:
                # Smooth yaw toward player's facing
                diff = player_yaw - self.yaw
                # Normalize to [-pi, pi]
                while diff > math.pi:  diff -= 2*math.pi
                while diff < -math.pi: diff += 2*math.pi
                # Only follow if difference is large (KotOR lazy camera)
                if abs(diff) > 0.5:
                    self.yaw = self.yaw + diff * min(dt * 2.0, 1.0)

        new_eye    = self.compute_eye(player_pos)
        new_target = self.compute_target(player_pos)

        # Smooth camera movement
        t = min(dt * self.smooth_factor, 1.0)
        self.eye    = (
            _lerp(self.eye[0], new_eye[0], t),
            _lerp(self.eye[1], new_eye[1], t),
            _lerp(self.eye[2], new_eye[2], t),
        )
        self.target = (
            _lerp(self.target[0], new_target[0], t),
            _lerp(self.target[1], new_target[1], t),
            _lerp(self.target[2], new_target[2], t),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Player State
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerState:
    """
    Full state of the player/camera in play mode.
    Matches KotOR.js ModulePlayer / ModuleCamera hybrid for the editor.
    """
    # Position (feet = base of character)
    x:      float = 0.0
    y:      float = 0.0
    z:      float = 0.0

    # Velocity
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0

    # Facing (yaw in radians, 0 = +Y)
    yaw:    float = 0.0
    # Vertical look angle
    pitch:  float = 0.0

    # Movement flags
    is_moving:  bool  = False
    is_running: bool  = False
    is_crouching:bool = False
    on_ground:  bool  = True
    fly_mode:   bool  = False    # Editor fly-through (ignore walkmesh)

    # Physics (when not in fly mode)
    gravity:    float = -9.8
    eye_height: float = 1.7

    # Speeds (m/s)
    walk_speed:  float = 2.5
    run_speed:   float = 5.5
    fly_speed:   float = 5.0
    sprint_mult: float = 2.0

    # Head bob
    head_bob_enabled: bool  = True
    head_bob_phase:   float = 0.0
    head_bob_amp:     float = 0.06    # metres
    head_bob_freq:    float = 8.0     # cycles per second

    # Interaction range
    interact_range: float = 3.0    # metres

    @property
    def position(self) -> Vec3:
        return (self.x, self.y, self.z)

    @position.setter
    def position(self, v: Vec3) -> None:
        self.x, self.y, self.z = float(v[0]), float(v[1]), float(v[2])

    @property
    def eye_position(self) -> Vec3:
        bob = 0.0
        if self.head_bob_enabled and self.is_moving:
            bob = math.sin(self.head_bob_phase) * self.head_bob_amp
        return (self.x, self.y, self.z + self.eye_height + bob)

    def forward_vector(self) -> Vec3:
        """Unit vector in the player's facing direction."""
        return (math.sin(self.yaw), math.cos(self.yaw), 0.0)

    def right_vector(self) -> Vec3:
        """Unit vector to the player's right."""
        return (math.cos(self.yaw), -math.sin(self.yaw), 0.0)


# ─────────────────────────────────────────────────────────────────────────────
#  PlayModeController  (the main engine controller)
# ─────────────────────────────────────────────────────────────────────────────

class PlayModeController:
    """
    Full play mode controller matching KotOR.js GameState movement loop.

    Features:
      - Walkmesh height query + sliding collision
      - Door auto-open detection
      - Sprint / walk / fly modes
      - Camera integration (TPS / FPS / orbit)
      - Entity proximity detection
      - Head-bobbing
      - Delta-time based integration (frame-rate independent)

    Usage::
        ctrl = PlayModeController(walkmesh=wok_data)
        ctrl.start(start_pos=(10, 10, 0))
        # each frame (16ms):
        input = MovementInput(forward=1.0, sprint=True)
        ctrl.update(input, delta=0.016)
        # Get camera matrices from ctrl.camera
    """

    def __init__(self, walkmesh: Any = None):
        self.player = PlayerState()
        self.camera = PlayCamera()

        # Walkmesh for collision + height queries
        self._walkmesh = walkmesh
        self._walkmesh_loaded = False
        if walkmesh is not None:
            self._walkmesh_loaded = True

        # Entity registry (for interaction + door detection)
        self._entities: Optional[Any] = None

        # Active mode
        self.active:      bool = False
        self.camera_mode: str  = CameraMode.THIRD_PERSON

        # Stats
        self.frame_count:  int   = 0
        self.total_time:   float = 0.0
        self._last_time:   float = time.perf_counter()

        # Interaction state
        self.nearby_entity_id: int = 0
        self.interaction_hint: str = ""

        # Ground-snap state
        self._ground_snap:  bool  = True
        self._current_floor: float = 0.0

        # Door auto-open range (metres)
        self._door_trigger_range: float = 1.5

        # Event callbacks
        self._on_door_enter: Optional[Any] = None
        self._on_interact:   Optional[Any] = None
        self._on_area_change:Optional[Any] = None

    # ── Setup ─────────────────────────────────────────────────────────────────

    def start(self, start_pos: Vec3 = (0.0, 0.0, 0.0),
              start_yaw: float = 0.0,
              camera_mode: str = CameraMode.THIRD_PERSON) -> None:
        """Enter play mode at given position."""
        self.player.position = start_pos
        self.player.yaw      = start_yaw
        self.camera.yaw      = start_yaw
        self.camera.mode     = camera_mode
        self.active          = True
        self._ground_snap    = (camera_mode != CameraMode.FREE_ORBIT)
        log.info(
            f"PlayMode started at ({start_pos[0]:.1f}, {start_pos[1]:.1f}, "
            f"{start_pos[2]:.1f}) cam={camera_mode}"
        )

    def stop(self) -> None:
        """Exit play mode, return to editor orbit camera."""
        self.active = False
        log.info("PlayMode stopped")

    def set_walkmesh(self, walkmesh: Any) -> None:
        """Set the walkmesh for height queries."""
        self._walkmesh    = walkmesh
        self._walkmesh_loaded = walkmesh is not None

    def set_entities(self, entity_registry: Any) -> None:
        """Set the entity registry for interaction detection."""
        self._entities = entity_registry

    def set_camera_mode(self, mode: str) -> None:
        self.camera_mode  = mode
        self.camera.mode  = mode
        self._ground_snap = (mode != CameraMode.FREE_ORBIT)

    # ── Main update ───────────────────────────────────────────────────────────

    def update(self, inp: MovementInput, delta: float) -> None:
        """
        Update player + camera for one frame.
        Call this ~60fps (delta = 1/fps).
        """
        if not self.active or delta <= 0.0:
            return

        self.frame_count += 1
        self.total_time  += delta
        effective_dt = min(delta, 0.1)  # cap at 100ms to prevent tunneling

        # Handle mode toggle (run/walk)
        if inp.toggle_run:
            self.player.is_running = not self.player.is_running

        # Apply mouse look
        self._apply_mouse_look(inp, effective_dt)

        # Apply movement
        if self.player.fly_mode or self.camera_mode == CameraMode.FREE_ORBIT:
            self._apply_fly_movement(inp, effective_dt)
        else:
            self._apply_ground_movement(inp, effective_dt)

        # Ground snap / walkmesh height
        if self._ground_snap and not self.player.fly_mode:
            self._snap_to_ground(effective_dt)

        # Update head bob
        if self.player.head_bob_enabled:
            self._update_head_bob(inp, effective_dt)

        # Update camera
        self.camera.update_from_player(
            self.player.position, self.player.yaw, effective_dt
        )

        # Door proximity detection
        self._check_door_proximity()

        # Entity interaction hint
        self._update_interaction_hint()

    # ── Mouse look ────────────────────────────────────────────────────────────

    def _apply_mouse_look(self, inp: MovementInput, dt: float) -> None:
        """Apply mouse rotation to camera yaw/pitch."""
        if abs(inp.turn) < 1e-6 and abs(inp.pitch) < 1e-6:
            return

        self.camera.yaw   += inp.turn
        self.camera.pitch += inp.pitch
        self.camera.clamp_pitch()

        # In FPS/TPS: player yaw follows camera yaw
        if self.camera_mode in (CameraMode.FIRST_PERSON, CameraMode.THIRD_PERSON):
            self.player.yaw = self.camera.yaw

    # ── Ground movement ───────────────────────────────────────────────────────

    def _apply_ground_movement(self, inp: MovementInput, dt: float) -> None:
        """
        Move the player on the walkmesh.
        Matches KotOR.js ModulePlayer.update() movement.
        """
        # Determine speed
        if self.player.is_running or inp.sprint:
            speed = self.player.run_speed
            if inp.sprint:
                speed *= self.player.sprint_mult
        else:
            speed = self.player.walk_speed

        # Movement direction (based on player yaw, not camera)
        yaw = self.player.yaw
        fwd_x = math.sin(yaw)
        fwd_y = math.cos(yaw)
        rgt_x = math.cos(yaw)
        rgt_y = -math.sin(yaw)

        # Compute velocity
        move_x = inp.forward * fwd_x + inp.right * rgt_x
        move_y = inp.forward * fwd_y + inp.right * rgt_y

        vel = _len3((move_x, move_y, 0.0))
        self.player.is_moving = vel > 0.01

        if self.player.is_moving:
            # Normalize and scale
            inv_vel = 1.0 / vel
            move_x *= inv_vel * speed * dt
            move_y *= inv_vel * speed * dt

            new_x = self.player.x + move_x
            new_y = self.player.y + move_y

            # Walkmesh boundary check
            if not self._check_walkmesh_boundary(new_x, new_y):
                # Try sliding — project along collision normal
                slide_x = self.player.x + move_x * 0.5
                slide_y = self.player.y
                if self._check_walkmesh_boundary(slide_x, slide_y):
                    new_x, new_y = slide_x, slide_y
                else:
                    slide_x = self.player.x
                    slide_y = self.player.y + move_y * 0.5
                    if self._check_walkmesh_boundary(slide_x, slide_y):
                        new_x, new_y = slide_x, slide_y
                    else:
                        new_x, new_y = self.player.x, self.player.y

            self.player.x = new_x
            self.player.y = new_y

    def _apply_fly_movement(self, inp: MovementInput, dt: float) -> None:
        """
        Free-fly movement for editor camera (ignores walkmesh).
        Matches GModular's existing WASD fly mode.
        """
        speed = self.player.fly_speed
        if inp.sprint:
            speed *= self.player.sprint_mult

        # Distance is camera's distance for orbit; use fly speed for play
        yaw = self.camera.yaw
        pit = self.camera.pitch

        fwd_x = math.sin(yaw) * math.cos(pit)
        fwd_y = math.cos(yaw) * math.cos(pit)
        fwd_z = math.sin(pit)

        rgt_x =  math.cos(yaw)
        rgt_y = -math.sin(yaw)
        rgt_z = 0.0

        up_x, up_y, up_z = 0.0, 0.0, 1.0

        dx = (inp.forward * fwd_x + inp.right * rgt_x + inp.up * up_x) * speed * dt
        dy = (inp.forward * fwd_y + inp.right * rgt_y + inp.up * up_y) * speed * dt
        dz = (inp.forward * fwd_z + inp.right * rgt_z + inp.up * up_z) * speed * dt

        self.player.x += dx
        self.player.y += dy
        self.player.z += dz
        self.player.is_moving = abs(dx) + abs(dy) + abs(dz) > 0.001

    # ── Walkmesh ──────────────────────────────────────────────────────────────

    def _check_walkmesh_boundary(self, x: float, y: float) -> bool:
        """
        Returns True if position (x, y) is on a walkable walkmesh face.
        Falls back to True when no walkmesh is loaded.
        """
        if not self._walkmesh_loaded or self._walkmesh is None:
            return True
        try:
            # Query walkmesh using height_at (returns None if off-mesh)
            h = self._walkmesh.height_at(x, y)
            return h is not None
        except Exception:
            return True

    def _snap_to_ground(self, dt: float) -> None:
        """
        Snap player Z to walkmesh height under current XY position.
        Uses smooth lerp to avoid jitter on uneven terrain.
        """
        if not self._walkmesh_loaded or self._walkmesh is None:
            return
        try:
            h = self._walkmesh.height_at(self.player.x, self.player.y)
            if h is not None:
                target_z = float(h)
                # Smooth snap
                diff = target_z - self.player.z
                if abs(diff) < 2.0:  # don't snap if too large (area transition)
                    self.player.z = self.player.z + diff * min(dt * 15.0, 1.0)
                else:
                    self.player.z = target_z
                self._current_floor = target_z
        except Exception as exc:
            log.debug("play_mode: floor-snap error: %s", exc)

    # ── Head bob ─────────────────────────────────────────────────────────────

    def _update_head_bob(self, inp: MovementInput, dt: float) -> None:
        """Update head bobbing phase when moving."""
        if not self.player.is_moving:
            # Decay bob phase back to zero
            self.player.head_bob_phase *= max(0.0, 1.0 - dt * 4.0)
            return
        freq = self.player.head_bob_freq
        if self.player.is_running or inp.sprint:
            freq *= 1.6
        self.player.head_bob_phase += dt * freq * math.pi * 2.0

    # ── Door proximity ────────────────────────────────────────────────────────

    def _check_door_proximity(self) -> None:
        """
        Check for doors within auto-open range and trigger them.
        Matches KotOR.js door trigger detection.
        """
        if self._entities is None:
            return
        try:
            px, py, pz = self.player.position
            for door in self._entities.get_doors():
                dx, dy, dz = door.position
                dist = math.sqrt((px-dx)**2 + (py-dy)**2)
                if dist < self._door_trigger_range:
                    if not door.is_open and not door.locked:
                        door.open()
                        if self._on_door_enter:
                            self._on_door_enter(door)
        except Exception as exc:
            log.debug("play_mode: door proximity check error: %s", exc)

    # ── Interaction hint ──────────────────────────────────────────────────────

    def _update_interaction_hint(self) -> None:
        """Find nearest interactable entity and update hint."""
        if self._entities is None:
            self.interaction_hint = ""
            self.nearby_entity_id = 0
            return
        try:
            px, py, pz = self.player.position
            best_dist = self.player.interact_range + 0.1
            best_id   = 0
            best_hint = ""

            for ent in self._entities.entities:
                if not ent.interactable:
                    continue
                ex, ey, ez = ent.position
                dist = math.sqrt((px-ex)**2 + (py-ey)**2 + (pz-ez)**2)
                if dist < best_dist:
                    best_dist = dist
                    best_id   = ent.entity_id
                    from .entity_system import Door3D, Placeable3D, Creature3D
                    if isinstance(ent, Door3D):
                        best_hint = f"[E] {'Open' if not ent.is_open else 'Close'} door"
                    elif isinstance(ent, Placeable3D):
                        best_hint = f"[E] Examine {ent.label or ent.resref}"
                    elif isinstance(ent, Creature3D):
                        best_hint = f"[E] Talk to {ent.label or ent.resref}"
                    else:
                        best_hint = f"[E] Interact"

            self.nearby_entity_id = best_id
            self.interaction_hint = best_hint
        except Exception:
            self.interaction_hint = ""

    # ── Interaction ───────────────────────────────────────────────────────────

    def interact(self) -> bool:
        """
        Trigger interaction with nearby entity.
        Called when player presses E.
        """
        if not self.nearby_entity_id or self._entities is None:
            return False
        ent = self._entities.get(self.nearby_entity_id)
        if ent is None:
            return False
        result = ent.interact()
        if self._on_interact:
            self._on_interact(ent)
        return result

    # ── Camera matrix helpers ─────────────────────────────────────────────────

    def get_view_matrix(self) -> Optional[Any]:
        """
        Build a view matrix from the play camera for rendering.
        Returns a numpy array or None if numpy unavailable.
        """
        try:
            import numpy as np
            eye    = self.camera.eye
            target = self.camera.target
            up     = (0.0, 0.0, 1.0)

            f = _norm3(_sub3(target, eye))
            r = _norm3(_cross3(f, up))
            u = _cross3(r, f)

            # Build column-major view matrix
            mat = np.array([
                [ r[0],  r[1],  r[2], -_dot3(r, eye)],
                [ u[0],  u[1],  u[2], -_dot3(u, eye)],
                [-f[0], -f[1], -f[2],  _dot3(f, eye)],
                [ 0.0,   0.0,   0.0,   1.0          ],
            ], dtype='f4').T   # transpose for column-major
            return mat
        except ImportError:
            return None

    # ── Stats / info ─────────────────────────────────────────────────────────

    def get_hud_text(self) -> str:
        """Return text for the play-mode HUD overlay."""
        p = self.player
        mode_str = "RUN" if p.is_running else "WALK"
        cam_str  = self.camera_mode.replace('_', ' ').title()
        hint = f" | {self.interaction_hint}" if self.interaction_hint else ""
        return (
            f"{cam_str} | {mode_str} | "
            f"({p.x:.1f}, {p.y:.1f}, {p.z:.1f})"
            f"{hint}"
        )

    def get_controls_hint(self) -> str:
        return (
            "WASD = move  │  Mouse = look  │  "
            "Shift = sprint  │  E = interact  │  "
            "F = fly mode  │  Esc = exit"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  PlaySession  — lightweight session handle for viewport integration
# ─────────────────────────────────────────────────────────────────────────────

class PlaySession:
    """
    Lightweight wrapper around PlayModeController for viewport integration.
    The viewport creates a PlaySession when entering play mode, and calls
    update() each frame with keyboard/mouse input.
    """

    def __init__(self):
        self.controller: Optional[PlayModeController] = None
        self.active: bool = False
        self._start_time: float = 0.0

    def start(self, walkmesh: Any = None,
              entities: Any = None,
              start_pos: Vec3 = (0.0, 0.0, 0.0),
              start_yaw: float = 0.0,
              camera_mode: str = CameraMode.THIRD_PERSON) -> None:
        """Initialize and start a play session."""
        self.controller = PlayModeController(walkmesh=walkmesh)
        if entities:
            self.controller.set_entities(entities)
        self.controller.start(start_pos, start_yaw, camera_mode)
        self.active     = True
        self._start_time = time.perf_counter()

    def stop(self) -> None:
        if self.controller:
            self.controller.stop()
        self.active = False

    def update(self, forward: float = 0.0, right: float = 0.0, up: float = 0.0,
               turn: float = 0.0, pitch: float = 0.0,
               sprint: bool = False, interact: bool = False,
               delta: float = 0.016) -> None:
        """Update from viewport input each frame."""
        if not self.active or not self.controller:
            return
        inp = MovementInput(
            forward=forward, right=right, up=up,
            turn=turn, pitch=pitch,
            sprint=sprint, interact=interact,
        )
        self.controller.update(inp, delta)
        if interact:
            self.controller.interact()

    @property
    def player(self) -> Optional[PlayerState]:
        return self.controller.player if self.controller else None

    @property
    def camera(self) -> Optional[PlayCamera]:
        return self.controller.camera if self.controller else None

    @property
    def position(self) -> Vec3:
        if self.controller:
            return self.controller.player.position
        return (0.0, 0.0, 0.0)

    @property
    def eye_position(self) -> Vec3:
        if self.controller:
            return self.controller.player.eye_position
        return (0.0, 0.0, 1.7)

    @property
    def yaw(self) -> float:
        return self.controller.player.yaw if self.controller else 0.0

    @property
    def pitch_angle(self) -> float:
        return self.controller.camera.pitch if self.controller else 0.0

    @property
    def interaction_hint(self) -> str:
        return self.controller.interaction_hint if self.controller else ""

    @property
    def hud_text(self) -> str:
        return self.controller.get_hud_text() if self.controller else ""

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._start_time
