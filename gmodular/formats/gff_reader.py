"""
GModular — GFF V3.2 Binary Reader
Reads .ARE, .GIT, .IFO, .DLG and other Odyssey GFF files.

Binary layout (from xoreos-docs/specs/bioware/GFF_Format.pdf):
  Header (56 bytes):
    FileType     4 chars
    FileVersion  4 chars  (should be "V3.2")
    StructOffset  uint32
    StructCount   uint32
    FieldOffset   uint32
    FieldCount    uint32
    LabelOffset   uint32
    LabelCount    uint32
    FieldDataOffset uint32
    FieldDataCount  uint32
    FieldIndicesOffset uint32
    FieldIndicesCount  uint32
    ListIndicesOffset  uint32
    ListIndicesCount   uint32
"""
from __future__ import annotations
import struct
import logging
from typing import BinaryIO, List, Dict, Any, Optional, Tuple

from .gff_types import (
    GFFFieldType, GFFField, GFFStruct, GFFRoot,
    GITData, GITPlaceable, GITCreature, GITDoor,
    GITTrigger, GITSoundObject, GITWaypoint, GITStoreObject,
    AREData, IFOData, Vector3, Quaternion,
    LocalizedString, Language, Gender, locstring_pair,
)

log = logging.getLogger(__name__)

_HEADER_FMT = "<4s4s12I"
_HEADER_SIZE = 56


# ─────────────────────────────────────────────────────────────────────────────
#  Low-level GFF Parser
# ─────────────────────────────────────────────────────────────────────────────

class GFFParseError(Exception):
    pass


