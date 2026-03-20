"""
GModular — Scene Manager
=========================
Engine-quality scene graph with proper entity management, VIS-based room
culling, bounding-volume hierarchy, and render object tracking.

Architecture derived from:
  - KotOR.js ModuleArea.ts + ModuleRoom.ts (KobaltBlu)
  - PyKotor GL scene.py buildCache() + render() (NickHugi)
  - KotOR.js GameState.ts scene update loop
  - Kotor.NET SceneManager design

This module handles:
  1. SceneRoom     — represents one loaded room (MDL + walkmesh + linked rooms)
  2. SceneEntity   — a placeable, door, creature, waypoint, etc. in the scene
  3. SceneGraph    — the full scene with rooms + entities + VIS culling
  4. RenderBucket  — sorted render order (opaque, transparent, overlay)
  5. VisibilitySystem — VIS file-based room culling (which rooms are visible
                         from each room, matching Odyssey engine's vis system)
  6. AABBTree      — fast bounding-box queries for object picking + culling

All bounding-volume operations are done in KotOR Z-up right-handed space.
"""

from __future__ import annotations
import math
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any

log = logging.getLogger(__name__)

# ─── Types ───────────────────────────────────────────────────────────────────

Vec3 = Tuple[float, float, float]


# ─────────────────────────────────────────────────────────────────────────────
#  Math helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dot3(a: Vec3, b: Vec3) -> float:
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

def _length3(v: Vec3) -> float:
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])

def _normalize3(v: Vec3) -> Vec3:
    n = _length3(v)
    if n < 1e-8: return (0.0, 0.0, 1.0)
    return (v[0]/n, v[1]/n, v[2]/n)

def _add3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0]+b[0], a[1]+b[1], a[2]+b[2])

def _sub3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])

def _scale3(v: Vec3, s: float) -> Vec3:
    return (v[0]*s, v[1]*s, v[2]*s)


# ─────────────────────────────────────────────────────────────────────────────
#  Axis-Aligned Bounding Box
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AABB:
    """Axis-aligned bounding box in world space."""
    min: Vec3 = field(default_factory=lambda: (0.0, 0.0, 0.0))
    max: Vec3 = field(default_factory=lambda: (0.0, 0.0, 0.0))

    @classmethod
    def empty(cls) -> 'AABB':
        return cls(
            min=( 1e18,  1e18,  1e18),
            max=(-1e18, -1e18, -1e18),
        )

    @property
    def center(self) -> Vec3:
        return (
            (self.min[0] + self.max[0]) * 0.5,
            (self.min[1] + self.max[1]) * 0.5,
            (self.min[2] + self.max[2]) * 0.5,
        )

    @property
    def extents(self) -> Vec3:
        return (
            (self.max[0] - self.min[0]) * 0.5,
            (self.max[1] - self.min[1]) * 0.5,
            (self.max[2] - self.min[2]) * 0.5,
        )

    @property
    def radius(self) -> float:
        e = self.extents
        return math.sqrt(e[0]*e[0] + e[1]*e[1] + e[2]*e[2])

    @property
    def is_valid(self) -> bool:
        return self.min[0] <= self.max[0]

    def expand(self, point: Vec3) -> None:
        self.min = (
            min(self.min[0], point[0]),
            min(self.min[1], point[1]),
            min(self.min[2], point[2]),
        )
        self.max = (
            max(self.max[0], point[0]),
            max(self.max[1], point[1]),
            max(self.max[2], point[2]),
        )

    def expand_aabb(self, other: 'AABB') -> None:
        if not other.is_valid:
            return
        self.expand(other.min)
        self.expand(other.max)

    def contains_point(self, p: Vec3) -> bool:
        return (
            self.min[0] <= p[0] <= self.max[0] and
            self.min[1] <= p[1] <= self.max[1] and
            self.min[2] <= p[2] <= self.max[2]
        )

    def intersects_sphere(self, center: Vec3, radius: float) -> bool:
        """Check if sphere intersects this AABB."""
        dx = max(self.min[0] - center[0], 0.0, center[0] - self.max[0])
        dy = max(self.min[1] - center[1], 0.0, center[1] - self.max[1])
        dz = max(self.min[2] - center[2], 0.0, center[2] - self.max[2])
        return dx*dx + dy*dy + dz*dz <= radius*radius

    def intersects_aabb(self, other: 'AABB') -> bool:
        return (
            self.min[0] <= other.max[0] and self.max[0] >= other.min[0] and
            self.min[1] <= other.max[1] and self.max[1] >= other.min[1] and
            self.min[2] <= other.max[2] and self.max[2] >= other.min[2]
        )

    def ray_intersect(self, origin: Vec3, direction: Vec3) -> Optional[float]:
        """
        Slab-method ray-AABB intersection.
        Returns t (ray parameter) or None if no intersection.
        """
        t_min = 0.0
        t_max = 1e18

        for i in range(3):
            d = direction[i]
            o = origin[i]
            lo = self.min[i]
            hi = self.max[i]
            if abs(d) < 1e-9:
                if o < lo or o > hi:
                    return None
            else:
                t1 = (lo - o) / d
                t2 = (hi - o) / d
                if t1 > t2:
                    t1, t2 = t2, t1
                t_min = max(t_min, t1)
                t_max = min(t_max, t2)
                if t_min > t_max:
                    return None
        return t_min


