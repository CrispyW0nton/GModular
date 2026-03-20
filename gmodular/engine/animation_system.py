"""
GModular — Animation System
============================
Full MDL animation controller interpolation matching the Odyssey engine.

Architecture derived from:
  - KotOR.js OdysseyModelAnimationManager.ts (KobaltBlu)
  - KotOR.js controllers/PositionController.ts + OrientationController.ts
  - PyKotor GL scene.py (NickHugi/OldRepublicDevs)
  - Kotor.NET MDL format spec (cchargin)

This module implements:
  1. AnimationClip   — wraps AnimationData with per-node channel lookup
  2. AnimationTrack  — per-node animation state (elapsed, looping, events)
  3. AnimationPlayer — drives one model's animation with blending + transitions
  4. SceneAnimator   — manages animation players for all entities in a scene
  5. Interpolation helpers — lerp, slerp, bezier position sampling

All interpolation matches the KotOR.js implementation for accuracy:
  - Linear position lerp between key frames
  - Quaternion SLERP for orientation
  - Bezier curves for P2P motion paths
  - Transition blending between animations (cross-fade)
"""

from __future__ import annotations
import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable

log = logging.getLogger(__name__)

# ─── Types ───────────────────────────────────────────────────────────────────

Vec3  = Tuple[float, float, float]
Vec4  = Tuple[float, float, float, float]  # quaternion xyzw
Mat4  = List[float]   # column-major 4x4


# ─────────────────────────────────────────────────────────────────────────────
#  Math helpers
# ─────────────────────────────────────────────────────────────────────────────

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def _lerp3(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )

def _slerp(q1: Vec4, q2: Vec4, t: float) -> Vec4:
    """Spherical linear interpolation between two quaternions (xyzw)."""
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    dot = x1*x2 + y1*y2 + z1*z2 + w1*w2

    # If quaternions are very close, use linear interpolation
    if dot > 0.9995:
        rx = x1 + (x2 - x1) * t
        ry = y1 + (y2 - y1) * t
        rz = z1 + (z2 - z1) * t
        rw = w1 + (w2 - w1) * t
        # Normalize
        n = math.sqrt(rx*rx + ry*ry + rz*rz + rw*rw)
        if n > 0:
            return (rx/n, ry/n, rz/n, rw/n)
        return q2

    # Clamp dot to prevent acos domain errors
    dot = max(-1.0, min(1.0, dot))
    if dot < 0:
        x2, y2, z2, w2 = -x2, -y2, -z2, -w2
        dot = -dot

    theta_0 = math.acos(dot)
    theta   = theta_0 * t
    sin_t   = math.sin(theta)
    sin_t0  = math.sin(theta_0)

    if sin_t0 < 1e-8:
        return q2

    s1 = math.cos(theta) - dot * sin_t / sin_t0
    s2 = sin_t / sin_t0

    rx = s1 * x1 + s2 * x2
    ry = s1 * y1 + s2 * y2
    rz = s1 * z1 + s2 * z2
    rw = s1 * w1 + s2 * w2
    n  = math.sqrt(rx*rx + ry*ry + rz*rz + rw*rw)
    if n > 0:
        return (rx/n, ry/n, rz/n, rw/n)
    return (rx, ry, rz, rw)


def _normalize3(v: Vec3) -> Vec3:
    x, y, z = v
    n = math.sqrt(x*x + y*y + z*z)
    if n < 1e-8:
        return (0.0, 0.0, 1.0)
    return (x/n, y/n, z/n)

def _normalize4(q: Vec4) -> Vec4:
    x, y, z, w = q
    n = math.sqrt(x*x + y*y + z*z + w*w)
    if n < 1e-8:
        return (0.0, 0.0, 0.0, 1.0)
    return (x/n, y/n, z/n, w/n)


# ─────────────────────────────────────────────────────────────────────────────
#  Controller types (matching mdl_parser.py constants)
# ─────────────────────────────────────────────────────────────────────────────

CTRL_POSITION    = 8
CTRL_ORIENTATION = 20
CTRL_SCALE       = 36
CTRL_SELF_ILLUM  = 100
CTRL_ALPHA       = 132
CTRL_ALPHA_OLD   = 128


# ─────────────────────────────────────────────────────────────────────────────
#  Key-frame sampling
# ─────────────────────────────────────────────────────────────────────────────

