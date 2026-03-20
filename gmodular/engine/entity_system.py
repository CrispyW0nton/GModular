"""
GModular — Entity System
=========================
Full 3D entity management for placeables, doors, creatures, and other
KotOR game objects. Each entity is a first-class scene object with:
  - A loaded 3D model (MDL)
  - Animation playback (via AnimationPlayer)
  - State machine (open/closed, alive/dead, idle/walk)
  - Interaction logic (click to open door, examine placeable, talk to creature)
  - Visual feedback (selection highlight, outline shader)

Architecture derived from:
  - KotOR.js ModulePlaceable.ts, ModuleDoor.ts, ModuleCreature.ts (KobaltBlu)
  - PyKotor GL scene.py buildCache() (NickHugi)
  - KotOR.js ModuleObject.ts update() pattern

Entity lifecycle:
  1. Created from GIT data (resref, position, bearing)
  2. Model loaded asynchronously (MDLParser → MeshData)
  3. Appearance looked up from 2DA tables
  4. Animation started (idle)
  5. Updated each frame (animation, state machine)
  6. Rendered via VAO handles from the viewport renderer
"""

from __future__ import annotations
import math
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import IntEnum, auto

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Entity states
# ─────────────────────────────────────────────────────────────────────────────

class DoorState(IntEnum):
    CLOSED  = 0
    OPENING = 1
    OPEN    = 2
    CLOSING = 3
    LOCKED  = 4
    BROKEN  = 5

class CreatureState(IntEnum):
    IDLE        = 0
    WALKING     = 1
    RUNNING     = 2
    TALKING     = 3
    ATTACKING   = 4
    DEAD        = 5
    UNCONSCIOUS = 6
    PATROLLING  = 7   # actively following a patrol route

class PlaceableState(IntEnum):
    DEFAULT  = 0
    OPEN     = 1
    CLOSED   = 2
    ACTIVE   = 3
    INACTIVE = 4
    BROKEN   = 5


# ─────────────────────────────────────────────────────────────────────────────
#  Door animations (KotOR door animation names)
# ─────────────────────────────────────────────────────────────────────────────

DOOR_ANIM_OPEN1   = 'opening1'
DOOR_ANIM_OPEN2   = 'opening2'
DOOR_ANIM_CLOSE1  = 'closing1'
DOOR_ANIM_CLOSE2  = 'closing2'
DOOR_ANIM_OPENED  = 'opened1'
DOOR_ANIM_CLOSED  = 'closed'
DOOR_ANIM_LOCKED  = 'locked'
DOOR_ANIM_TRANS   = 'trans'

# Creature animation → KotOR animation name mapping
CREATURE_ANIM_MAP = {
    CreatureState.IDLE:     ('cpause1', True),   # (anim_name, loop)
    CreatureState.WALKING:  ('cwalk',   True),
    CreatureState.RUNNING:  ('crun',    True),
    CreatureState.TALKING:  ('tlknorm', True),
    CreatureState.DEAD:     ('cdead1',  False),
}


# ─────────────────────────────────────────────────────────────────────────────
#  Entity3D  (base class for all interactive entities)
# ─────────────────────────────────────────────────────────────────────────────