# ─────────────────────────────────────────────────────────────────────────────
#  Frustum culling (6 planes)
# ─────────────────────────────────────────────────────────────────────────────

class Frustum:
    """
    View frustum for culling.
    Built from a combined view-projection matrix (column-major, row 4×4).
    """

    def __init__(self):
        # 6 planes: each is (nx, ny, nz, d) where dot(n, p) + d >= 0 means inside
        self.planes: List[Tuple[float, float, float, float]] = []

    def from_vp_matrix(self, vp: List[float]) -> None:
        """
        Extract frustum planes from a 4×4 VP matrix stored column-major.
        vp[i] is column-major: vp[col*4 + row]
        """
        # Row-access helper: row r, col c
        def m(r: int, c: int) -> float:
            return vp[c * 4 + r]

        planes = []
        # Left  : row3 + row0
        planes.append(self._norm_plane(
            m(3,0)+m(0,0), m(3,1)+m(0,1), m(3,2)+m(0,2), m(3,3)+m(0,3)))
        # Right : row3 - row0
        planes.append(self._norm_plane(
            m(3,0)-m(0,0), m(3,1)-m(0,1), m(3,2)-m(0,2), m(3,3)-m(0,3)))
        # Bottom: row3 + row1
        planes.append(self._norm_plane(
            m(3,0)+m(1,0), m(3,1)+m(1,1), m(3,2)+m(1,2), m(3,3)+m(1,3)))
        # Top   : row3 - row1
        planes.append(self._norm_plane(
            m(3,0)-m(1,0), m(3,1)-m(1,1), m(3,2)-m(1,2), m(3,3)-m(1,3)))
        # Near  : row3 + row2
        planes.append(self._norm_plane(
            m(3,0)+m(2,0), m(3,1)+m(2,1), m(3,2)+m(2,2), m(3,3)+m(2,3)))
        # Far   : row3 - row2
        planes.append(self._norm_plane(
            m(3,0)-m(2,0), m(3,1)-m(2,1), m(3,2)-m(2,2), m(3,3)-m(2,3)))
        self.planes = planes

    @staticmethod
    def _norm_plane(a: float, b: float, c: float, d: float) -> Tuple:
        n = math.sqrt(a*a + b*b + c*c)
        if n < 1e-8:
            return (a, b, c, d)
        return (a/n, b/n, c/n, d/n)

    def test_sphere(self, center: Vec3, radius: float) -> bool:
        """Returns True if sphere is at least partially inside frustum."""
        cx, cy, cz = center
        for nx, ny, nz, d in self.planes:
            dist = nx*cx + ny*cy + nz*cz + d
            if dist < -radius:
                return False
        return True

    def test_aabb(self, box: AABB) -> bool:
        """Returns True if AABB is at least partially inside frustum."""
        cx, cy, cz = box.center
        ex, ey, ez = box.extents
        for nx, ny, nz, d in self.planes:
            # Positive vertex (maximum dot product with plane normal)
            r = abs(nx)*ex + abs(ny)*ey + abs(nz)*ez
            dist = nx*cx + ny*cy + nz*cz + d
            if dist < -r:
                return False
        return True