class GFFReader:
    """
    Reads a GFF V3.2 binary file into a tree of GFFStruct objects.
    Based on xoreos src/aurora/gff3file.cpp.
    """

    def __init__(self, data: bytes):
        self._data = data
        self._root: Optional[GFFRoot] = None

    @classmethod
    def from_file(cls, path: str) -> "GFFReader":
        with open(path, "rb") as f:
            return cls(f.read())

    @classmethod
    def from_bytes(cls, data: bytes) -> "GFFReader":
        return cls(data)

    def parse(self) -> GFFRoot:
        if self._root is not None:
            return self._root

        d = self._data
        if len(d) < _HEADER_SIZE:
            raise GFFParseError("File too small to be a GFF")

        (file_type, file_version,
         struct_off, struct_count,
         field_off, field_count,
         label_off, label_count,
         fdata_off, fdata_count,
         findices_off, findices_count,
         lindices_off, lindices_count
         ) = struct.unpack_from(_HEADER_FMT, d, 0)

        file_type   = file_type.decode("ascii", errors="replace").rstrip("\x00").strip()
        file_version = file_version.decode("ascii", errors="replace").rstrip("\x00").strip()

        if file_version not in ("V3.2", "V3.3"):
            log.warning(f"GFF version {file_version!r} (expected V3.2) — trying anyway")

        # Read labels
        labels: List[str] = []
        for i in range(label_count):
            off = label_off + i * 16
            raw = d[off:off + 16]
            labels.append(raw.rstrip(b"\x00").decode("ascii", errors="replace"))

        # Read all raw field blocks
        raw_fields: List[Tuple[int, int, int]] = []  # (type_id, label_idx, data_or_offset)
        for i in range(field_count):
            off = field_off + i * 12
            type_id, label_idx, data = struct.unpack_from("<III", d, off)
            raw_fields.append((type_id, label_idx, data))

        # Read struct definitions
        struct_defs: List[Tuple[int, int, int]] = []  # (type, data_or_offset, field_count)
        for i in range(struct_count):
            off = struct_off + i * 12
            stype, sdata, sfields = struct.unpack_from("<III", d, off)
            struct_defs.append((stype, sdata, sfields))

        # Helpers to read variable-length data
        def read_field_data(offset: int, size: int) -> bytes:
            start = fdata_off + offset
            return d[start:start + size]

        def read_cexo_string(data_offset: int) -> str:
            off = fdata_off + data_offset
            size = struct.unpack_from("<I", d, off)[0]
            raw = d[off + 4:off + 4 + size]
            return raw.decode("utf-8", errors="replace")

        def read_resref(data_or_inline: int, is_complex: bool = True) -> str:
            if is_complex:
                off = fdata_off + data_or_inline
                size = d[off]
                raw = d[off + 1:off + 1 + size]
                return raw.decode("ascii", errors="replace").rstrip("\x00")
            else:
                # ResRef stored inline (4 bytes) for short resrefs
                raw = struct.pack("<I", data_or_inline)
                return raw.decode("ascii", errors="replace").rstrip("\x00")

        def read_cexolocstring(data_offset: int) -> LocalizedString:
            """Read a CExoLocString field, decoding each substring with the
            correct Windows codepage for its language ID.

            Binary layout:
              uint32  total_size
              uint32  stringref   (TLK index; 0xFFFFFFFF = not set)
              uint32  string_count
              for each string:
                uint32  substring_id  (language*2 + gender)
                uint32  length_bytes
                bytes   text          (encoded with language-specific codepage)

            References:
              xoreos src/aurora/locstring.cpp
              PyKotor Libraries/PyKotor/src/pykotor/common/language.py
            """
            off = fdata_off + data_offset
            str_ref = struct.unpack_from("<I", d, off + 4)[0]
            count   = struct.unpack_from("<I", d, off + 8)[0]
            pos = off + 12
            ls = LocalizedString(stringref=str_ref)
            for _ in range(count):
                if pos + 8 > len(d):
                    break
                substring_id, str_len = struct.unpack_from("<II", d, pos)
                pos += 8
                raw = d[pos:pos + str_len]
                pos += str_len
                try:
                    lang, gender = locstring_pair(substring_id)
                    encoding = lang.get_encoding()
                    txt = raw.decode(encoding, errors="replace")
                except Exception:
                    txt = raw.decode("cp1252", errors="replace")
                ls._substrings[substring_id] = txt
            return ls

        def read_void_data(data_offset: int) -> bytes:
            off = fdata_off + data_offset
            size = struct.unpack_from("<I", d, off)[0]
            return d[off + 4:off + 4 + size]

        def field_indices_for(sdata: int, sfields: int) -> List[int]:
            """Return list of field indices for a struct."""
            if sfields == 1:
                return [sdata]
            else:
                off = findices_off + sdata
                return list(struct.unpack_from(f"<{sfields}I", d, off))

        def list_items(list_off: int) -> List[int]:
            """Return struct indices from a LIST field."""
            real_off = lindices_off + list_off
            count = struct.unpack_from("<I", d, real_off)[0]
            return list(struct.unpack_from(f"<{count}I", d, real_off + 4))

        def parse_field(type_id: int, label_idx: int, raw_data: int) -> GFFField:
            label = labels[label_idx] if label_idx < len(labels) else f"field_{label_idx}"
            ft = type_id

            if ft == GFFFieldType.BYTE:
                return GFFField(label, ft, raw_data & 0xFF)
            elif ft == GFFFieldType.CHAR:
                return GFFField(label, ft, struct.pack("<I", raw_data)[0])
            elif ft == GFFFieldType.WORD:
                return GFFField(label, ft, raw_data & 0xFFFF)
            elif ft == GFFFieldType.SHORT:
                v = raw_data & 0xFFFF
                return GFFField(label, ft, struct.unpack("<h", struct.pack("<H", v))[0])
            elif ft == GFFFieldType.DWORD:
                return GFFField(label, ft, raw_data)
            elif ft == GFFFieldType.INT:
                v = struct.unpack("<i", struct.pack("<I", raw_data))[0]
                return GFFField(label, ft, v)
            elif ft == GFFFieldType.DWORD64:
                off = fdata_off + raw_data
                v = struct.unpack_from("<Q", d, off)[0]
                return GFFField(label, ft, v)
            elif ft == GFFFieldType.INT64:
                off = fdata_off + raw_data
                v = struct.unpack_from("<q", d, off)[0]
                return GFFField(label, ft, v)
            elif ft == GFFFieldType.FLOAT:
                v = struct.unpack("<f", struct.pack("<I", raw_data))[0]
                return GFFField(label, ft, v)
            elif ft == GFFFieldType.DOUBLE:
                off = fdata_off + raw_data
                v = struct.unpack_from("<d", d, off)[0]
                return GFFField(label, ft, v)
            elif ft == GFFFieldType.CEXOSTRING:
                return GFFField(label, ft, read_cexo_string(raw_data))
            elif ft == GFFFieldType.RESREF:
                return GFFField(label, ft, read_resref(raw_data))
            elif ft == GFFFieldType.CEXOLOCSTRING:
                return GFFField(label, ft, read_cexolocstring(raw_data))
            elif ft == GFFFieldType.VOID:
                return GFFField(label, ft, read_void_data(raw_data))
            elif ft == GFFFieldType.STRUCT:
                sub = parse_struct(raw_data)
                return GFFField(label, ft, sub)
            elif ft == GFFFieldType.LIST:
                items = []
                for sidx in list_items(raw_data):
                    items.append(parse_struct(sidx))
                return GFFField(label, ft, items)
            elif ft == GFFFieldType.ORIENTATION:
                off = fdata_off + raw_data
                x, y, z, w = struct.unpack_from("<4f", d, off)
                return GFFField(label, ft, Quaternion(x, y, z, w))
            elif ft == GFFFieldType.VECTOR:
                off = fdata_off + raw_data
                x, y, z = struct.unpack_from("<3f", d, off)
                return GFFField(label, ft, Vector3(x, y, z))
            elif ft == GFFFieldType.STRREF:
                return GFFField(label, ft, raw_data)
            else:
                log.debug(f"Unknown GFF field type {type_id} for label {label!r}")
                return GFFField(label, type_id, raw_data)

        def parse_struct(struct_idx: int) -> GFFStruct:
            if struct_idx >= len(struct_defs):
                return GFFStruct(struct_id=0)
            stype, sdata, sfields = struct_defs[struct_idx]
            s = GFFStruct(struct_id=stype)
            if sfields == 0:
                return s
            indices = field_indices_for(sdata, sfields)
            for fi in indices:
                if fi >= len(raw_fields):
                    continue
                ftype, flabel, fdata = raw_fields[fi]
                try:
                    fld = parse_field(ftype, flabel, fdata)
                    s.fields[fld.label] = fld
                except Exception as e:
                    log.debug(f"Field parse error at index {fi}: {e}")
            return s

        # Parse root struct (always struct 0)
        root_struct = parse_struct(0)
        root = GFFRoot(
            file_type=file_type,
            file_version=file_version,
            struct_id=root_struct.struct_id,
            fields=root_struct.fields,
        )
        self._root = root
        return root


