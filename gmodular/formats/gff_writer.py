"""
GModular — GFF V3.2 Binary Writer
Writes .GIT files in the exact binary format that KotOR 1/2 expects.

Reference: xoreos src/aurora/gff3writer.cpp
           reone src/resource/format/gffwriter.cpp

Layout (little-endian):
  - 56-byte header
  - Struct block  (12 bytes * N)
  - Field block   (12 bytes * N)
  - Label block   (16 bytes * N, zero-padded ASCII)
  - Field data    (variable-length data for complex types)
  - Field indices (4 bytes * N, for structs with >1 field)
  - List indices  (4 bytes * N, for list fields)
"""
from __future__ import annotations
import struct
import logging
from typing import List, Dict, Any, Optional

from .gff_types import (
    GFFFieldType, GFFField, GFFStruct, GFFRoot,
    GITData, GITPlaceable, GITCreature, GITDoor,
    GITTrigger, GITSoundObject, GITWaypoint, GITStoreObject,
    AREData, IFOData, Vector3, Quaternion,
)

log = logging.getLogger(__name__)

_HEADER_SIZE = 56


class GFFWriter:
    """
    Serialises a GFFRoot object to GFF V3.2 binary bytes.
    """

    def __init__(self, root: GFFRoot):
        self._root = root
        # Build output lists
        self._structs:       List[tuple] = []   # (type, data_or_offset, field_count)
        self._fields:        List[tuple] = []   # (type_id, label_idx, data_or_offset)
        self._labels:        List[str]   = []
        self._label_map:     Dict[str, int] = {}
        self._field_data:    bytearray   = bytearray()
        self._field_indices: bytearray   = bytearray()
        self._list_indices:  bytearray   = bytearray()

    # ── Public API ─────────────────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        """Serialise GFF root to bytes."""
        self._reset()
        self._build_all(self._root)
        return self._build()

    def write_file(self, path: str):
        data = self.to_bytes()
        with open(path, "wb") as f:
            f.write(data)
        log.info(f"GFF written: {path} ({len(data)} bytes)")

    # ── Internal ───────────────────────────────────────────────────────────

    def _reset(self):
        self._structs.clear()
        self._fields.clear()
        self._labels.clear()
        self._label_map.clear()
        self._field_data = bytearray()
        self._field_indices = bytearray()
        self._list_indices = bytearray()

    def _intern_label(self, label: str) -> int:
        if label in self._label_map:
            return self._label_map[label]
        idx = len(self._labels)
        self._labels.append(label[:16])
        self._label_map[label] = idx
        return idx

    def _add_field_data(self, data: bytes) -> int:
        """Append data to field data block. Returns offset."""
        off = len(self._field_data)
        self._field_data.extend(data)
        return off

    def _collect_structs_bfs(self, root: "GFFStruct"):
        """
        BFS traversal to collect all structs in order.
        Returns ordered list of structs.
        The GFF format requires that struct indices be stable before
        any field data referencing them is written.
        """
        from collections import deque
        ordered: List[GFFStruct] = []
        queue = deque([root])
        while queue:
            s = queue.popleft()
            ordered.append(s)
            for field in s.fields.values():
                if field.type_id == GFFFieldType.STRUCT and isinstance(field.value, GFFStruct):
                    queue.append(field.value)
                elif field.type_id == GFFFieldType.LIST and isinstance(field.value, list):
                    for sub in field.value:
                        if isinstance(sub, GFFStruct):
                            queue.append(sub)
        return ordered

    def _build_all(self, root: "GFFStruct"):
        """
        Two-phase GFF build:
        Phase 1: BFS-collect all structs and assign stable indices.
        Phase 2: Encode all fields (can now safely reference struct indices).
        """
        # Phase 1: assign struct indices
        all_structs = self._collect_structs_bfs(root)
        struct_idx_map: Dict = {}   # id(struct) -> index
        for i, s in enumerate(all_structs):
            struct_idx_map[id(s)] = i
            self._structs.append((s.struct_id, 0, 0))   # placeholders

        # Phase 2: encode fields for each struct
        def encode_field(field: GFFField) -> int:
            """Encode one field and return its field list index."""
            ft    = field.type_id
            label = field.label
            value = field.value
            lidx  = self._intern_label(label)
            fi    = len(self._fields)

            def write_fd(data: bytes) -> int:
                return self._add_field_data(data)

            if ft == GFFFieldType.BYTE:
                self._fields.append((ft, lidx, int(value or 0) & 0xFF))
            elif ft == GFFFieldType.CHAR:
                self._fields.append((ft, lidx, int(value or 0) & 0xFF))
            elif ft == GFFFieldType.WORD:
                self._fields.append((ft, lidx, int(value or 0) & 0xFFFF))
            elif ft == GFFFieldType.SHORT:
                v = struct.unpack("<H", struct.pack("<h", int(value or 0)))[0]
                self._fields.append((ft, lidx, v))
            elif ft == GFFFieldType.DWORD:
                self._fields.append((ft, lidx, int(value or 0) & 0xFFFFFFFF))
            elif ft == GFFFieldType.INT:
                v = struct.unpack("<I", struct.pack("<i", int(value or 0)))[0]
                self._fields.append((ft, lidx, v))
            elif ft == GFFFieldType.DWORD64:
                off = write_fd(struct.pack("<Q", int(value or 0)))
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.INT64:
                off = write_fd(struct.pack("<q", int(value or 0)))
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.FLOAT:
                v = struct.unpack("<I", struct.pack("<f", float(value or 0.0)))[0]
                self._fields.append((ft, lidx, v))
            elif ft == GFFFieldType.DOUBLE:
                off = write_fd(struct.pack("<d", float(value or 0.0)))
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.CEXOSTRING:
                s_val = str(value or "").encode("utf-8")
                off = write_fd(struct.pack("<I", len(s_val)) + s_val)
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.RESREF:
                s_val = str(value or "")[:16].encode("ascii")
                off = write_fd(bytes([len(s_val)]) + s_val)
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.CEXOLOCSTRING:
                s_val = str(value or "").encode("utf-8")
                inner = struct.pack("<II", 0xFFFFFFFF, 1)
                inner += struct.pack("<II", 0, len(s_val)) + s_val
                off = write_fd(struct.pack("<I", len(inner)) + inner)
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.VOID:
                b = bytes(value) if value else b""
                off = write_fd(struct.pack("<I", len(b)) + b)
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.STRUCT:
                sub_idx = struct_idx_map.get(id(value), 0)
                self._fields.append((ft, lidx, sub_idx))
            elif ft == GFFFieldType.LIST:
                items = value or []
                list_off = len(self._list_indices)
                # Write count + struct indices (all already in struct_idx_map)
                self._list_indices.extend(struct.pack("<I", len(items)))
                for sub_s in items:
                    sub_idx = struct_idx_map.get(id(sub_s), 0)
                    self._list_indices.extend(struct.pack("<I", sub_idx))
                self._fields.append((ft, lidx, list_off))
            elif ft == GFFFieldType.ORIENTATION:
                q = value if isinstance(value, Quaternion) else Quaternion()
                off = write_fd(struct.pack("<4f", q.x, q.y, q.z, q.w))
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.VECTOR:
                v = value if isinstance(value, Vector3) else Vector3()
                off = write_fd(struct.pack("<3f", v.x, v.y, v.z))
                self._fields.append((ft, lidx, off))
            elif ft == GFFFieldType.STRREF:
                self._fields.append((ft, lidx, int(value or 0xFFFFFFFF)))
            else:
                log.warning(f"Unknown field type {ft!r} for {label!r}")
                self._fields.append((GFFFieldType.DWORD, lidx, 0))
            return fi

        # Encode fields for each struct in BFS order
        for s in all_structs:
            struct_index = struct_idx_map[id(s)]
            field_count  = len(s.fields)

            if field_count == 0:
                self._structs[struct_index] = (s.struct_id, 0xFFFFFFFF, 0)
                continue

            field_indices = [encode_field(f) for f in s.fields.values()]

            if field_count == 1:
                sdata = field_indices[0]
            else:
                sdata = len(self._field_indices)
                for fi in field_indices:
                    self._field_indices.extend(struct.pack("<I", fi))

            self._structs[struct_index] = (s.struct_id, sdata, field_count)

    # keep legacy entry points
    def _add_struct(self, s: "GFFStruct") -> int:
        """Compatibility shim — now handled by _build_all."""
        return 0

    def _add_field(self, field: GFFField) -> int:
        """Compatibility shim — now handled by _build_all."""
        return 0

    def _build(self) -> bytes:
        """Assemble all blocks into final bytes."""
        ns = len(self._structs)
        nf = len(self._fields)
        nl = len(self._labels)
        nfd = len(self._field_data)
        nfi = len(self._field_indices)
        nli = len(self._list_indices)

        # Compute offsets
        struct_off   = _HEADER_SIZE
        field_off    = struct_off  + ns * 12
        label_off    = field_off   + nf * 12
        fdata_off    = label_off   + nl * 16
        findices_off = fdata_off   + nfd
        lindices_off = findices_off + nfi

        # Build file_type padded to 4 chars
        ft = (self._root.file_type + "    ")[:4].encode("ascii")
        fv = b"V3.2"

        header = struct.pack(
            "<4s4s12I",
            ft, fv,
            struct_off,   ns,
            field_off,    nf,
            label_off,    nl,
            fdata_off,    nfd,
            findices_off, nfi,
            lindices_off, nli,
        )

        struct_block = b"".join(
            struct.pack("<III", stype, sdata, sfields)
            for stype, sdata, sfields in self._structs
        )

        field_block = b"".join(
            struct.pack("<III", ftype, flabel, fdata)
            for ftype, flabel, fdata in self._fields
        )

        label_block = b"".join(
            lbl.encode("ascii")[:16].ljust(16, b"\x00")
            for lbl in self._labels
        )

        return (header + struct_block + field_block + label_block +
                bytes(self._field_data) +
                bytes(self._field_indices) +
                bytes(self._list_indices))