# ─────────────────────────────────────────────────────────────────────────────
#  Scene Entity  (one GIT object in the scene)
# ─────────────────────────────────────────────────────────────────────────────

# Entity type constants (matching KotOR.js ModuleObjectType)
ENTITY_ROOM        = 1
ENTITY_PLACEABLE   = 2
ENTITY_DOOR        = 3
ENTITY_CREATURE    = 4
ENTITY_WAYPOINT    = 5
ENTITY_TRIGGER     = 6
ENTITY_ENCOUNTER   = 7
ENTITY_SOUND       = 8
ENTITY_STORE       = 9
ENTITY_CAMERA      = 10

ENTITY_TYPE_NAMES = {
    ENTITY_ROOM:      'room',
    ENTITY_PLACEABLE: 'placeable',
    ENTITY_DOOR:      'door',
    ENTITY_CREATURE:  'creature',
    ENTITY_WAYPOINT:  'waypoint',
    ENTITY_TRIGGER:   'trigger',
    ENTITY_ENCOUNTER: 'encounter',
    ENTITY_SOUND:     'sound',
    ENTITY_STORE:     'store',
    ENTITY_CAMERA:    'camera',
}


@dataclass
class SceneEntity:
    """
    One entity in the scene (room, creature, door, placeable, etc.).

    Mirrors KotOR.js ModuleObject with key fields for editor/render use.
    """
    entity_id:   int   = 0        # Unique scene ID (1-based)
    entity_type: int   = ENTITY_PLACEABLE
    resref:      str   = ""       # Template resref
    tag:         str   = ""       # Script tag
    label:       str   = ""       # Display name

    # Transform
    position:    Vec3  = field(default_factory=lambda: (0.0, 0.0, 0.0))
    bearing:     float = 0.0      # Yaw angle in radians
    scale:       float = 1.0

    # Model
    model_resref: str  = ""       # MDL resref (without extension)
    mesh_data:    Any  = field(default=None, repr=False)  # parsed MeshData
    aabb:         AABB = field(default_factory=AABB)

    # Render handles (managed by renderer)
    vao_handles: List[Any]  = field(default_factory=list, repr=False)
    selected:    bool        = False
    visible:     bool        = True
    hidden:      bool        = False

    # Animation state
    current_animation: str   = ""
    animation_loop:    bool  = True
    anim_elapsed:      float = 0.0

    # Door-specific state
    is_open:     bool  = False
    is_locked:   bool  = False

    # Creature-specific
    npc_index:   int   = -1     # index in appearances.2da
    faction:     int   = 0

    # GIT source data reference
    git_data:    Any   = field(default=None, repr=False)

    # Room link (which room this entity belongs to)
    room_name:   str   = ""

    def __hash__(self):
        return hash(self.entity_id)

    def __eq__(self, other):
        if isinstance(other, SceneEntity):
            return self.entity_id == other.entity_id
        return NotImplemented

    @property
    def type_name(self) -> str:
        return ENTITY_TYPE_NAMES.get(self.entity_type, 'unknown')

    def get_screen_radius(self) -> float:
        """Estimated world-space radius for culling."""
        return self.aabb.radius if self.aabb.is_valid else 1.0