def _find_frame_pair(rows: List[Tuple[float, List[float]]], elapsed: float):
    """
    Binary-search for the two surrounding key frames for `elapsed`.

    rows: sorted list of (time, [values...]) pairs from AnimationData.
    Returns: (last_row, next_row, fraction)
      fraction = how far between last_row.time and next_row.time we are.
    """
    if not rows:
        return None, None, 0.0

    # If elapsed is past the last frame, clamp to last
    if elapsed >= rows[-1][0]:
        return rows[-1], rows[-1], 0.0

    # If elapsed is before first frame, clamp to first
    if elapsed <= rows[0][0]:
        return rows[0], rows[0], 0.0

    # Binary search
    lo, hi = 0, len(rows) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if rows[mid][0] <= elapsed:
            lo = mid
        else:
            hi = mid

    last_t, last_v = rows[lo]
    next_t, next_v = rows[hi]

    dt = next_t - last_t
    if dt < 1e-8:
        return rows[lo], rows[hi], 1.0

    frac = (elapsed - last_t) / dt
    frac = max(0.0, min(1.0, frac))
    return rows[lo], rows[hi], frac


def sample_position(rows: List[Tuple[float, List[float]]], elapsed: float,
                    default: Vec3 = (0.0, 0.0, 0.0)) -> Vec3:
    """Sample position controller (3 floats) at elapsed time."""
    if not rows:
        return default
    last, nxt, frac = _find_frame_pair(rows, elapsed)
    if last is None:
        return default
    lv = last[1]
    nv = nxt[1]
    lx, ly, lz = lv[0], lv[1], lv[2]
    nx, ny, nz = nv[0], nv[1], nv[2]
    return (
        lx + (nx - lx) * frac,
        ly + (ny - ly) * frac,
        lz + (nz - lz) * frac,
    )


def sample_orientation(rows: List[Tuple[float, List[float]]], elapsed: float,
                       default: Vec4 = (0.0, 0.0, 0.0, 1.0)) -> Vec4:
    """
    Sample orientation controller (4 floats xyzw) at elapsed time.
    Uses SLERP interpolation matching KotOR.js OrientationController.
    """
    if not rows:
        return default
    last, nxt, frac = _find_frame_pair(rows, elapsed)
    if last is None:
        return default
    lv = last[1]
    nv = nxt[1]

    # Handle compressed 2-column format (wxyz where w is in column 0)
    # Standard 4-column: [x, y, z, w]
    if len(lv) >= 4:
        q1 = (lv[0], lv[1], lv[2], lv[3])
    elif len(lv) == 3:
        # Reconstruct w from unit quaternion constraint
        x, y, z = lv[0], lv[1], lv[2]
        w_sq = 1.0 - x*x - y*y - z*z
        w = math.sqrt(max(0.0, w_sq))
        q1 = (x, y, z, w)
    else:
        q1 = default

    if len(nv) >= 4:
        q2 = (nv[0], nv[1], nv[2], nv[3])
    elif len(nv) == 3:
        x, y, z = nv[0], nv[1], nv[2]
        w_sq = 1.0 - x*x - y*y - z*z
        w = math.sqrt(max(0.0, w_sq))
        q2 = (x, y, z, w)
    else:
        q2 = default

    if frac < 1e-6:
        return _normalize4(q1)
    if frac > 1.0 - 1e-6:
        return _normalize4(q2)
    return _slerp(q1, q2, frac)


def sample_scale(rows: List[Tuple[float, List[float]]], elapsed: float,
                 default: float = 1.0) -> float:
    """Sample scale controller (1 float) at elapsed time."""
    if not rows:
        return default
    last, nxt, frac = _find_frame_pair(rows, elapsed)
    if last is None:
        return default
    lv, nv = last[1], nxt[1]
    ls = lv[0] if lv else default
    ns = nv[0] if nv else default
    return ls + (ns - ls) * frac


def sample_alpha(rows: List[Tuple[float, List[float]]], elapsed: float,
                 default: float = 1.0) -> float:
    """Sample alpha controller (1 float) at elapsed time."""
    return sample_scale(rows, elapsed, default)