# ─────────────────────────────────────────────────────────────────────────────
#  High-Level GIT Serialiser
# ─────────────────────────────────────────────────────────────────────────────

def _float_field(label: str, value: float) -> GFFField:
    return GFFField(label, GFFFieldType.FLOAT, float(value))

def _resref_field(label: str, value: str) -> GFFField:
    return GFFField(label, GFFFieldType.RESREF, str(value or "")[:16])

def _string_field(label: str, value: str) -> GFFField:
    return GFFField(label, GFFFieldType.CEXOSTRING, str(value or ""))

def _dword_field(label: str, value: int) -> GFFField:
    return GFFField(label, GFFFieldType.DWORD, int(value or 0))

def _byte_field(label: str, value: int) -> GFFField:
    return GFFField(label, GFFFieldType.BYTE, int(value or 0) & 0xFF)


def _placeable_struct(p: GITPlaceable) -> GFFStruct:
    s = GFFStruct(struct_id=9)
    s.fields["ResRef"]      = _resref_field("ResRef",      p.resref)
    s.fields["TemplateResRef"] = _resref_field("TemplateResRef", p.template_resref)
    s.fields["Tag"]         = _string_field("Tag", p.tag)
    s.fields["XPosition"]   = _float_field("XPosition", p.position.x)
    s.fields["YPosition"]   = _float_field("YPosition", p.position.y)
    s.fields["ZPosition"]   = _float_field("ZPosition", p.position.z)
    s.fields["Bearing"]     = _float_field("Bearing", p.bearing)
    if p.on_used:            s.fields["OnUsed"]           = _resref_field("OnUsed",           p.on_used)
    if p.on_heartbeat:       s.fields["OnHeartbeat"]      = _resref_field("OnHeartbeat",      p.on_heartbeat)
    if p.on_closed:          s.fields["OnClosed"]         = _resref_field("OnClosed",         p.on_closed)
    if p.on_damaged:         s.fields["OnDamaged"]        = _resref_field("OnDamaged",        p.on_damaged)
    if p.on_death:           s.fields["OnDeath"]          = _resref_field("OnDeath",          p.on_death)
    if p.on_end_conversation: s.fields["OnEndConversation"] = _resref_field("OnEndConversation", p.on_end_conversation)
    if p.on_inventory_disturbed: s.fields["OnInventoryDisturbed"] = _resref_field("OnInventoryDisturbed", p.on_inventory_disturbed)
    if p.on_lock:            s.fields["OnLock"]           = _resref_field("OnLock",           p.on_lock)
    if p.on_melee_attacked:  s.fields["OnMeleeAttacked"]  = _resref_field("OnMeleeAttacked",  p.on_melee_attacked)
    if p.on_open:            s.fields["OnOpen"]           = _resref_field("OnOpen",           p.on_open)
    if p.on_user_defined:    s.fields["OnUserDefined"]    = _resref_field("OnUserDefined",    p.on_user_defined)
    return s