# ─────────────────────────────────────────────────────────────────────────────
#  Scene Room
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SceneRoom:
    """
    One room in the module, matching KotOR.js ModuleRoom.

    A room has:
      - Name (e.g. 'tar_m02aa_01a')
      - World position offset
      - Linked room names (from VIS file)
      - AABB computed from its MDL geometry
      - A list of entities contained within this room
    """
    name:          str   = ""
    position:      Vec3  = field(default_factory=lambda: (0.0, 0.0, 0.0))
    model_resref:  str   = ""       # Same as name (lowercase)
    mesh_data:     Any   = field(default=None, repr=False)
    aabb:          AABB  = field(default_factory=AABB)
    visible:       bool  = True

    # VIS links (room names this room can see / is linked to)
    linked_rooms:  List[str] = field(default_factory=list)

    # Entities in this room
    entity_ids:    List[int] = field(default_factory=list)

    # GL handles (managed by renderer)
    vao_handles:   List[Any] = field(default_factory=list, repr=False)

    def __hash__(self):
        return hash(self.name.lower())

    def __eq__(self, other):
        if isinstance(other, SceneRoom):
            return self.name.lower() == other.name.lower()
        return NotImplemented


# ─────────────────────────────────────────────────────────────────────────────
#  Visibility System
# ─────────────────────────────────────────────────────────────────────────────

class VisibilitySystem:
    """
    Room-to-room visibility based on VIS file.
    Matches Odyssey engine: each room specifies which other rooms are visible
    from within it.

    Built from LYT/VIS data. When a player is in room R, only rooms listed
    in vis_data[R] (plus R itself) need to be rendered.
    """

    def __init__(self):
        # vis_data[room_name] = set of visible room names (lowercase)
        self.vis_data: Dict[str, Set[str]] = {}
        self._default_range: float = 50.0  # fallback: render rooms within this radius

    def load_from_lyt_vis(self, lyt_vis_data: Any) -> None:
        """Load VIS data from a parsed LYTVis object."""
        try:
            for room_name, visible_rooms in (getattr(lyt_vis_data, 'vis_map', {}) or {}).items():
                key = room_name.lower()
                self.vis_data[key] = {r.lower() for r in visible_rooms}
        except Exception as e:
            log.debug(f"VisibilitySystem: error loading VIS data: {e}")

    def load_from_dict(self, vis_dict: Dict[str, List[str]]) -> None:
        """Load from plain dict: room_name → [visible room names]."""
        for k, v in vis_dict.items():
            self.vis_data[k.lower()] = {r.lower() for r in v}

    def get_visible_rooms(self, current_room: str) -> Set[str]:
        """
        Return the set of room names visible from current_room.
        Always includes current_room itself.
        If no VIS data, returns empty set (caller should use distance fallback).
        """
        key = current_room.lower()
        visible = self.vis_data.get(key, set())
        result = set(visible)
        result.add(key)
        return result

    def has_vis_data(self) -> bool:
        return len(self.vis_data) > 0