# ─────────────────────────────────────────────────────────────────────────────
#  Node Transform State
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NodeTransform:
    """Animated transform for one node (position + rotation + scale + alpha)."""
    position:    Vec3  = field(default_factory=lambda: (0.0, 0.0, 0.0))
    orientation: Vec4  = field(default_factory=lambda: (0.0, 0.0, 0.0, 1.0))
    scale:       float = 1.0
    alpha:       float = 1.0

    def copy(self) -> 'NodeTransform':
        return NodeTransform(
            position=self.position,
            orientation=self.orientation,
            scale=self.scale,
            alpha=self.alpha,
        )

    def lerp_toward(self, target: 'NodeTransform', t: float) -> 'NodeTransform':
        """Blend this transform toward target by factor t (for transition)."""
        return NodeTransform(
            position=_lerp3(self.position, target.position, t),
            orientation=_slerp(self.orientation, target.orientation, t),
            scale=_lerp(self.scale, target.scale, t),
            alpha=_lerp(self.alpha, target.alpha, t),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  AnimationState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AnimationState:
    """
    Runtime state for one playing animation, matching KotOR.js
    OdysseyModelAnimationManagerState.
    """
    loop:        bool  = False
    elapsed:     float = 0.0
    last_time:   float = 0.0
    delta:       float = 0.0
    elapsed_cnt: int   = 0   # how many times the animation has looped
    last_event:  int   = -1  # last fired event index

    def reset(self, loop: bool = False) -> None:
        self.loop       = loop
        self.elapsed    = 0.0
        self.last_time  = 0.0
        self.delta      = 0.0
        self.elapsed_cnt= 0
        self.last_event = -1


# ─────────────────────────────────────────────────────────────────────────────
#  AnimationPlayer  (per model-instance)
# ─────────────────────────────────────────────────────────────────────────────

class AnimationPlayer:
    """
    Drives one model's MDL animations, matching KotOR.js OdysseyModelAnimationManager.

    Supports:
      - Primary animation (current)
      - Transition blending (cross-fade from last animation)
      - Overlay animation (additive layer for upper-body/head)
      - Looping animations (idle, walk, run)
      - One-shot animations (attack, death, open)
      - Animation events (footstep, hit sound triggers)

    Usage::
        player = AnimationPlayer(mesh_data.animations)
        player.play("cpause1", loop=True)
        # each frame:
        player.update(delta_seconds)
        transforms = player.node_transforms  # Dict[str, NodeTransform]
    """

    def __init__(self, animations: List[Any]):
        """
        animations: list of AnimationData objects from MDLParser.
        """
        # Build lookup dict: name.lower() → AnimationData
        self._anims: Dict[str, Any] = {}
        for a in (animations or []):
            name = (getattr(a, 'name', '') or '').lower().strip()
            if name:
                self._anims[name] = a

        # Current + last animation + overlay
        self._current:  Optional[Any]  = None
        self._current_state = AnimationState()
        self._last:     Optional[Any]  = None
        self._last_state    = AnimationState()
        self._overlay:  Optional[Any]  = None
        self._overlay_state = AnimationState()

        # Transition state
        self._trans_elapsed: float = 0.0
        self._in_transition: bool  = False

        # Per-node transforms (output of last update())
        self.node_transforms: Dict[str, NodeTransform] = {}

        # Event callbacks: name → callable
        self._event_callbacks: Dict[str, Callable] = {}

        # Pause state
        self._paused: bool = False
        self._speed:  float = 1.0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def animation_names(self) -> List[str]:
        return list(self._anims.keys())

    @property
    def current_animation_name(self) -> str:
        if self._current:
            return getattr(self._current, 'name', '').lower()
        return ''

    def has_animation(self, name: str) -> bool:
        return name.lower() in self._anims

    def play(self, name: str, loop: bool = False, force_restart: bool = False) -> bool:
        """
        Play the named animation.

        Returns True if the animation was found and started.
        Matches KotOR.js OdysseyModel3D.playAnimation().
        """
        key = name.lower().strip()
        anim = self._anims.get(key)
        if anim is None:
            # Try prefix match for supermodel animations
            for k in self._anims:
                if k.startswith(key) or key.startswith(k):
                    anim = self._anims[k]
                    break
        if anim is None:
            log.debug(f"AnimationPlayer: animation '{name}' not found")
            return False

        # Don't restart if same animation is already playing (unless forced)
        if (not force_restart and self._current is anim
                and not self._current_state.elapsed_cnt):
            return True

        # Save current as last (for transition blending)
        if self._current is not None:
            self._last = self._current
            self._last_state.elapsed = self._current_state.elapsed
            self._last_state.loop    = self._current_state.loop
            # Start transition if the new animation has a transition time
            trans = getattr(anim, 'transition', 0.0) or 0.0
            if trans > 0.0 and self._last is not None:
                self._in_transition  = True
                self._trans_elapsed  = 0.0

        self._current = anim
        self._current_state.reset(loop=loop)
        log.debug(f"AnimationPlayer: playing '{name}' loop={loop} length={getattr(anim,'length',0):.2f}s")
        return True

    def play_overlay(self, name: str, loop: bool = False) -> bool:
        """Play an overlay (upper body / additive) animation."""
        key = name.lower().strip()
        anim = self._anims.get(key)
        if anim is None:
            return False
        self._overlay = anim
        self._overlay_state.reset(loop=loop)
        return True

    def stop(self) -> None:
        """Stop the current animation."""
        self._current  = None
        self._last     = None
        self._overlay  = None
        self._in_transition = False
        self._trans_elapsed = 0.0

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def set_speed(self, speed: float) -> None:
        """Set animation playback speed multiplier (1.0 = normal)."""
        self._speed = max(0.0, speed)

    def seek(self, time_s: float, pause: bool = True) -> bool:
        """
        Jump to ``time_s`` seconds in the current animation.

        This is the proper scrubber API — the animation panel's ruler calls this
        instead of directly poking ``_current_state.elapsed``.

        Args:
            time_s:  Target time in seconds (clamped to [0, length]).
            pause:   If True (default) pause playback at the seek position.

        Returns:
            True if a current animation is active, False if no animation loaded.
        """
        if self._current is None:
            return False
        length = getattr(self._current, 'length', 0.0) or 0.0
        t = max(0.0, min(time_s, length) if length > 0 else time_s)
        # Preserve last_time so event-fire window is reset
        self._current_state.last_time = t
        self._current_state.elapsed   = t
        if pause:
            self._paused = True
        # Force immediate transform recompute so viewport updates
        self._compute_transforms()
        return True

    def get_duration(self) -> float:
        """Return the duration (seconds) of the current animation, or 0.0."""
        if self._current is None:
            return 0.0
        return float(getattr(self._current, 'length', 0.0) or 0.0)

    def get_elapsed(self) -> float:
        """Return current playback position in seconds."""
        return self._current_state.elapsed

    def on_event(self, event_name: str, callback: Callable) -> None:
        """Register a callback for an animation event (e.g. 'Hit', 'FootstepLeft')."""
        self._event_callbacks[event_name.lower()] = callback

    def update(self, delta: float) -> None:
        """
        Advance the animation by `delta` seconds.
        Populates self.node_transforms with per-node transforms.
        Matching KotOR.js OdysseyModelAnimationManager.update().
        """
        if self._paused or delta <= 0.0:
            return

        effective_dt = delta * self._speed

        # ── Update transition timer ────────────────────────────────────────
        if self._in_transition:
            self._trans_elapsed += effective_dt
            trans_dur = (getattr(self._current, 'transition', 0.0) or 0.0)
            if self._trans_elapsed >= trans_dur or trans_dur <= 0.0:
                self._in_transition  = False
                self._last           = None
                self._trans_elapsed  = 0.0

        # ── Advance current animation ──────────────────────────────────────
        if self._current is not None:
            length = getattr(self._current, 'length', 0.0) or 0.0
            self._current_state.delta    = effective_dt
            self._current_state.last_time = self._current_state.elapsed
            self._current_state.elapsed  += effective_dt

            # Fire events
            self._fire_events(self._current, self._current_state)

            # Handle end of animation
            if self._current_state.elapsed >= length:
                if self._current_state.loop:
                    self._current_state.elapsed = (
                        self._current_state.elapsed % length if length > 0 else 0.0
                    )
                    self._current_state.elapsed_cnt += 1
                else:
                    self._current_state.elapsed = length
                    self._current_state.elapsed_cnt += 1

        # ── Advance last animation (for transition) ────────────────────────
        if self._last is not None and self._in_transition:
            length = getattr(self._last, 'length', 0.0) or 0.0
            self._last_state.elapsed = min(
                self._last_state.elapsed + effective_dt, length
            )

        # ── Advance overlay animation ──────────────────────────────────────
        if self._overlay is not None:
            length = getattr(self._overlay, 'length', 0.0) or 0.0
            self._overlay_state.delta    = effective_dt
            self._overlay_state.elapsed += effective_dt
            if self._overlay_state.elapsed >= length:
                if self._overlay_state.loop:
                    self._overlay_state.elapsed = (
                        self._overlay_state.elapsed % length if length > 0 else 0.0
                    )
                else:
                    self._overlay = None
                    self._overlay_state.elapsed = 0.0

        # ── Compute per-node transforms ────────────────────────────────────
        self._compute_transforms()

    def _fire_events(self, anim: Any, state: AnimationState) -> None:
        """Fire animation events in [last_time, elapsed] window."""
        events = getattr(anim, 'events', [])
        if not events:
            return
        for i, evt in enumerate(events):
            evt_time = getattr(evt, 'time', None)
            if evt_time is None:
                evt_time = getattr(evt, 'length', 0.0)
            evt_name = (getattr(evt, 'name', '') or '').lower()
            if state.last_time <= evt_time < state.elapsed:
                cb = self._event_callbacks.get(evt_name)
                if cb:
                    try:
                        cb(evt_name, evt_time)
                    except Exception as e:
                        log.debug(f"Event callback '{evt_name}' error: {e}")

    def _compute_transforms(self) -> None:
        """
        Compute per-node transforms from current + overlay animations.
        Stores results in self.node_transforms.
        """
        new_transforms: Dict[str, NodeTransform] = {}

        # Compute from current animation
        if self._current is not None:
            self._sample_anim_into(
                self._current, self._current_state,
                new_transforms
            )

        # Blend from transition (last animation cross-fade)
        if self._in_transition and self._last is not None:
            last_transforms: Dict[str, NodeTransform] = {}
            self._sample_anim_into(self._last, self._last_state, last_transforms)

            trans_dur = getattr(self._current, 'transition', 0.25) or 0.25
            blend = min(1.0, self._trans_elapsed / trans_dur)

            for node_name, last_xf in last_transforms.items():
                cur_xf = new_transforms.get(node_name)
                if cur_xf is not None:
                    new_transforms[node_name] = last_xf.lerp_toward(cur_xf, blend)
                else:
                    new_transforms[node_name] = last_xf.lerp_toward(
                        NodeTransform(), blend
                    )

        # Apply overlay animation (only overrides position + orientation)
        if self._overlay is not None:
            overlay_transforms: Dict[str, NodeTransform] = {}
            self._sample_anim_into(
                self._overlay, self._overlay_state, overlay_transforms
            )
            for node_name, ov_xf in overlay_transforms.items():
                new_transforms[node_name] = ov_xf

        self.node_transforms = new_transforms

    def _sample_anim_into(self, anim: Any, state: AnimationState,
                          out: Dict[str, NodeTransform]) -> None:
        """
        Sample all nodes of `anim` at state.elapsed and put results in `out`.
        """
        elapsed = state.elapsed
        root_node = getattr(anim, 'root_node', None)
        if root_node is None:
            return

        # Walk the animation node tree
        stack = [root_node]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            name = (getattr(node, 'name', '') or '').lower()
            controllers = getattr(node, 'controllers', {}) or {}

            xf = NodeTransform()

            # Position
            pos_rows = controllers.get(CTRL_POSITION)
            if pos_rows:
                xf.position = sample_position(pos_rows, elapsed,
                                               default=getattr(node, 'position', (0,0,0)) or (0,0,0))
            else:
                p = getattr(node, 'position', (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)
                xf.position = (float(p[0]), float(p[1]), float(p[2])) if len(p) >= 3 else (0,0,0)

            # Orientation
            ori_rows = controllers.get(CTRL_ORIENTATION)
            if ori_rows:
                xf.orientation = sample_orientation(ori_rows, elapsed,
                                                     default=getattr(node, 'rotation', (0,0,0,1)) or (0,0,0,1))
            else:
                r = getattr(node, 'rotation', (0.0, 0.0, 0.0, 1.0)) or (0,0,0,1)
                xf.orientation = (float(r[0]), float(r[1]), float(r[2]), float(r[3])) if len(r) >= 4 else (0,0,0,1)

            # Scale
            scale_rows = controllers.get(CTRL_SCALE)
            if scale_rows:
                xf.scale = sample_scale(scale_rows, elapsed)

            # Alpha
            alpha_rows = controllers.get(CTRL_ALPHA) or controllers.get(CTRL_ALPHA_OLD)
            if alpha_rows:
                xf.alpha = sample_alpha(alpha_rows, elapsed,
                                        default=getattr(node, 'alpha', 1.0))
            else:
                xf.alpha = getattr(node, 'alpha', 1.0)

            if name:
                out[name] = xf

            # Recurse into children
            for child in getattr(node, 'children', []):
                stack.append(child)


# ─────────────────────────────────────────────────────────────────────────────
#  AnimationSet  — collection of animation players for scene entities
# ─────────────────────────────────────────────────────────────────────────────

class AnimationSet:
    """
    Manages animation players for all entities in a scene.

    Usage::
        anim_set = AnimationSet()
        player = anim_set.get_or_create(entity_id, mesh_data.animations)
        player.play("cpause1", loop=True)
        # each frame:
        anim_set.update_all(delta)
    """

    def __init__(self):
        self._players: Dict[int, AnimationPlayer] = {}

    def get_or_create(self, entity_id: int,
                      animations: List[Any]) -> AnimationPlayer:
        if entity_id not in self._players:
            self._players[entity_id] = AnimationPlayer(animations)
        return self._players[entity_id]

    def get(self, entity_id: int) -> Optional[AnimationPlayer]:
        return self._players.get(entity_id)

    def remove(self, entity_id: int) -> None:
        self._players.pop(entity_id, None)

    def update_all(self, delta: float) -> None:
        """Update all active animation players."""
        for player in self._players.values():
            player.update(delta)

    def clear(self) -> None:
        self._players.clear()

    def __len__(self) -> int:
        return len(self._players)


# ─────────────────────────────────────────────────────────────────────────────
#  Default KotOR animation names (from animations.2da / KotOR.js)
# ─────────────────────────────────────────────────────────────────────────────

# Standard creature animation names used across all KotOR creatures
KOTOR_ANIMATIONS = {
    # Idle states
    'PAUSE1':     'cpause1',
    'PAUSE2':     'cpause2',
    'LOOPING_PAUSE': 'cpause1',

    # Movement
    'WALK':       'cwalk',
    'RUN':        'crun',
    'SNEAK_WALK': 'csneakwlk',

    # Combat ready
    'READY':      'creadygrenade',
    'CREADY':     'cready',

    # Death
    'DEAD1':      'cdead1',
    'DEAD2':      'cdead2',

    # Interactions
    'TALK_NORMAL':   'tlknorm',
    'TALK_FORCEFUL': 'tlkforce',
    'TALK_PLEADING': 'tlkplead',
    'LISTEN':        'tlklstn',

    # Door animations
    'DOOR_OPEN1':   'opening1',
    'DOOR_OPEN2':   'opening2',
    'DOOR_CLOSE1':  'closing1',
    'DOOR_CLOSE2':  'closing2',
    'DOOR_OPENED':  'opened1',
    'DOOR_CLOSED':  'closed',

    # Placeable
    'DEFAULT':      'default',
    'PLCD_OPEN':    'open',
    'PLCD_CLOSE':   'close',
    'PLCD_ACTIVATE':'activate',
}

# Map of model classification → default idle animation name
CLASSIFICATION_DEFAULT_ANIM = {
    0:  'cpause1',  # character
    1:  'cpause1',  # character 2
    2:  'default',  # door
    4:  'default',  # placeable
    5:  'cpause1',  # creature
    6:  'cpause1',  # NPC
}


def get_default_idle_animation(mesh_data: Any) -> str:
    """
    Return the best default idle animation name for a model.
    Checks model classification and available animations.
    """
    if mesh_data is None:
        return 'cpause1'

    classification = getattr(mesh_data, 'classification', 0) or 0
    default_name = CLASSIFICATION_DEFAULT_ANIM.get(classification, 'cpause1')

    anims = getattr(mesh_data, 'animations', [])
    anim_names = [getattr(a, 'name', '').lower() for a in (anims or [])]

    # Try the default first
    if default_name in anim_names:
        return default_name

    # Fall back to common idles
    for candidate in ['cpause1', 'cpause2', 'pause1', 'idle', 'default']:
        if candidate in anim_names:
            return candidate

    # Use first available animation
    if anim_names:
        return anim_names[0]

    return 'cpause1'