class Entity3D:
    """
    Base class for all interactive 3D entities in the scene.
    Matches the pattern of KotOR.js ModuleObject.

    Subclasses: Door3D, Placeable3D, Creature3D, Waypoint3D
    """

    def __init__(self, entity_id: int, entity_type: int,
                 git_data: Any = None):
        self.entity_id   = entity_id
        self.entity_type = entity_type
        self.git_data    = git_data

        # Identity
        self.resref:  str  = ""
        self.tag:     str  = ""
        self.label:   str  = ""

        # Transform
        self._x:      float = 0.0
        self._y:      float = 0.0
        self._z:      float = 0.0
        self._bearing: float = 0.0   # yaw in radians
        self._scale:  float = 1.0

        # Model
        self.model_resref:  str  = ""
        self.mesh_data:     Any  = None
        self.model_loaded:  bool = False

        # Animation
        self._animation_player: Optional[Any] = None   # AnimationPlayer
        self.current_anim: str  = ""
        self.anim_loop:    bool = True

        # Visual state
        self.selected:   bool = False
        self.highlighted:bool = False
        self.visible:    bool = True

        # Interaction
        self.interactable: bool = True
        self._on_interact: Optional[Callable] = None
        self._on_select:   Optional[Callable] = None

        # VAO render handles (managed by viewport)
        self.vao_handles: List[Any] = []

        # AABB in world space
        self.world_aabb_min: Tuple[float,float,float] = (-0.4, -0.4, 0.0)
        self.world_aabb_max: Tuple[float,float,float] = ( 0.4,  0.4, 1.8)

        # Read from git_data if provided
        if git_data:
            self._load_from_git()

    def _load_from_git(self) -> None:
        """Extract basic fields from git data object."""
        try:
            pos = getattr(self.git_data, 'position', None)
            if pos:
                self._x = float(pos.x)
                self._y = float(pos.y)
                self._z = float(pos.z)
            self.resref   = str(getattr(self.git_data, 'resref', '') or '')
            self.tag      = str(getattr(self.git_data, 'tag', '') or '')
            self._bearing = float(getattr(self.git_data, 'bearing', 0.0) or 0.0)
        except Exception as _e:
            log.debug("Entity3D._init_from_git_data: %s", _e)

    # ── Transform properties ──────────────────────────────────────────────────

    @property
    def position(self) -> Tuple[float, float, float]:
        return (self._x, self._y, self._z)

    @position.setter
    def position(self, v: Tuple[float, float, float]) -> None:
        self._x, self._y, self._z = float(v[0]), float(v[1]), float(v[2])
        self._update_aabb()

    @property
    def bearing(self) -> float:
        return self._bearing

    @bearing.setter
    def bearing(self, rad: float) -> None:
        self._bearing = float(rad)

    @property
    def bearing_degrees(self) -> float:
        return math.degrees(self._bearing)

    @bearing_degrees.setter
    def bearing_degrees(self, deg: float) -> None:
        self._bearing = math.radians(deg)

    def _update_aabb(self) -> None:
        """Update world AABB when position changes."""
        mx, my, mz = self.world_aabb_min
        Mx, My, Mz = self.world_aabb_max
        dx, dy, dz = self._x, self._y, self._z
        self.world_aabb_min = (dx + mx, dy + my, dz + mz)
        self.world_aabb_max = (dx + Mx, dy + My, dz + Mz)

    # ── Model loading ─────────────────────────────────────────────────────────

    def load_model(self, mdl_path: str, mdx_path: str = "") -> bool:
        """Load MDL geometry data for this entity."""
        try:
            from ..formats.mdl_parser import MDLParser
            self.mesh_data  = MDLParser.parse_files(mdl_path, mdx_path)
            self.model_loaded = True
            # Update AABB from model bounds
            if self.mesh_data:
                bb_min = getattr(self.mesh_data, 'bb_min', None)
                bb_max = getattr(self.mesh_data, 'bb_max', None)
                if bb_min and bb_max:
                    self.world_aabb_min = (
                        self._x + bb_min[0], self._y + bb_min[1],
                        self._z + bb_min[2]
                    )
                    self.world_aabb_max = (
                        self._x + bb_max[0], self._y + bb_max[1],
                        self._z + bb_max[2]
                    )
            log.debug(f"{self.__class__.__name__} '{self.resref}': model loaded")
            return True
        except Exception as e:
            log.debug(f"{self.__class__.__name__} '{self.resref}': model load failed — {e}")
            return False

    def find_model_in_dir(self, game_dir: str, name: str = "") -> Tuple[str, str]:
        """
        Search game_dir for {name}.mdl / {name}.mdx.
        Returns (mdl_path, mdx_path) or ("", "").
        """
        if not game_dir or not name:
            return "", ""
        try:
            name_l = name.lower()
            # Build case-insensitive index
            if not hasattr(self, '_dir_index') or self._dir_index_dir != game_dir:
                index = {}
                for root, _, files in os.walk(game_dir):
                    for f in files:
                        index[f.lower()] = os.path.join(root, f)
                self._dir_index = index
                self._dir_index_dir = game_dir

            mdl_path = self._dir_index.get(name_l + '.mdl', '')
            mdx_path = self._dir_index.get(name_l + '.mdx', '')
            return mdl_path, mdx_path
        except Exception:
            return "", ""

    # ── Animation ─────────────────────────────────────────────────────────────

    def setup_animation_player(self) -> None:
        """Create AnimationPlayer from loaded mesh_data animations."""
        if self.mesh_data is None:
            return
        try:
            from .animation_system import AnimationPlayer
            anims = getattr(self.mesh_data, 'animations', [])
            self._animation_player = AnimationPlayer(anims or [])
        except Exception as e:
            log.debug(f"setup_animation_player: {e}")

    def play_animation(self, name: str, loop: bool = True) -> bool:
        """Play the named animation on this entity."""
        if self._animation_player is None and self.mesh_data is not None:
            self.setup_animation_player()
        if self._animation_player is None:
            return False
        success = self._animation_player.play(name, loop=loop)
        if success:
            self.current_anim = name
            self.anim_loop    = loop
        return success

    def update_animation(self, delta: float) -> None:
        """Advance animation by delta seconds."""
        if self._animation_player:
            self._animation_player.update(delta)

    @property
    def node_transforms(self) -> Dict[str, Any]:
        """Return current per-node transforms from animation player."""
        if self._animation_player:
            return self._animation_player.node_transforms
        return {}

    # ── Interaction ───────────────────────────────────────────────────────────

    def on_interact(self, callback: Callable) -> None:
        self._on_interact = callback

    def on_select(self, callback: Callable) -> None:
        self._on_select = callback

    def interact(self) -> bool:
        """Called when player interacts with this entity."""
        if self._on_interact:
            try:
                self._on_interact(self)
                return True
            except Exception as e:
                log.debug(f"Entity interact callback error: {e}")
        return False

    def select(self) -> None:
        self.selected = True
        if self._on_select:
            try:
                self._on_select(self)
            except Exception as _e:
                log.debug("Entity3D.select callback error: %s", _e)

    def deselect(self) -> None:
        self.selected = False

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, delta: float) -> None:
        """Base update — advance animation."""
        self.update_animation(delta)