def _creature_struct(c: GITCreature) -> GFFStruct:
    s = GFFStruct(struct_id=4)
    s.fields["ResRef"]    = _resref_field("ResRef",    c.resref)
    s.fields["TemplateResRef"] = _resref_field("TemplateResRef", c.template_resref)
    s.fields["Tag"]       = _string_field("Tag", c.tag)
    s.fields["XPosition"] = _float_field("XPosition", c.position.x)
    s.fields["YPosition"] = _float_field("YPosition", c.position.y)
    s.fields["ZPosition"] = _float_field("ZPosition", c.position.z)
    s.fields["XOrientation"] = _float_field("XOrientation", c.bearing)
    s.fields["YOrientation"] = _float_field("YOrientation", 0.0)
    if c.on_heartbeat:         s.fields["OnHeartbeat"]       = _resref_field("OnHeartbeat",       c.on_heartbeat)
    if c.on_death:             s.fields["OnDeath"]           = _resref_field("OnDeath",           c.on_death)
    if c.on_end_conversation:  s.fields["OnEndConversation"] = _resref_field("OnEndConversation", c.on_end_conversation)
    if c.on_spawn:             s.fields["OnSpawn"]           = _resref_field("OnSpawn",           c.on_spawn)
    if c.on_notice:            s.fields["OnNotice"]          = _resref_field("OnNotice",          c.on_notice)
    if c.on_disturbed:         s.fields["OnDisturbed"]       = _resref_field("OnDisturbed",       c.on_disturbed)
    if c.on_blocked:           s.fields["OnBlocked"]         = _resref_field("OnBlocked",         c.on_blocked)
    if c.on_attacked:          s.fields["OnAttacked"]        = _resref_field("OnAttacked",        c.on_attacked)
    if c.on_damaged:           s.fields["OnDamaged"]         = _resref_field("OnDamaged",         c.on_damaged)
    if c.on_user_defined:      s.fields["OnUserDefined"]     = _resref_field("OnUserDefined",     c.on_user_defined)
    if c.on_conversation:      s.fields["Conversation"]      = _resref_field("Conversation",      c.on_conversation)
    return s


