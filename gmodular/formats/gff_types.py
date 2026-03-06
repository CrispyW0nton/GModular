"""
GModular — GFF V3.2 Type Definitions
Binary GFF format used by BioWare Odyssey engine (.ARE, .GIT, .IFO, .DLG)
Reverse-engineered from xoreos-docs + KotOR modding wiki.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Dict, Optional, Any, Union


# ── GFF Field Type IDs ──────────────────────────────────────────────────────

class GFFFieldType(IntEnum):
    BYTE      = 0
    CHAR      = 1
    WORD      = 2
    SHORT     = 3
    DWORD     = 4
    INT       = 5
    DWORD64   = 6
    INT64     = 7
    FLOAT     = 8
    DOUBLE    = 9
    CEXOSTRING = 10
    RESREF    = 11
    CEXOLOCSTRING = 12
    VOID      = 13
    STRUCT    = 14
    LIST      = 15
    ORIENTATION = 16
    VECTOR    = 17
    STRREF    = 18


# ── GFF Data Model ──────────────────────────────────────────────────────────

@dataclass
class GFFField:
    """A single GFF field (label + typed value)."""
    label: str
    type_id: int
    value: Any = None

    def __repr__(self):
        return f"GFFField({self.label!r}, type={self.type_id}, value={self.value!r})"


@dataclass
class GFFStruct:
    """A GFF struct — ordered dict of fields."""
    struct_id: int = 0
    fields: Dict[str, GFFField] = field(default_factory=dict)

    def get(self, label: str, default=None) -> Any:
        f = self.fields.get(label)
        return f.value if f is not None else default

    def set(self, label: str, type_id: int, value: Any):
        self.fields[label] = GFFField(label=label, type_id=type_id, value=value)

    def __contains__(self, label: str) -> bool:
        return label in self.fields

    def __repr__(self):
        return f"GFFStruct(id={self.struct_id}, fields={list(self.fields.keys())})"


@dataclass
class GFFRoot(GFFStruct):
    """Root GFF struct with file type/version metadata."""
    file_type: str = "    "    # 4-char, e.g. "GIT ", "ARE ", "IFO "
    file_version: str = "V3.2"

    def __repr__(self):
        return f"GFFRoot(type={self.file_type!r}, fields={list(self.fields.keys())})"


# ── Odyssey Module Geometry ────────────────────────────────────────────────

@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_tuple(self):
        return (self.x, self.y, self.z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __repr__(self):
        return f"Vec3({self.x:.3f},{self.y:.3f},{self.z:.3f})"


@dataclass
class Quaternion:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0

    def to_tuple(self):
        return (self.x, self.y, self.z, self.w)

    def __repr__(self):
        return f"Quat({self.x:.3f},{self.y:.3f},{self.z:.3f},{self.w:.3f})"


# ── GIT Entry Types ────────────────────────────────────────────────────────

@dataclass
class GITPlaceable:
    """A placeable object instance in a GIT file."""
    template_resref: str = ""        # UTC/UTP blueprint
    resref: str = ""                 # ResRef (≤16 chars)
    tag: str = ""
    position: Vector3 = field(default_factory=Vector3)
    bearing: float = 0.0             # Yaw in radians
    on_used: str = ""
    on_heartbeat: str = ""
    on_closed: str = ""
    on_damaged: str = ""
    on_death: str = ""
    on_end_conversation: str = ""
    on_inventory_disturbed: str = ""
    on_lock: str = ""
    on_melee_attacked: str = ""
    on_open: str = ""
    on_user_defined: str = ""

    # Runtime metadata (not serialised)
    _scene_id: int = field(default=-1, repr=False, compare=False)

    def resref_truncated(self) -> str:
        return self.resref[:16]

    def template_truncated(self) -> str:
        return self.template_resref[:16]


@dataclass
class GITCreature:
    """A creature instance in a GIT file."""
    template_resref: str = ""
    resref: str = ""
    tag: str = ""
    position: Vector3 = field(default_factory=Vector3)
    bearing: float = 0.0
    on_heartbeat: str = ""
    on_death: str = ""
    on_end_conversation: str = ""
    on_disturbed: str = ""
    on_blocked: str = ""
    on_attacked: str = ""
    on_damaged: str = ""
    on_user_defined: str = ""
    on_notice: str = ""
    on_conversation: str = ""
    on_spawn: str = ""
    _scene_id: int = field(default=-1, repr=False, compare=False)


@dataclass
class GITDoor:
    """A door instance in a GIT file."""
    template_resref: str = ""
    resref: str = ""
    tag: str = ""
    position: Vector3 = field(default_factory=Vector3)
    bearing: float = 0.0
    linked_to: str = ""
    linked_to_flags: int = 0
    transition_destination: str = ""
    on_open: str = ""
    on_fail_to_open: str = ""
    on_closed: str = ""
    on_damaged: str = ""
    on_death: str = ""
    on_heartbeat: str = ""
    on_lock: str = ""
    on_melee_attacked: str = ""
    on_open2: str = ""
    on_unlock: str = ""
    on_user_defined: str = ""
    _scene_id: int = field(default=-1, repr=False, compare=False)


@dataclass
class GITTrigger:
    """A trigger volume in a GIT file."""
    template_resref: str = ""
    resref: str = ""
    tag: str = ""
    position: Vector3 = field(default_factory=Vector3)
    geometry: List[Vector3] = field(default_factory=list)   # trigger polygon vertices
    on_enter: str = ""
    on_exit: str = ""
    on_heartbeat: str = ""
    on_user_defined: str = ""
    _scene_id: int = field(default=-1, repr=False, compare=False)


@dataclass
class GITSoundObject:
    """An ambient sound emitter in a GIT file."""
    template_resref: str = ""
    resref: str = ""
    tag: str = ""
    position: Vector3 = field(default_factory=Vector3)
    _scene_id: int = field(default=-1, repr=False, compare=False)


@dataclass
class GITWaypoint:
    """A waypoint/patrol node in a GIT file."""
    template_resref: str = ""
    resref: str = ""
    tag: str = ""
    position: Vector3 = field(default_factory=Vector3)
    map_note: str = ""
    map_note_enabled: int = 0
    _scene_id: int = field(default=-1, repr=False, compare=False)


@dataclass
class GITStoreObject:
    """A store (merchant) in a GIT file."""
    template_resref: str = ""
    resref: str = ""
    tag: str = ""
    position: Vector3 = field(default_factory=Vector3)
    bearing: float = 0.0
    _scene_id: int = field(default=-1, repr=False, compare=False)


# ── Full GIT Module Data ───────────────────────────────────────────────────

@dataclass
class GITData:
    """
    Complete in-memory representation of a .GIT file.
    Mirrors the GFF struct hierarchy exactly as KotOR expects it.
    """
    # Instance lists (parallel to GIT list fields)
    placeables:    List[GITPlaceable]   = field(default_factory=list)
    creatures:     List[GITCreature]    = field(default_factory=list)
    doors:         List[GITDoor]        = field(default_factory=list)
    triggers:      List[GITTrigger]     = field(default_factory=list)
    sounds:        List[GITSoundObject] = field(default_factory=list)
    waypoints:     List[GITWaypoint]    = field(default_factory=list)
    stores:        List[GITStoreObject] = field(default_factory=list)

    # Area ambient data
    ambient_sound_day: str = ""
    ambient_sound_night: str = ""
    ambient_sound_dayvol: int = 50
    ambient_sound_nightvol: int = 50
    env_audio: int = 0

    def all_objects(self):
        """Yield all placeable instances (for viewport selection)."""
        yield from self.placeables
        yield from self.creatures
        yield from self.doors
        yield from self.waypoints

    @property
    def object_count(self) -> int:
        return (len(self.placeables) + len(self.creatures) +
                len(self.doors) + len(self.triggers) +
                len(self.sounds) + len(self.waypoints) + len(self.stores))


# ── ARE (Area) Data ────────────────────────────────────────────────────────

@dataclass
class AREData:
    """In-memory representation of a .ARE file (area metadata + room list)."""
    tag: str = ""
    name: str = ""
    # Room list (from ARE's "Rooms" list field)
    rooms: List[str] = field(default_factory=list)    # room model names (.mdl ResRef)
    # Area environment
    fog_enabled: int = 0
    fog_near: float = 0.0
    fog_far: float = 50.0
    fog_color: int = 0
    ambient_color: int = 0x404040
    diffuse_color: int = 0x808080
    # Tileset
    tileset_resref: str = ""
    # Flags
    sky_box: str = ""
    dynamic_day_night: int = 0
    # Misc
    shadow_opacity: int = 100
    wind_power: int = 0

    @property
    def room_count(self) -> int:
        return len(self.rooms)


# ── IFO (Module Info) Data ─────────────────────────────────────────────────

@dataclass
class IFOData:
    """In-memory representation of a .IFO file (module metadata)."""
    mod_name: str = "New Module"
    mod_description: str = ""
    entry_area: str = ""              # ResRef of starting area
    entry_position: Vector3 = field(default_factory=Vector3)
    entry_direction: float = 0.0
    on_module_load: str = ""
    on_module_start: str = ""
    on_player_death: str = ""
    on_player_dying: str = ""
    on_player_levelup: str = ""
    on_player_respawn: str = ""
    on_player_rest: str = ""
    on_heartbeat: str = ""
    on_client_enter: str = ""
    on_client_leave: str = ""
    on_cutscene_abort: str = ""
    on_unacquire_item: str = ""
    on_acquire_item: str = ""
    on_activate_item: str = ""
    expansion_list: List[str] = field(default_factory=list)