# ─────────────────────────────────────────────────────────────────────────────
#  Door3D
# ─────────────────────────────────────────────────────────────────────────────

class Door3D(Entity3D):
    """
    A door entity with open/close animation state machine.
    Matches KotOR.js ModuleDoor behavior.
    """

    ENTITY_TYPE = 3  # ENTITY_DOOR

    def __init__(self, entity_id: int, git_data: Any = None):
        super().__init__(entity_id, self.ENTITY_TYPE, git_data)
        self.state:    DoorState = DoorState.CLOSED
        self.locked:   bool = False
        self.lock_dc:  int  = 0
        self.open_on_click: bool = True

        # Door model dimensions (set from appearance)
        self.world_aabb_min = (-0.6, -0.15, 0.0)
        self.world_aabb_max = ( 0.6,  0.15, 2.5)

    def _load_from_git(self) -> None:
        super()._load_from_git()
        if self.git_data:
            self.locked  = bool(getattr(self.git_data, 'lock_able', False))
            self.lock_dc = int(getattr(self.git_data, 'lock_dc', 0) or 0)

    def _setup_idle_animation(self) -> None:
        """Start the door in its default (closed) animation."""
        if self.state == DoorState.CLOSED:
            for name in [DOOR_ANIM_CLOSED, 'default', 'close']:
                if self.play_animation(name, loop=False):
                    return
        elif self.state == DoorState.OPEN:
            for name in [DOOR_ANIM_OPENED, 'opened', 'open']:
                if self.play_animation(name, loop=False):
                    return

    def open(self, force: bool = False) -> bool:
        """Open the door (plays opening animation)."""
        if self.state in (DoorState.OPEN, DoorState.OPENING):
            return True
        if self.locked and not force:
            self.play_animation(DOOR_ANIM_LOCKED, loop=False)
            return False

        self.state = DoorState.OPENING
        if not self.play_animation(DOOR_ANIM_OPEN1, loop=False):
            self.play_animation('open', loop=False)

        # Schedule completion (in real engine this fires on anim end event)
        log.debug(f"Door '{self.resref}': opening")
        return True

    def close(self) -> bool:
        """Close the door (plays closing animation)."""
        if self.state in (DoorState.CLOSED, DoorState.CLOSING):
            return True
        self.state = DoorState.CLOSING
        if not self.play_animation(DOOR_ANIM_CLOSE1, loop=False):
            self.play_animation('close', loop=False)
        log.debug(f"Door '{self.resref}': closing")
        return True

    def toggle(self) -> None:
        """Toggle door open/closed."""
        if self.state in (DoorState.CLOSED, DoorState.LOCKED):
            self.open()
        else:
            self.close()

    def update(self, delta: float) -> None:
        self.update_animation(delta)
        # State machine: transition from OPENING → OPEN when animation ends
        if self._animation_player:
            state = self._animation_player._current_state
            if (self.state == DoorState.OPENING and
                    state.elapsed_cnt > 0):
                self.state = DoorState.OPEN
                for name in [DOOR_ANIM_OPENED, 'opened1', 'opened']:
                    if self.play_animation(name, loop=False):
                        break
            elif (self.state == DoorState.CLOSING and
                    state.elapsed_cnt > 0):
                self.state = DoorState.CLOSED
                for name in [DOOR_ANIM_CLOSED, 'closed']:
                    if self.play_animation(name, loop=False):
                        break