def _door_struct(d: GITDoor) -> GFFStruct:
    s = GFFStruct(struct_id=8)
    s.fields["ResRef"]    = _resref_field("ResRef",    d.resref)
    s.fields["TemplateResRef"] = _resref_field("TemplateResRef", d.template_resref)
    s.fields["Tag"]       = _string_field("Tag", d.tag)
    s.fields["X"]         = _float_field("X", d.position.x)
    s.fields["Y"]         = _float_field("Y", d.position.y)
    s.fields["Z"]         = _float_field("Z", d.position.z)
    s.fields["Bearing"]   = _float_field("Bearing", d.bearing)
    s.fields["LinkedTo"]  = _string_field("LinkedTo", d.linked_to)
    s.fields["LinkedToFlags"] = _byte_field("LinkedToFlags", d.linked_to_flags)
    s.fields["TransitionDestin"] = _resref_field("TransitionDestin", d.transition_destination)
    if d.on_open:            s.fields["OnOpen"]           = _resref_field("OnOpen",           d.on_open)
    if d.on_closed:          s.fields["OnClosed"]         = _resref_field("OnClosed",         d.on_closed)
    if d.on_fail_to_open:    s.fields["OnFailToOpen"]     = _resref_field("OnFailToOpen",     d.on_fail_to_open)
    if d.on_damaged:         s.fields["OnDamaged"]        = _resref_field("OnDamaged",        d.on_damaged)
    if d.on_death:           s.fields["OnDeath"]          = _resref_field("OnDeath",          d.on_death)
    if d.on_heartbeat:       s.fields["OnHeartbeat"]      = _resref_field("OnHeartbeat",      d.on_heartbeat)
    if d.on_lock:            s.fields["OnLock"]           = _resref_field("OnLock",           d.on_lock)
    if d.on_melee_attacked:  s.fields["OnMeleeAttacked"]  = _resref_field("OnMeleeAttacked",  d.on_melee_attacked)
    if d.on_open2:           s.fields["OnOpen2"]          = _resref_field("OnOpen2",          d.on_open2)
    if d.on_unlock:          s.fields["OnUnlock"]         = _resref_field("OnUnlock",         d.on_unlock)
    if d.on_user_defined:    s.fields["OnUserDefined"]    = _resref_field("OnUserDefined",    d.on_user_defined)
    return s