# ─────────────────────────────────────────────────────────────────────────────
#  High-Level GIT Deserialiser
# ─────────────────────────────────────────────────────────────────────────────

def _get_locstr(root_or_struct: Any, label: str, default: str = "") -> str:
    """Get a string from either a LocalizedString or a plain str field."""
    if hasattr(root_or_struct, 'fields'):
        fld = root_or_struct.fields.get(label)
        v = fld.value if fld is not None else None
    else:
        v = root_or_struct.get(label)
    if v is None:
        return default
    if isinstance(v, LocalizedString):
        result = v.get_english()
        return result if result else default
    return str(v)


def _get_s(struct: GFFStruct, label: str, default: str = "") -> str:
    v = struct.get(label, default)
    if v is None:
        return default
    if isinstance(v, LocalizedString):
        return v.get_english()
    return str(v)

def _get_f(struct: GFFStruct, label: str, default: float = 0.0) -> float:
    v = struct.get(label, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _get_i(struct: GFFStruct, label: str, default: int = 0) -> int:
    v = struct.get(label, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

def _get_vec(struct: GFFStruct, label: str) -> Vector3:
    v = struct.get(label)
    if isinstance(v, Vector3):
        return v
    return Vector3()

def _git_placeable(s: GFFStruct) -> GITPlaceable:
    p = GITPlaceable()
    p.template_resref = _get_s(s, "TemplateResRef")
    p.resref          = _get_s(s, "ResRef")
    p.tag             = _get_s(s, "Tag")
    # Position stored as three separate float fields in real GIT files
    p.position = Vector3(
        _get_f(s, "XPosition"),
        _get_f(s, "YPosition"),
        _get_f(s, "ZPosition"),
    )
    p.bearing  = _get_f(s, "Bearing")
    p.on_used                  = _get_s(s, "OnUsed")
    p.on_heartbeat             = _get_s(s, "OnHeartbeat")
    p.on_closed                = _get_s(s, "OnClosed")
    p.on_damaged               = _get_s(s, "OnDamaged")
    p.on_death                 = _get_s(s, "OnDeath")
    p.on_end_conversation      = _get_s(s, "OnEndConversation")
    p.on_inventory_disturbed   = _get_s(s, "OnInventoryDisturbed")
    p.on_lock                  = _get_s(s, "OnLock")
    p.on_melee_attacked        = _get_s(s, "OnMeleeAttacked")
    p.on_open                  = _get_s(s, "OnOpen")
    p.on_user_defined          = _get_s(s, "OnUserDefined")
    return p

def _git_creature(s: GFFStruct) -> GITCreature:
    c = GITCreature()
    c.template_resref = _get_s(s, "TemplateResRef")
    c.resref          = _get_s(s, "ResRef")
    c.tag             = _get_s(s, "Tag")
    x = _get_f(s, "XPosition")
    y = _get_f(s, "YPosition")
    z = _get_f(s, "ZPosition")
    c.position = Vector3(x, y, z)
    c.bearing  = _get_f(s, "XOrientation")   # creature uses XOrientation
    c.on_heartbeat         = _get_s(s, "OnHeartbeat")
    c.on_death             = _get_s(s, "OnDeath")
    c.on_end_conversation  = _get_s(s, "OnEndConversation")
    c.on_disturbed         = _get_s(s, "OnDisturbed")
    c.on_blocked           = _get_s(s, "OnBlocked")
    c.on_attacked          = _get_s(s, "OnAttacked")
    c.on_damaged           = _get_s(s, "OnDamaged")
    c.on_user_defined      = _get_s(s, "OnUserDefined")
    c.on_notice            = _get_s(s, "OnNotice")
    c.on_conversation      = _get_s(s, "Conversation")
    c.on_spawn             = _get_s(s, "OnSpawn")
    return c

def _git_door(s: GFFStruct) -> GITDoor:
    d = GITDoor()
    d.template_resref = _get_s(s, "TemplateResRef")
    d.resref          = _get_s(s, "ResRef")
    d.tag             = _get_s(s, "Tag")
    d.position = Vector3(_get_f(s, "X"), _get_f(s, "Y"), _get_f(s, "Z"))
    d.bearing  = _get_f(s, "Bearing")
    d.linked_to       = _get_s(s, "LinkedTo")
    d.linked_to_flags = _get_i(s, "LinkedToFlags")
    d.transition_destination = _get_s(s, "TransitionDestin")
    d.on_open  = _get_s(s, "OnOpen")
    d.on_closed = _get_s(s, "OnClosed")
    d.on_damaged = _get_s(s, "OnDamaged")
    d.on_death   = _get_s(s, "OnDeath")
    d.on_heartbeat = _get_s(s, "OnHeartbeat")
    d.on_lock    = _get_s(s, "OnLock")
    d.on_melee_attacked = _get_s(s, "OnMeleeAttacked")
    d.on_open2   = _get_s(s, "OnOpen2")
    d.on_unlock  = _get_s(s, "OnUnlock")
    d.on_fail_to_open = _get_s(s, "OnFailToOpen")
    d.on_user_defined = _get_s(s, "OnUserDefined")
    return d

def _git_trigger(s: GFFStruct) -> GITTrigger:
    t = GITTrigger()
    t.template_resref = _get_s(s, "TemplateResRef")
    t.resref = _get_s(s, "ResRef")
    t.tag    = _get_s(s, "Tag")
    t.position = Vector3(_get_f(s, "XPosition"), _get_f(s, "YPosition"), _get_f(s, "ZPosition"))
    # Geometry is a LIST of structs with Point field
    geo_list = s.get("Geometry", [])
    if isinstance(geo_list, list):
        for gs in geo_list:
            if isinstance(gs, GFFStruct):
                pt = gs.get("Point")
                if isinstance(pt, Vector3):
                    t.geometry.append(pt)
    t.on_enter     = _get_s(s, "OnEnter")
    t.on_exit      = _get_s(s, "OnExit")
    t.on_heartbeat = _get_s(s, "OnHeartbeat")
    t.on_user_defined = _get_s(s, "OnUserDefined")
    return t

def _git_sound(s: GFFStruct) -> GITSoundObject:
    so = GITSoundObject()
    so.template_resref = _get_s(s, "TemplateResRef")
    so.resref = _get_s(s, "ResRef")
    so.tag    = _get_s(s, "Tag")
    so.position = Vector3(_get_f(s, "XPosition"), _get_f(s, "YPosition"), _get_f(s, "ZPosition"))
    return so

def _git_waypoint(s: GFFStruct) -> GITWaypoint:
    w = GITWaypoint()
    w.template_resref = _get_s(s, "TemplateResRef")
    w.resref = _get_s(s, "ResRef")
    w.tag    = _get_s(s, "Tag")
    w.position = Vector3(_get_f(s, "XPosition"), _get_f(s, "YPosition"), _get_f(s, "ZPosition"))
    w.bearing  = _get_f(s, "XOrientation")   # KotOR stores bearing as XOrientation
    w.map_note = _get_s(s, "MapNote")
    w.map_note_enabled = _get_i(s, "MapNoteEnabled")
    return w

def _git_store(s: GFFStruct) -> GITStoreObject:
    st = GITStoreObject()
    st.template_resref = _get_s(s, "TemplateResRef")
    st.resref = _get_s(s, "ResRef")
    st.tag    = _get_s(s, "Tag")
    st.position = Vector3(_get_f(s, "XPosition"), _get_f(s, "YPosition"), _get_f(s, "ZPosition"))
    st.bearing  = _get_f(s, "Bearing")
    return st


def load_git(path: str) -> GITData:
    """Load a .GIT file and return a GITData object."""
    reader = GFFReader.from_file(path)
    root   = reader.parse()
    git    = GITData()

    def _iter_list(label: str):
        field = root.fields.get(label)
        if field is None:
            return []
        v = field.value
        if isinstance(v, list):
            return v
        return []

    for s in _iter_list("Placeable List"):
        try:
            git.placeables.append(_git_placeable(s))
        except Exception as e:
            log.debug(f"Placeable parse error: {e}")

    for s in _iter_list("Creature List"):
        try:
            git.creatures.append(_git_creature(s))
        except Exception as e:
            log.debug(f"Creature parse error: {e}")

    for s in _iter_list("Door List"):
        try:
            git.doors.append(_git_door(s))
        except Exception as e:
            log.debug(f"Door parse error: {e}")

    for s in _iter_list("TriggerList"):
        try:
            git.triggers.append(_git_trigger(s))
        except Exception as e:
            log.debug(f"Trigger parse error: {e}")

    for s in _iter_list("SoundList"):
        try:
            git.sounds.append(_git_sound(s))
        except Exception as e:
            log.debug(f"Sound parse error: {e}")

    for s in _iter_list("WaypointList"):
        try:
            git.waypoints.append(_git_waypoint(s))
        except Exception as e:
            log.debug(f"Waypoint parse error: {e}")

    for s in _iter_list("StoreList"):
        try:
            git.stores.append(_git_store(s))
        except Exception as e:
            log.debug(f"Store parse error: {e}")

    # Ambient audio
    git.ambient_sound_day   = root.get("AmbientSndDay", "")
    git.ambient_sound_night = root.get("AmbientSndNit", "")
    git.ambient_sound_dayvol   = root.get("AmbientSndDayVol", 50)
    git.ambient_sound_nightvol = root.get("AmbientSndNitVol", 50)
    git.env_audio           = root.get("EnvAudio", 0)

    log.info(f"Loaded GIT: {git.object_count} objects "
             f"({len(git.placeables)}p {len(git.creatures)}c "
             f"{len(git.doors)}d {len(git.triggers)}t)")
    return git


def load_are(path: str) -> AREData:
    """Load a .ARE file and return an AREData object."""
    reader = GFFReader.from_file(path)
    root   = reader.parse()
    are    = AREData()
    are.tag  = root.get("Tag", "")
    are.name = _get_locstr(root, "Name", "")
    # Room list
    rooms_list = root.fields.get("Rooms")
    if rooms_list and isinstance(rooms_list.value, list):
        for rs in rooms_list.value:
            if isinstance(rs, GFFStruct):
                room_name = _get_s(rs, "RoomName")
                if room_name:
                    are.rooms.append(room_name)
    are.fog_enabled   = root.get("FogEnabled", 0)
    are.fog_near      = root.get("FogNear", 0.0)
    are.fog_far       = root.get("FogFar", 50.0)
    are.fog_color     = root.get("FogColor2", 0)
    are.ambient_color = root.get("AmbientColor", 0x404040)
    are.diffuse_color = root.get("DiffuseColor", 0x808080)
    are.tileset_resref = root.get("Tileset", "")
    are.sky_box       = root.get("SkyboxName", "")
    are.shadow_opacity = root.get("ShadowOpacity", 100)
    are.wind_power    = root.get("WindPower", 0)
    log.info(f"Loaded ARE: tag={are.tag!r}, {len(are.rooms)} rooms")
    return are


def load_ifo(path: str) -> IFOData:
    """Load a .IFO file and return an IFOData object."""
    reader = GFFReader.from_file(path)
    root   = reader.parse()
    ifo    = IFOData()
    ifo.mod_name        = _get_locstr(root, "Mod_Name", "New Module")
    ifo.mod_description = _get_locstr(root, "Mod_Description", "")
    ifo.entry_area      = root.get("Mod_Entry_Area", "")
    x = root.get("Mod_Entry_X", 0.0)
    y = root.get("Mod_Entry_Y", 0.0)
    z = root.get("Mod_Entry_Z", 0.0)
    ifo.entry_position  = Vector3(x, y, z)
    ifo.entry_direction = root.get("Mod_Entry_Dir_X", 0.0)
    ifo.on_module_load  = root.get("Mod_OnModLoad", "")
    ifo.on_module_start = root.get("Mod_OnModStart", "")
    ifo.on_player_death = root.get("Mod_OnPlrDeath", "")
    ifo.on_player_dying = root.get("Mod_OnPlrDying", "")
    ifo.on_player_levelup  = root.get("Mod_OnPlrLvlUp", "")
    ifo.on_player_respawn  = root.get("Mod_OnSpawnBtnDn", "")
    ifo.on_player_rest     = root.get("Mod_OnPlrRest", "")
    ifo.on_heartbeat    = root.get("Mod_OnHeartbeat", "")
    ifo.on_client_enter = root.get("Mod_OnClientEntr", "")
    ifo.on_client_leave = root.get("Mod_OnClientLeav", "")   # 16-char label in binary GFF
    ifo.on_cutscene_abort   = root.get("Mod_OnCutsnAbort", "")
    ifo.on_unacquire_item   = root.get("Mod_OnUnAqreItem", "")
    ifo.on_acquire_item     = root.get("Mod_OnAcquireIte", "")  # 16-char truncation
    ifo.on_activate_item    = root.get("Mod_OnActvtItem", "")
    log.info(f"Loaded IFO: name={ifo.mod_name!r}, entry={ifo.entry_area!r}")
    return ifo