# ─────────────────────────────────────────────────────────────────────────────
#  Placeable3D
# ─────────────────────────────────────────────────────────────────────────────

class Placeable3D(Entity3D):
    """
    A placeable entity (crates, computers, terminals, decorations, etc.).
    Matches KotOR.js ModulePlaceable behavior.
    """

    ENTITY_TYPE = 2  # ENTITY_PLACEABLE

    def __init__(self, entity_id: int, git_data: Any = None):
        super().__init__(entity_id, self.ENTITY_TYPE, git_data)
        self.state:     PlaceableState = PlaceableState.DEFAULT
        self.has_inv:   bool = False
        self.usable:    bool = True
        self.static:    bool = False
        self.has_trap:  bool = False

        self.world_aabb_min = (-0.4, -0.4, 0.0)
        self.world_aabb_max = ( 0.4,  0.4, 1.2)

    def _load_from_git(self) -> None:
        super()._load_from_git()
        if self.git_data:
            self.has_inv = bool(getattr(self.git_data, 'has_inventory', False))

    def _setup_idle_animation(self) -> None:
        for name in ['default', 'cpause1', 'idle']:
            if self.play_animation(name, loop=True):
                return

    def activate(self) -> None:
        self.state = PlaceableState.ACTIVE
        for name in ['activate', 'open', 'use']:
            if self.play_animation(name, loop=False):
                return

    def deactivate(self) -> None:
        self.state = PlaceableState.INACTIVE
        for name in ['deactivate', 'close', 'default']:
            if self.play_animation(name, loop=True):
                return

    def update(self, delta: float) -> None:
        self.update_animation(delta)


# ─────────────────────────────────────────────────────────────────────────────
#  Creature3D
# ─────────────────────────────────────────────────────────────────────────────