# ─────────────────────────────────────────────────────────────────────────────
#  Scene Statistics
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SceneStats:
    """Runtime performance counters for the scene."""
    frame_count:        int   = 0
    total_rooms:        int   = 0
    visible_rooms:      int   = 0
    culled_rooms:       int   = 0
    total_entities:     int   = 0
    visible_entities:   int   = 0
    total_triangles:    int   = 0
    rendered_triangles: int   = 0
    last_frame_ms:      float = 0.0
    fps:                float = 0.0

    def __str__(self) -> str:
        return (
            f"FPS:{self.fps:.0f} | "
            f"Rooms:{self.visible_rooms}/{self.total_rooms} | "
            f"Tris:{self.rendered_triangles:,} | "
            f"Ents:{self.visible_entities}/{self.total_entities}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Render Bucket
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RenderItem:
    """One item to render this frame."""
    entity_id:   int
    entity_type: int
    distance:    float = 0.0     # distance from camera (for sorting)
    vao_entries: List[Any] = field(default_factory=list)
    transform:   Any = None      # 4×4 transform matrix (numpy or list)
    alpha:       float = 1.0
    selected:    bool  = False


@dataclass
class RenderBucket:
    """
    Sorted render items for one frame.

    Render order:
      1. Rooms (opaque, front-to-back)
      2. Opaque entities (front-to-back for early-Z)
      3. Transparent entities (back-to-front for correct blending)
      4. Walkmesh overlay
      5. Selection highlights + gizmos
    """
    rooms_opaque:         List[RenderItem] = field(default_factory=list)
    entities_opaque:      List[RenderItem] = field(default_factory=list)
    entities_transparent: List[RenderItem] = field(default_factory=list)
    walkmesh_walk:        List[Any] = field(default_factory=list)
    walkmesh_nowalk:      List[Any] = field(default_factory=list)
    selected_entities:    List[RenderItem] = field(default_factory=list)

    def clear(self) -> None:
        self.rooms_opaque.clear()
        self.entities_opaque.clear()
        self.entities_transparent.clear()
        self.walkmesh_walk.clear()
        self.walkmesh_nowalk.clear()
        self.selected_entities.clear()

    def sort(self, camera_pos: Vec3) -> None:
        """Sort render items for optimal draw order."""
        def dist(item: RenderItem) -> float:
            return item.distance

        # Opaque: front-to-back (early Z culling)
        self.rooms_opaque.sort(key=dist)
        self.entities_opaque.sort(key=dist)
        # Transparent: back-to-front (correct alpha blending)
        self.entities_transparent.sort(key=dist, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Scene Graph  (the main engine scene)
# ─────────────────────────────────────────────────────────────────────────────

class SceneGraph:
    """
    The full scene for one module, matching KotOR.js ModuleArea.

    Manages:
      - All rooms with their geometry and VIS links
      - All scene entities (GIT objects)
      - Frustum culling + VIS-based room culling
      - Entity spatial lookup (which room is each entity in)
      - Render bucket generation

    Usage::
        scene = SceneGraph()
        scene.add_room(SceneRoom(...))
        scene.add_entity(SceneEntity(...))
        scene.set_camera(eye, target)
        bucket = scene.build_render_bucket()
    """

    def __init__(self):
        # Rooms: name → SceneRoom
        self._rooms:     Dict[str, SceneRoom]   = {}
        # Entities: entity_id → SceneEntity
        self._entities:  Dict[int, SceneEntity] = {}
        self._next_id:   int = 1

        # VIS system
        self.vis_system: VisibilitySystem = VisibilitySystem()

        # Frustum for culling
        self._frustum:   Frustum = Frustum()

        # Camera state
        self._camera_pos:  Vec3 = (0.0, 0.0, 2.0)
        self._camera_room: str  = ""

        # Visibility flags (matching PyKotor GL hide_ flags)
        self.hide_rooms:      bool = False
        self.hide_creatures:  bool = False
        self.hide_placeables: bool = False
        self.hide_doors:      bool = False
        self.hide_triggers:   bool = True
        self.hide_encounters: bool = True
        self.hide_waypoints:  bool = False
        self.hide_sounds:     bool = False
        self.hide_stores:     bool = False
        self.hide_cameras:    bool = False
        self.show_walkmesh:   bool = True

        # Statistics
        self.stats = SceneStats()

        # Selected entity ID
        self.selected_entity_id: int = 0

        # Last frame time
        self._last_frame_time: float = time.perf_counter()

    # ── Room management ───────────────────────────────────────────────────────

    def add_room(self, room: SceneRoom) -> None:
        key = room.name.lower()
        self._rooms[key] = room
        log.debug(f"SceneGraph: room '{key}' added")

    def remove_room(self, name: str) -> None:
        key = name.lower()
        self._rooms.pop(key, None)

    def get_room(self, name: str) -> Optional[SceneRoom]:
        return self._rooms.get(name.lower())

    @property
    def rooms(self) -> List[SceneRoom]:
        return list(self._rooms.values())

    def get_room_at_position(self, pos: Vec3) -> Optional[SceneRoom]:
        """Return the room whose AABB contains pos (nearest center as tiebreaker)."""
        best: Optional[SceneRoom] = None
        best_dist = float('inf')
        for room in self._rooms.values():
            if room.aabb.is_valid and room.aabb.contains_point(pos):
                c = room.aabb.center
                d = _length3(_sub3(pos, c))
                if d < best_dist:
                    best_dist = d
                    best = room
        return best

    # ── Entity management ─────────────────────────────────────────────────────

    def allocate_id(self) -> int:
        eid = self._next_id
        self._next_id += 1
        return eid

    def add_entity(self, entity: SceneEntity) -> int:
        if entity.entity_id == 0:
            entity.entity_id = self.allocate_id()
        self._entities[entity.entity_id] = entity
        # Assign entity to room
        room = self.get_room_at_position(entity.position)
        if room:
            entity.room_name = room.name
            if entity.entity_id not in room.entity_ids:
                room.entity_ids.append(entity.entity_id)
        return entity.entity_id

    def remove_entity(self, entity_id: int) -> None:
        ent = self._entities.pop(entity_id, None)
        if ent:
            room = self._rooms.get(ent.room_name.lower())
            if room and entity_id in room.entity_ids:
                room.entity_ids.remove(entity_id)

    def get_entity(self, entity_id: int) -> Optional[SceneEntity]:
        return self._entities.get(entity_id)

    def get_entities_by_type(self, entity_type: int) -> List[SceneEntity]:
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    @property
    def entities(self) -> List[SceneEntity]:
        return list(self._entities.values())

    # ── Selection ─────────────────────────────────────────────────────────────

    def select_entity(self, entity_id: int) -> Optional[SceneEntity]:
        """Select an entity, deselecting the previous one."""
        # Deselect current
        if self.selected_entity_id:
            prev = self._entities.get(self.selected_entity_id)
            if prev:
                prev.selected = False
        # Select new
        self.selected_entity_id = entity_id
        ent = self._entities.get(entity_id)
        if ent:
            ent.selected = True
        return ent

    def deselect_all(self) -> None:
        for ent in self._entities.values():
            ent.selected = False
        self.selected_entity_id = 0

    # ── Camera + culling state ─────────────────────────────────────────────────

    def set_camera(self, eye: Vec3, vp_matrix: Optional[List[float]] = None) -> None:
        """
        Update camera position and optional VP matrix for frustum culling.
        """
        self._camera_pos = eye
        if vp_matrix:
            self._frustum.from_vp_matrix(vp_matrix)
        # Update current room
        room = self.get_room_at_position(eye)
        self._camera_room = room.name if room else ""

    # ── VIS-based visibility ──────────────────────────────────────────────────

    def get_visible_room_names(self) -> Set[str]:
        """
        Return set of room names that should be rendered from current camera pos.
        Uses VIS data if available, falls back to distance-based culling.
        """
        if self.vis_system.has_vis_data() and self._camera_room:
            return self.vis_system.get_visible_rooms(self._camera_room)
        # Fallback: all rooms (when no VIS data)
        return set(self._rooms.keys())

    # ── Entity type → should_hide ─────────────────────────────────────────────

    def _should_hide_entity(self, ent: SceneEntity) -> bool:
        if ent.hidden:
            return True
        t = ent.entity_type
        if t == ENTITY_CREATURE  and self.hide_creatures:  return True
        if t == ENTITY_PLACEABLE and self.hide_placeables: return True
        if t == ENTITY_DOOR      and self.hide_doors:      return True
        if t == ENTITY_TRIGGER   and self.hide_triggers:   return True
        if t == ENTITY_ENCOUNTER and self.hide_encounters: return True
        if t == ENTITY_WAYPOINT  and self.hide_waypoints:  return True
        if t == ENTITY_SOUND     and self.hide_sounds:     return True
        if t == ENTITY_STORE     and self.hide_stores:     return True
        if t == ENTITY_CAMERA    and self.hide_cameras:    return True
        return False

    # ── Render bucket generation ───────────────────────────────────────────────

    def build_render_bucket(self) -> RenderBucket:
        """
        Build this frame's render bucket using frustum + VIS culling.
        Matching KotOR.js GameState render loop:
          1. Determine visible rooms via VIS
          2. Frustum-cull each visible room
          3. For each visible room, include its entities
          4. Sort by distance
        """
        bucket = RenderBucket()

        now = time.perf_counter()
        dt  = now - self._last_frame_time
        self._last_frame_time = now

        # Stats reset
        stats = self.stats
        stats.frame_count  += 1
        stats.total_rooms   = len(self._rooms)
        stats.total_entities = len(self._entities)
        vis_rooms = 0
        vis_ents  = 0

        if self.hide_rooms:
            visible_room_names: Set[str] = set()
        else:
            visible_room_names = self.get_visible_room_names()

        cam = self._camera_pos

        # ── Rooms ─────────────────────────────────────────────────────────────
        for room_name in visible_room_names:
            room = self._rooms.get(room_name)
            if room is None:
                continue

            # Frustum cull room
            if room.aabb.is_valid:
                if not self._frustum.test_aabb(room.aabb):
                    stats.culled_rooms = stats.culled_rooms + 1
                    continue

            c = room.aabb.center if room.aabb.is_valid else room.position
            dist = _length3(_sub3(cam, c))
            item = RenderItem(
                entity_id=0,
                entity_type=ENTITY_ROOM,
                distance=dist,
                vao_entries=room.vao_handles,
            )
            bucket.rooms_opaque.append(item)
            vis_rooms += 1

        # ── Entities ──────────────────────────────────────────────────────────
        for ent in self._entities.values():
            if self._should_hide_entity(ent):
                continue

            # Only show entities in visible rooms (or if no room assignment)
            if ent.room_name and ent.room_name.lower() not in visible_room_names:
                # Still check distance fallback
                if visible_room_names:
                    continue

            # Frustum cull entity
            if ent.aabb.is_valid:
                if not self._frustum.test_sphere(ent.aabb.center, ent.aabb.radius + 0.5):
                    continue

            c = ent.aabb.center if ent.aabb.is_valid else ent.position
            dist = _length3(_sub3(cam, c))
            item = RenderItem(
                entity_id=ent.entity_id,
                entity_type=ent.entity_type,
                distance=dist,
                vao_entries=ent.vao_handles,
                alpha=1.0,
                selected=ent.selected,
            )

            # Check if entity has transparent mesh
            is_transparent = False
            for vao_e in ent.vao_handles:
                if isinstance(vao_e, dict) and vao_e.get('alpha', 1.0) < 0.99:
                    is_transparent = True
                    break

            if is_transparent:
                bucket.entities_transparent.append(item)
            else:
                bucket.entities_opaque.append(item)

            if ent.selected:
                bucket.selected_entities.append(item)

            vis_ents += 1

        # Sort buckets
        bucket.sort(cam)

        stats.visible_rooms    = vis_rooms
        stats.visible_entities = vis_ents

        if dt > 0:
            stats.fps = 1.0 / dt
        stats.last_frame_ms = dt * 1000.0

        return bucket

    # ── Ray casting ───────────────────────────────────────────────────────────

    def ray_cast(self, origin: Vec3, direction: Vec3,
                 entity_types: Optional[List[int]] = None) -> Optional[Tuple[int, float]]:
        """
        Cast a ray and return (entity_id, t) of the nearest hit, or None.

        entity_types: if provided, only test entities of these types.
        """
        best_t:  float = float('inf')
        best_id: int   = 0

        entities_to_test = self._entities.values()

        for ent in entities_to_test:
            if entity_types and ent.entity_type not in entity_types:
                continue
            if self._should_hide_entity(ent):
                continue
            if ent.aabb.is_valid:
                t = ent.aabb.ray_intersect(origin, direction)
                if t is not None and 0.0 <= t < best_t:
                    best_t  = t
                    best_id = ent.entity_id

        return (best_id, best_t) if best_id else None

    # ── Scene AABB ───────────────────────────────────────────────────────────

    def compute_scene_aabb(self) -> AABB:
        """Compute world-space AABB encompassing all rooms."""
        box = AABB.empty()
        for room in self._rooms.values():
            if room.aabb.is_valid:
                box.expand_aabb(room.aabb)
            elif room.position:
                box.expand(room.position)
        return box

    # ── Statistics ────────────────────────────────────────────────────────────

    def update_stats(self, rendered_tris: int = 0) -> None:
        self.stats.rendered_triangles = rendered_tris

    # ── Clear ────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        self._rooms.clear()
        self._entities.clear()
        self._next_id = 1
        self._camera_room = ""
        self.vis_system.vis_data.clear()

    def clear_entities(self) -> None:
        """Remove all entities but keep rooms."""
        self._entities.clear()
        for room in self._rooms.values():
            room.entity_ids.clear()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def populate_from_state(self, module_state: Any,
                             room_instances: List[Any],
                             game_dir: str = "") -> None:
        """
        Populate the scene from a module state object.
        Processes rooms from LYT and entities from GIT.
        """
        self.clear()

        # Load rooms
        for i, room_inst in enumerate(room_instances or []):
            try:
                name = (getattr(room_inst, 'mdl_name', None) or
                        getattr(room_inst, 'resref', None) or
                        getattr(room_inst, 'name', f'room_{i}') or f'room_{i}')
                x = float(getattr(room_inst, 'world_x',
                           getattr(room_inst, 'x', 0.0)) or 0.0)
                y = float(getattr(room_inst, 'world_y',
                           getattr(room_inst, 'y', 0.0)) or 0.0)
                z = float(getattr(room_inst, 'world_z',
                           getattr(room_inst, 'z', 0.0)) or 0.0)
                room = SceneRoom(
                    name=name.lower(),
                    position=(x, y, z),
                    model_resref=name.lower(),
                )
                self.add_room(room)
            except Exception as e:
                log.debug(f"SceneGraph: error adding room: {e}")

        # Load entities from GIT
        if module_state is not None:
            git = getattr(module_state, 'git', None)
            if git is not None:
                self._populate_entities_from_git(git)

        log.info(
            f"SceneGraph: loaded {len(self._rooms)} rooms, "
            f"{len(self._entities)} entities"
        )

    def _populate_entities_from_git(self, git: Any) -> None:
        """Load all GIT object types into the scene."""
        type_map = [
            (ENTITY_CREATURE,  'creatures'),
            (ENTITY_PLACEABLE, 'placeables'),
            (ENTITY_DOOR,      'doors'),
            (ENTITY_WAYPOINT,  'waypoints'),
            (ENTITY_TRIGGER,   'triggers'),
            (ENTITY_ENCOUNTER, 'encounters'),
            (ENTITY_SOUND,     'sounds'),
            (ENTITY_STORE,     'stores'),
            (ENTITY_CAMERA,    'cameras'),
        ]

        for etype, attr in type_map:
            items = getattr(git, attr, []) or []
            for item in items:
                try:
                    pos = getattr(item, 'position', None)
                    if pos:
                        x, y, z = float(pos.x), float(pos.y), float(pos.z)
                    else:
                        x = y = z = 0.0
                    bearing = float(getattr(item, 'bearing', 0.0))
                    resref  = str(getattr(item, 'resref', '') or '')
                    tag     = str(getattr(item, 'tag', '') or '')

                    ent = SceneEntity(
                        entity_type=etype,
                        resref=resref,
                        tag=tag,
                        position=(x, y, z),
                        bearing=bearing,
                        git_data=item,
                    )
                    # Default capsule AABB
                    ent.aabb = AABB(
                        min=(x - 0.4, y - 0.4, z),
                        max=(x + 0.4, y + 0.4, z + 1.8),
                    )
                    self.add_entity(ent)
                except Exception as e:
                    log.debug(f"SceneGraph: error adding {ENTITY_TYPE_NAMES.get(etype,'?')}: {e}")