def _trigger_struct(t: GITTrigger) -> GFFStruct:
    s = GFFStruct(struct_id=1)
    s.fields["ResRef"]    = _resref_field("ResRef",    t.resref)
    s.fields["TemplateResRef"] = _resref_field("TemplateResRef", t.template_resref)
    s.fields["Tag"]       = _string_field("Tag", t.tag)
    s.fields["XPosition"] = _float_field("XPosition", t.position.x)
    s.fields["YPosition"] = _float_field("YPosition", t.position.y)
    s.fields["ZPosition"] = _float_field("ZPosition", t.position.z)
    # Geometry
    geo_structs = []
    for pt in t.geometry:
        gs = GFFStruct(struct_id=1)
        gs.fields["Point"] = GFFField("Point", GFFFieldType.VECTOR, pt)
        geo_structs.append(gs)
    s.fields["Geometry"] = GFFField("Geometry", GFFFieldType.LIST, geo_structs)
    if t.on_enter:           s.fields["OnEnter"]       = _resref_field("OnEnter",       t.on_enter)
    if t.on_exit:            s.fields["OnExit"]        = _resref_field("OnExit",        t.on_exit)
    if t.on_heartbeat:       s.fields["OnHeartbeat"]   = _resref_field("OnHeartbeat",   t.on_heartbeat)
    if t.on_user_defined:    s.fields["OnUserDefined"] = _resref_field("OnUserDefined", t.on_user_defined)
    return s


def _waypoint_struct(w: GITWaypoint) -> GFFStruct:
    s = GFFStruct(struct_id=6)
    s.fields["ResRef"]    = _resref_field("ResRef",    w.resref)
    s.fields["TemplateResRef"] = _resref_field("TemplateResRef", w.template_resref)
    s.fields["Tag"]       = _string_field("Tag", w.tag)
    s.fields["XPosition"] = _float_field("XPosition", w.position.x)
    s.fields["YPosition"] = _float_field("YPosition", w.position.y)
    s.fields["ZPosition"] = _float_field("ZPosition", w.position.z)
    s.fields["XOrientation"] = _float_field("XOrientation", getattr(w, "bearing", 0.0))
    s.fields["MapNote"]   = GFFField("MapNote", GFFFieldType.CEXOLOCSTRING, w.map_note)
    s.fields["MapNoteEnabled"] = _byte_field("MapNoteEnabled", w.map_note_enabled)
    return s


def _sound_struct(so: GITSoundObject) -> GFFStruct:
    """Build a GFFStruct for a GIT SoundObject entry."""
    s = GFFStruct(struct_id=6)
    s.fields["ResRef"]         = _resref_field("ResRef",         so.resref)
    s.fields["TemplateResRef"] = _resref_field("TemplateResRef", so.template_resref)
    s.fields["Tag"]            = _string_field("Tag",            so.tag)
    s.fields["XPosition"]      = _float_field("XPosition", so.position.x)
    s.fields["YPosition"]      = _float_field("YPosition", so.position.y)
    s.fields["ZPosition"]      = _float_field("ZPosition", so.position.z)
    return s


def _store_struct(st: GITStoreObject) -> GFFStruct:
    """Build a GFFStruct for a GIT StoreObject entry."""
    s = GFFStruct(struct_id=6)
    s.fields["ResRef"]         = _resref_field("ResRef",         st.resref)
    s.fields["TemplateResRef"] = _resref_field("TemplateResRef", st.template_resref)
    s.fields["Tag"]            = _string_field("Tag",            st.tag)
    s.fields["XPosition"]      = _float_field("XPosition", st.position.x)
    s.fields["YPosition"]      = _float_field("YPosition", st.position.y)
    s.fields["ZPosition"]      = _float_field("ZPosition", st.position.z)
    s.fields["Bearing"]        = _float_field("Bearing",   getattr(st, "bearing", 0.0))
    return s