class Creature3D(Entity3D):
    """
    A creature / NPC entity with full animation state machine.
    Matches KotOR.js ModuleCreature behavior.

    Supports:
      - Body + head model combination
      - Idle / walk / run / talk animations
      - Faction (for combat AI stub)
      - Appearance table lookup
    """

    ENTITY_TYPE = 4  # ENTITY_CREATURE

    def __init__(self, entity_id: int, git_data: Any = None):
        super().__init__(entity_id, self.ENTITY_TYPE, git_data)
        self.state:       CreatureState = CreatureState.IDLE
        self.race:        int   = 0
        self.gender:      int   = 0
        self.appearance:  int   = 0
        self.faction:     int   = 0
        self.hp:          int   = 10
        self.max_hp:      int   = 10
        self.walk_rate:   float = 2.5    # m/s walking speed
        self.run_rate:    float = 5.0    # m/s running speed
        self.is_player:   bool  = False
        self.head_model_resref: str = ""

        self.world_aabb_min = (-0.35, -0.35, 0.0)
        self.world_aabb_max = ( 0.35,  0.35, 1.8)

        # Head animation player (separate from body)
        self._head_player: Optional[Any] = None

        # ── Patrol AI ────────────────────────────────────────────────────────
        # patrol_waypoints: list of (x, y, z) tuples defining the route.
        # Set by PatrolPathEditor via ViewportWidget.set_patrol_path or the
        # MCP kotor_play_animation tool.
        self.patrol_waypoints: list = []    # [(x,y,z), ...]
        self._patrol_idx:  int   = 0        # index of current target waypoint
        self._patrol_wait: float = 0.0      # seconds remaining at current stop
        self.patrol_dwell: float = 1.5      # seconds to dwell at each waypoint
        self.patrol_arrival_radius: float = 0.25   # arrive when within this distance

    def _load_from_git(self) -> None:
        super()._load_from_git()
        if self.git_data:
            self.appearance = int(getattr(self.git_data, 'appearance', 0) or 0)
            self.faction    = int(getattr(self.git_data, 'faction_id', 0) or 0)
            self.race       = int(getattr(self.git_data, 'race', 0) or 0)
            self.gender     = int(getattr(self.git_data, 'gender', 0) or 0)

    def _setup_idle_animation(self) -> None:
        """Start idle animation (cpause1 for characters)."""
        for name in ['cpause1', 'cpause2', 'pause1', 'idle']:
            if self.play_animation(name, loop=True):
                self.state = CreatureState.IDLE
                return

    def start_walk(self) -> None:
        if self.state in (CreatureState.WALKING,):
            return
        self.state = CreatureState.WALKING
        for name in ['cwalk', 'walk']:
            if self.play_animation(name, loop=True):
                return

    def start_run(self) -> None:
        if self.state == CreatureState.RUNNING:
            return
        self.state = CreatureState.RUNNING
        for name in ['crun', 'run']:
            if self.play_animation(name, loop=True):
                return

    def start_idle(self) -> None:
        if self.state == CreatureState.IDLE:
            return
        self.state = CreatureState.IDLE
        self._setup_idle_animation()

    def start_talk(self) -> None:
        self.state = CreatureState.TALKING
        for name in ['tlknorm', 'tlkforce', 'talk']:
            if self.play_animation(name, loop=True):
                return

    # ── Patrol AI ─────────────────────────────────────────────────────────────

    def set_patrol_route(self, waypoints: list) -> None:
        """
        Set the patrol route for this creature.

        Parameters
        ----------
        waypoints : list
            List of (x, y, z) tuples.  Passing an empty list disables patrolling
            and returns the creature to idle.
        """
        self.patrol_waypoints = list(waypoints)
        self._patrol_idx  = 0
        self._patrol_wait = 0.0
        if self.patrol_waypoints:
            self.start_walk()
            self.state = CreatureState.PATROLLING   # override WALKING set by start_walk
        else:
            self.start_idle()

    def _patrol_tick(self, delta: float) -> None:
        """Advance patrol AI by delta seconds."""
        wps = self.patrol_waypoints
        if not wps or self.state == CreatureState.DEAD:
            return

        # ── Dwell phase — wait at current waypoint before moving on ──────────
        if self._patrol_wait > 0.0:
            self._patrol_wait -= delta
            if self._patrol_wait <= 0.0:
                self._patrol_wait = 0.0
                # Advance to next waypoint
                self._patrol_idx = (self._patrol_idx + 1) % len(wps)
                self.start_walk()
            else:
                # Idle while dwelling
                if self.state != CreatureState.IDLE:
                    self.start_idle()
            return

        # ── Move phase — walk toward current waypoint ─────────────────────────
        tx, ty, tz = wps[self._patrol_idx]
        dx = tx - self._x
        dy = ty - self._y
        dist = (dx * dx + dy * dy) ** 0.5

        if dist <= self.patrol_arrival_radius:
            # Arrived — start dwell timer
            self._x = tx
            self._y = ty
            self._z = tz
            self._patrol_wait = self.patrol_dwell
            self._update_aabb()
            if self.state != CreatureState.IDLE:
                self.start_idle()
            return

        # Face the target
        import math
        self._bearing = math.atan2(dy, dx)

        # Step toward target
        speed = self.walk_rate
        if dist > 0:
            step = min(speed * delta, dist)
            self._x += dx / dist * step
            self._y += dy / dist * step
            self._update_aabb()

        if self.state not in (CreatureState.WALKING, CreatureState.PATROLLING):
            self.start_walk()
            self.state = CreatureState.PATROLLING

    def die(self) -> None:
        if self.state == CreatureState.DEAD:
            return
        self.state = CreatureState.DEAD
        self.hp = 0
        for name in ['cdead1', 'cdead2', 'dead']:
            if self.play_animation(name, loop=False):
                return

    def is_alive(self) -> bool:
        return self.hp > 0 and self.state != CreatureState.DEAD

    def update(self, delta: float) -> None:
        # Patrol AI tick — runs before animation update so state changes
        # are reflected in the animation for the same frame.
        if self.patrol_waypoints and self.state not in (CreatureState.DEAD,
                                                         CreatureState.UNCONSCIOUS,
                                                         CreatureState.TALKING):
            self._patrol_tick(delta)

        self.update_animation(delta)
        if self._head_player:
            self._head_player.update(delta)