def save_git(git: GITData, path: str, game: str = "K1"):
    """
    Write a GITData object to a .GIT binary file (GFF V3.2).
    """
    root = GFFRoot(file_type="GIT ")
    root.struct_id = 0xFFFFFFFF

    # Placeable List
    root.fields["Placeable List"] = GFFField(
        "Placeable List", GFFFieldType.LIST,
        [_placeable_struct(p) for p in git.placeables]
    )

    # Creature List
    root.fields["Creature List"] = GFFField(
        "Creature List", GFFFieldType.LIST,
        [_creature_struct(c) for c in git.creatures]
    )

    # Door List
    root.fields["Door List"] = GFFField(
        "Door List", GFFFieldType.LIST,
        [_door_struct(d) for d in git.doors]
    )

    # Trigger List
    root.fields["TriggerList"] = GFFField(
        "TriggerList", GFFFieldType.LIST,
        [_trigger_struct(t) for t in git.triggers]
    )

    # Sound List
    root.fields["SoundList"] = GFFField(
        "SoundList", GFFFieldType.LIST,
        [_sound_struct(so) for so in git.sounds]
    )

    # Waypoint List
    root.fields["WaypointList"] = GFFField(
        "WaypointList", GFFFieldType.LIST,
        [_waypoint_struct(w) for w in git.waypoints]
    )

    # Store List
    root.fields["StoreList"] = GFFField(
        "StoreList", GFFFieldType.LIST,
        [_store_struct(st) for st in git.stores]
    )

    # Ambient audio
    if git.ambient_sound_day:
        root.fields["AmbientSndDay"] = _resref_field("AmbientSndDay", git.ambient_sound_day)
        root.fields["AmbientSndDayVol"] = _byte_field("AmbientSndDayVol", git.ambient_sound_dayvol)
    if git.ambient_sound_night:
        root.fields["AmbientSndNit"] = _resref_field("AmbientSndNit", git.ambient_sound_night)
        root.fields["AmbientSndNitVol"] = _byte_field("AmbientSndNitVol", git.ambient_sound_nightvol)

    root.fields["EnvAudio"] = _dword_field("EnvAudio", git.env_audio)

    writer = GFFWriter(root)
    writer.write_file(path)
    log.info(f"Saved GIT: {path} ({git.object_count} objects)")


def _locstring_field(label: str, value: str) -> "GFFField":
    """Build a CExoLocString field (used for Mod_Name, Mod_Description in IFO)."""
    return GFFField(label, GFFFieldType.CEXOLOCSTRING, value)


def save_ifo(ifo: "IFOData", path: str):
    """
    Write an IFOData object to a .IFO binary file (GFF V3.2).
    Covers all KotOR module script hooks and entry-point data.
    """
    root = GFFRoot(file_type="IFO ")
    root.struct_id = 0xFFFFFFFF

    # Module identity
    root.fields["Mod_Name"]        = _locstring_field("Mod_Name",        ifo.mod_name)
    root.fields["Mod_Description"] = _locstring_field("Mod_Description", ifo.mod_description)

    # Entry area / position
    root.fields["Mod_Entry_Area"]  = _resref_field("Mod_Entry_Area",  ifo.entry_area)
    pos = ifo.entry_position
    root.fields["Mod_Entry_X"]     = _float_field("Mod_Entry_X",  pos.x if pos else 0.0)
    root.fields["Mod_Entry_Y"]     = _float_field("Mod_Entry_Y",  pos.y if pos else 0.0)
    root.fields["Mod_Entry_Z"]     = _float_field("Mod_Entry_Z",  pos.z if pos else 0.0)
    root.fields["Mod_Entry_Dir_X"] = _float_field("Mod_Entry_Dir_X", ifo.entry_direction)

    # Script hooks — only write non-empty
    _script_map = {
        "Mod_OnModLoad":    ifo.on_module_load,
        "Mod_OnModStart":   ifo.on_module_start,
        "Mod_OnPlrDeath":   ifo.on_player_death,
        "Mod_OnPlrDying":   ifo.on_player_dying,
        "Mod_OnPlrLvlUp":   ifo.on_player_levelup,
        "Mod_OnSpawnBtnDn": ifo.on_player_respawn,
        "Mod_OnPlrRest":    ifo.on_player_rest,
        "Mod_OnHeartbeat":  ifo.on_heartbeat,
        "Mod_OnClientEntr": ifo.on_client_enter,
        "Mod_OnClientLeav": ifo.on_client_leave,    # 16-char truncation of Mod_OnClientLeave
        "Mod_OnCutsnAbort": ifo.on_cutscene_abort,
        "Mod_OnUnAqreItem": ifo.on_unacquire_item,
        "Mod_OnAcquireIte": ifo.on_acquire_item,    # 16-char truncation of Mod_OnAcquireItem
        "Mod_OnActvtItem":  ifo.on_activate_item,
    }
    for label, value in _script_map.items():
        if value:
            root.fields[label] = _resref_field(label, value[:16])

    # Expansion list (empty is fine)
    root.fields["Mod_Expan_List"] = GFFField("Mod_Expan_List", GFFFieldType.LIST, [])

    writer = GFFWriter(root)
    writer.write_file(path)
    log.info(f"Saved IFO: {path}")