# ─────────────────────────────────────────────────────────────────────────────
#  Waypoint3D
# ─────────────────────────────────────────────────────────────────────────────

class Waypoint3D(Entity3D):
    """A waypoint (patrol marker, spawn point, etc.)."""

    ENTITY_TYPE = 5  # ENTITY_WAYPOINT

    def __init__(self, entity_id: int, git_data: Any = None):
        super().__init__(entity_id, self.ENTITY_TYPE, git_data)
        self.world_aabb_min = (-0.2, -0.2, 0.0)
        self.world_aabb_max = ( 0.2,  0.2, 0.3)
        self.map_note: str = ""

    def _load_from_git(self) -> None:
        super()._load_from_git()
        if self.git_data:
            self.map_note = str(getattr(self.git_data, 'map_note', '') or '')

    def update(self, delta: float) -> None:
        pass  # Waypoints don't animate


# ─────────────────────────────────────────────────────────────────────────────
#  EntityRegistry  — manages all entities in the scene
# ─────────────────────────────────────────────────────────────────────────────

class EntityRegistry:
    """
    Registry of all Entity3D objects in the current module.

    Provides:
      - Creation from GIT data
      - Model loading (from game directory)
      - Per-frame update (animation)
      - Query by type, resref, tag
    """

    def __init__(self):
        self._entities: Dict[int, Entity3D] = {}
        self._next_id: int = 1

    def _alloc_id(self) -> int:
        eid = self._next_id
        self._next_id += 1
        return eid

    # ── Population ────────────────────────────────────────────────────────────

    def populate_from_git(self, git_data: Any) -> int:
        """Create entities from all GIT object lists."""
        self.clear()
        count = 0

        creators = [
            ('creatures',  self._create_creature),
            ('placeables', self._create_placeable),
            ('doors',      self._create_door),
            ('waypoints',  self._create_waypoint),
        ]

        for attr, creator in creators:
            items = getattr(git_data, attr, []) or []
            for item in items:
                try:
                    ent = creator(item)
                    if ent:
                        self._entities[ent.entity_id] = ent
                        count += 1
                except Exception as e:
                    log.debug(f"EntityRegistry: error creating entity: {e}")

        log.info(f"EntityRegistry: created {count} entities")
        return count

    def _create_creature(self, git: Any) -> Optional[Creature3D]:
        ent = Creature3D(self._alloc_id(), git)
        ent._setup_idle_animation() if ent.model_loaded else None
        return ent

    def _create_placeable(self, git: Any) -> Optional[Placeable3D]:
        ent = Placeable3D(self._alloc_id(), git)
        return ent

    def _create_door(self, git: Any) -> Optional[Door3D]:
        ent = Door3D(self._alloc_id(), git)
        return ent

    def _create_waypoint(self, git: Any) -> Optional[Waypoint3D]:
        ent = Waypoint3D(self._alloc_id(), git)
        return ent

    # ── Model loading ─────────────────────────────────────────────────────────

    def load_models_from_dir(self, game_dir: str,
                              twoda_lookup: Optional[Any] = None) -> int:
        """
        Attempt to load MDL models for all entities that have a model_resref.
        Uses 2DA tables for appearance lookup when provided.
        Returns count of loaded models.
        """
        loaded = 0
        for ent in self._entities.values():
            model_name = ent.model_resref
            if not model_name:
                model_name = self._resolve_model_name(ent, twoda_lookup)
                if model_name:
                    ent.model_resref = model_name
            if not model_name:
                continue
            mdl, mdx = ent.find_model_in_dir(game_dir, model_name)
            if mdl and os.path.exists(mdl):
                if ent.load_model(mdl, mdx):
                    ent.setup_animation_player()
                    # Start appropriate idle animation
                    if isinstance(ent, Creature3D):
                        ent._setup_idle_animation()
                    elif isinstance(ent, Placeable3D):
                        ent._setup_idle_animation()
                    elif isinstance(ent, Door3D):
                        ent._setup_idle_animation()
                    loaded += 1
        log.info(f"EntityRegistry: {loaded}/{len(self._entities)} models loaded")
        return loaded

    def _resolve_model_name(self, ent: Entity3D,
                             twoda_lookup: Optional[Any]) -> str:
        """
        Look up model name from 2DA for creature/door/placeable.
        Returns empty string if not found.
        """
        if twoda_lookup is None:
            return ""
        try:
            if isinstance(ent, Creature3D):
                row = twoda_lookup.get_row('appearance', ent.appearance)
                if row:
                    return str(row.get('race', '') or row.get('modelb', '') or '')
            elif isinstance(ent, Door3D):
                # Door model from genericdoors.2da
                appearance = int(getattr(ent.git_data, 'appearance', 0) or 0)
                row = twoda_lookup.get_row('genericdoors', appearance)
                if row:
                    return str(row.get('modelname', '') or '')
            elif isinstance(ent, Placeable3D):
                # Placeable model from placeables.2da
                appearance = int(getattr(ent.git_data, 'appearance', 0) or 0)
                row = twoda_lookup.get_row('placeables', appearance)
                if row:
                    return str(row.get('modelname', '') or '')
        except Exception as _e:
            log.debug("EntityRegistry._resolve_model_name: %s", _e)
        return ""

    # ── Update ────────────────────────────────────────────────────────────────

    def update_all(self, delta: float) -> None:
        """Update all entities (animation, state machines)."""
        for ent in self._entities.values():
            try:
                ent.update(delta)
            except Exception as _e:
                log.debug("EntityRegistry.update_all entity %d: %s", ent.entity_id, _e)

    # ── Queries ───────────────────────────────────────────────────────────────

    def get(self, entity_id: int) -> Optional[Entity3D]:
        return self._entities.get(entity_id)

    def get_by_type(self, entity_type: int) -> List[Entity3D]:
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    def get_by_tag(self, tag: str) -> List[Entity3D]:
        tag_l = tag.lower()
        return [e for e in self._entities.values() if e.tag.lower() == tag_l]

    def get_by_resref(self, resref: str) -> List[Entity3D]:
        resref_l = resref.lower()
        return [e for e in self._entities.values() if e.resref.lower() == resref_l]

    def get_doors(self) -> List[Door3D]:
        return [e for e in self._entities.values() if isinstance(e, Door3D)]

    def get_creatures(self) -> List[Creature3D]:
        return [e for e in self._entities.values() if isinstance(e, Creature3D)]

    def get_placeables(self) -> List[Placeable3D]:
        return [e for e in self._entities.values() if isinstance(e, Placeable3D)]

    @property
    def entities(self) -> List[Entity3D]:
        return list(self._entities.values())

    def __len__(self) -> int:
        return len(self._entities)

    def clear(self) -> None:
        self._entities.clear()
        self._next_id = 1

    # ── Interaction summary ───────────────────────────────────────────────────

    def get_summary(self) -> str:
        doors     = len(self.get_doors())
        creatures = len(self.get_creatures())
        placeables= len(self.get_placeables())
        return (
            f"{len(self._entities)} entities: "
            f"{creatures} creatures, {doors} doors, {placeables} placeables"
        )
