"""
GModular — Complete KotOR Format Library
=========================================
Implements all binary/text KotOR format parsers and writers that were
previously missing from GModular's format layer.  Based on deep analysis of:

  * Kotor.NET  (NickHugi / C# reference implementation)
  * PyKotor    (OldRepublicDevs / Python reference with engine addresses)
  * xoreos     (Community RE project)
  * swkotor.exe binary RE notes in PyKotor docstrings

Formats implemented here
-------------------------
| Format | Ext  | R | W | Notes                                         |
|--------|------|---|---|-----------------------------------------------|
| SSF    | .ssf | ✓ | ✓ | Sound Set File — 28 creature sound StrRefs    |
| LIP    | .lip | ✓ | ✓ | Lip-sync keyframes (time + viseme shape)      |
| TXI    | .txi | ✓ | ✓ | Texture metadata / procedure config           |
| VIS    | .vis | ✓ | ✓ | Room visibility graph (ASCII)                 |
| PTH    | .pth | ✓ | ✓ | Path graph (GFF-wrapped XY waypoint nodes)    |
| 2DA    | .2da | ✓ | ✓ | Two-dimensional array (binary v2.b + ASCII)   |
| TLK    | .tlk | ✓ | ✓ | Talk table (string bank)                      |
| NCS    | .ncs | ✓ | ✓ | NWScript bytecode (disassemble + reassemble)  |
| LTR    | .ltr | ✓ | ✓ | Letter/name-gen Markov chain tables           |

All classes are Qt-free and can be unit-tested without a display.
"""
from __future__ import annotations

import io
import logging
import os
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  SSF — Sound Set File
#  Binary: "SSF V1.1" header + 28 × int32 StrRefs
#  Ref: Kotor.NET/Formats/KotorSSF, PyKotor/formats/ssf
# ═══════════════════════════════════════════════════════════════════════════

class SSFSound(IntEnum):
    """28 creature sound event slots (K1 / TSL)."""
    BATTLE_CRY_1        = 0
    BATTLE_CRY_2        = 1
    BATTLE_CRY_3        = 2
    BATTLE_CRY_4        = 3
    BATTLE_CRY_5        = 4
    BATTLE_CRY_6        = 5
    SELECT_1            = 6
    SELECT_2            = 7
    SELECT_3            = 8
    ATTACK_GRUNT_1      = 9
    ATTACK_GRUNT_2      = 10
    ATTACK_GRUNT_3      = 11
    PAIN_GRUNT_1        = 12
    PAIN_GRUNT_2        = 13
    LOW_HEALTH          = 14
    DEAD                = 15
    CRITICAL_HIT        = 16
    TARGET_IMMUNE       = 17
    LAY_MINE            = 18
    DISARM_MINE         = 19
    BEGIN_STEALTH       = 20
    BEGIN_SEARCH        = 21
    BEGIN_UNLOCK        = 22
    UNLOCK_FAILED       = 23
    UNLOCK_SUCCESS      = 24
    SEPARATED_FROM_PARTY = 25
    REJOINED_PARTY      = 26
    POISONED            = 27

_SSF_SOUND_NAMES: Dict[SSFSound, str] = {
    SSFSound.BATTLE_CRY_1: "Battle Cry 1",
    SSFSound.BATTLE_CRY_2: "Battle Cry 2",
    SSFSound.BATTLE_CRY_3: "Battle Cry 3",
    SSFSound.BATTLE_CRY_4: "Battle Cry 4",
    SSFSound.BATTLE_CRY_5: "Battle Cry 5",
    SSFSound.BATTLE_CRY_6: "Battle Cry 6",
    SSFSound.SELECT_1: "Select 1",
    SSFSound.SELECT_2: "Select 2",
    SSFSound.SELECT_3: "Select 3",
    SSFSound.ATTACK_GRUNT_1: "Attack Grunt 1",
    SSFSound.ATTACK_GRUNT_2: "Attack Grunt 2",
    SSFSound.ATTACK_GRUNT_3: "Attack Grunt 3",
    SSFSound.PAIN_GRUNT_1: "Pain Grunt 1",
    SSFSound.PAIN_GRUNT_2: "Pain Grunt 2",
    SSFSound.LOW_HEALTH: "Low Health",
    SSFSound.DEAD: "Dead",
    SSFSound.CRITICAL_HIT: "Critical Hit",
    SSFSound.TARGET_IMMUNE: "Target Immune",
    SSFSound.LAY_MINE: "Lay Mine",
    SSFSound.DISARM_MINE: "Disarm Mine",
    SSFSound.BEGIN_STEALTH: "Begin Stealth",
    SSFSound.BEGIN_SEARCH: "Begin Search",
    SSFSound.BEGIN_UNLOCK: "Begin Unlock",
    SSFSound.UNLOCK_FAILED: "Unlock Failed",
    SSFSound.UNLOCK_SUCCESS: "Unlock Success",
    SSFSound.SEPARATED_FROM_PARTY: "Separated from Party",
    SSFSound.REJOINED_PARTY: "Rejoined Party",
    SSFSound.POISONED: "Poisoned",
}

_SSF_NUM_SOUNDS = 28
_SSF_HEADER = b"SSF V1.1"
_SSF_OFFSET = 12   # header (8) + offset_field (4) — sound table starts here


class SSFData:
    """In-memory representation of an SSF Sound Set File.

    Each of the 28 slots holds a StrRef (int32) into dialog.tlk.
    A value of -1 (0xFFFFFFFF as uint32) means no sound is assigned.
    """

    def __init__(self) -> None:
        self._strefs: List[int] = [-1] * _SSF_NUM_SOUNDS

    # ── Access ────────────────────────────────────────────────────────────

    def get(self, sound: SSFSound) -> int:
        """Return the StrRef for *sound* (-1 = not set)."""
        return self._strefs[int(sound)]

    def set(self, sound: SSFSound, strref: int) -> None:
        """Set the StrRef for *sound*."""
        self._strefs[int(sound)] = int(strref)

    def reset(self) -> None:
        """Clear all StrRef assignments."""
        self._strefs = [-1] * _SSF_NUM_SOUNDS

    def as_dict(self) -> Dict[str, int]:
        """Return human-readable name → StrRef mapping."""
        return {_SSF_SOUND_NAMES[s]: self._strefs[int(s)] for s in SSFSound}

    def __repr__(self) -> str:
        assigned = sum(1 for v in self._strefs if v != -1)
        return f"<SSFData {assigned}/{_SSF_NUM_SOUNDS} sounds assigned>"


def read_ssf(data: bytes) -> SSFData:
    """Parse a KotOR SSF binary blob and return an :class:`SSFData`."""
    if len(data) < 12:
        raise ValueError("SSF data too short")
    file_type = data[0:4]
    file_ver  = data[4:8]
    if file_type != b"SSF ":
        raise ValueError(f"Not an SSF file (magic={file_type!r})")
    if file_ver != b"V1.1":
        raise ValueError(f"Unsupported SSF version {file_ver!r}")
    (sounds_off,) = struct.unpack_from("<I", data, 8)
    ssf = SSFData()
    for i in range(_SSF_NUM_SOUNDS):
        pos = sounds_off + i * 4
        if pos + 4 > len(data):
            break
        (raw,) = struct.unpack_from("<I", data, pos)
        # 0xFFFFFFFF → -1 (no sound)
        ssf._strefs[i] = -1 if raw == 0xFFFFFFFF else raw
    return ssf


def write_ssf(ssf: SSFData) -> bytes:
    """Serialise an :class:`SSFData` to KotOR SSF binary format."""
    buf = bytearray()
    buf += b"SSF "
    buf += b"V1.1"
    buf += struct.pack("<I", _SSF_OFFSET)   # offset to sound table
    for i in range(_SSF_NUM_SOUNDS):
        raw = 0xFFFFFFFF if ssf._strefs[i] == -1 else ssf._strefs[i]
        buf += struct.pack("<I", raw)
    return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════
#  LIP — Lip-Sync File
#  Binary: "LIP V1.0" + float length + uint32 count + N × (float time, uint8 shape)
#  Ref: PyKotor/formats/lip, CLIP::LoadLip @ swkotor.exe:0x0070c590
# ═══════════════════════════════════════════════════════════════════════════

class LIPShape(IntEnum):
    """16 viseme shapes used in KotOR lip-sync animation (Preston Blair phoneme set)."""
    NEUTRAL   = 0
    EE        = 1   # "ee", "ea"
    EH        = 2   # "e", "i"
    SIL       = 3   # silence / idle
    AH        = 4   # "a", "ah"
    OH        = 5   # "o"
    OOH       = 6   # "oo", "ew"
    TH        = 7   # "th"
    D_T_N     = 8   # "d", "t", "n"
    F_V       = 9   # "f", "v"
    S_Z       = 10  # "s", "z"
    M_P_B     = 11  # "m", "p", "b"
    CH_SH_ZH  = 12  # "ch", "sh"
    L         = 13  # "l"
    R         = 14  # "r"
    W_Q_OO    = 15  # "w", "q", "oo"


@dataclass
class LIPKeyframe:
    """Single lip-sync keyframe: time in seconds and mouth shape."""
    time:  float
    shape: LIPShape


@dataclass
class LIPData:
    """In-memory LIP (lip-sync) file.

    Attributes
    ----------
    length:    Total audio clip duration in seconds.
    keyframes: Ordered list of (time, shape) keyframes.
    """
    length: float = 0.0
    keyframes: List[LIPKeyframe] = field(default_factory=list)

    def add(self, time: float, shape: LIPShape) -> None:
        """Append a keyframe (caller is responsible for ordering by time)."""
        self.keyframes.append(LIPKeyframe(time=time, shape=shape))

    def sorted_keyframes(self) -> List[LIPKeyframe]:
        """Return keyframes sorted by ascending time."""
        return sorted(self.keyframes, key=lambda k: k.time)

    def __len__(self) -> int:
        return len(self.keyframes)

    def __iter__(self) -> Iterator[LIPKeyframe]:
        return iter(self.keyframes)

    def __repr__(self) -> str:
        return f"<LIPData length={self.length:.2f}s frames={len(self.keyframes)}>"


def read_lip(data: bytes) -> LIPData:
    """Parse KotOR LIP binary data."""
    if len(data) < 16:
        raise ValueError("LIP data too short")
    if data[0:4] != b"LIP ":
        raise ValueError(f"Not a LIP file (magic={data[0:4]!r})")
    if data[4:8] != b"V1.0":
        raise ValueError(f"Unsupported LIP version {data[4:8]!r}")
    (length,)      = struct.unpack_from("<f", data, 8)
    (entry_count,) = struct.unpack_from("<I", data, 12)
    lip = LIPData(length=length)
    off = 16
    for _ in range(entry_count):
        if off + 5 > len(data):
            break
        (t,)     = struct.unpack_from("<f", data, off); off += 4
        shape_b  = data[off]; off += 1
        try:
            shape = LIPShape(shape_b)
        except ValueError:
            shape = LIPShape.NEUTRAL
        lip.add(t, shape)
    return lip


def write_lip(lip: LIPData) -> bytes:
    """Serialise a :class:`LIPData` to KotOR LIP binary format."""
    buf = bytearray()
    buf += b"LIP "
    buf += b"V1.0"
    buf += struct.pack("<f", lip.length)
    buf += struct.pack("<I", len(lip.keyframes))
    for kf in lip.keyframes:
        buf += struct.pack("<f", kf.time)
        buf += struct.pack("<B", int(kf.shape))
    return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════
#  TXI — Texture Extended Info
#  ASCII key=value pairs embedded in .txi files (same name as .tpc/.tga)
#  Ref: Kotor.NET/Formats/KotorTXI, swkotor.exe texture loader
# ═══════════════════════════════════════════════════════════════════════════

class TXIData:
    """Parsed texture metadata from a .txi file.

    Common fields
    -------------
    - ``envmaptexture``  : cubemap environment reflection map
    - ``bumpyshinytexture`` : bump-map reference
    - ``blending``       : additive / punchthrough
    - ``proceduretype``  : cycle / water / arturo / ringtexture
    - ``numx``, ``numy`` : animation frame grid
    - ``fps``            : frame rate for animated textures
    - ``decal``          : 1 = don't write to depth buffer
    - ``clamp``          : 1 = clamp UV wrapping
    - ``downsamplemin``, ``downsamplemax`` : mip-map limits
    - ``filter``         : LINEAR / NEAREST
    """

    def __init__(self) -> None:
        self._fields: Dict[str, str] = {}

    # ── Access ────────────────────────────────────────────────────────────

    def get(self, key: str, default: str = "") -> str:
        return self._fields.get(key.lower(), default)

    def set(self, key: str, value: str) -> None:
        self._fields[key.lower()] = str(value)

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._fields.get(key.lower(), str(default)))
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self._fields.get(key.lower(), str(default)))
        except (ValueError, TypeError):
            return default

    def has(self, key: str) -> bool:
        return key.lower() in self._fields

    @property
    def all_fields(self) -> Dict[str, str]:
        return dict(self._fields)

    # ── Convenience properties ─────────────────────────────────────────────

    @property
    def envmap(self) -> str:
        return self.get("envmaptexture")

    @property
    def bumpmap(self) -> str:
        return self.get("bumpyshinytexture")

    @property
    def is_animated(self) -> bool:
        return self.get_int("numx") > 0 or self.get_int("numy") > 0

    @property
    def is_procedural(self) -> bool:
        return bool(self.get("proceduretype"))

    @property
    def is_decal(self) -> bool:
        return self.get_int("decal") == 1

    @property
    def blending(self) -> str:
        return self.get("blending", "").lower()

    @property
    def fps(self) -> float:
        return self.get_float("fps", 2.0)

    @property
    def num_frames(self) -> int:
        return max(1, self.get_int("numx", 1) * self.get_int("numy", 1))

    def __repr__(self) -> str:
        return f"<TXIData fields={list(self._fields.keys())}>"


def read_txi(data: bytes) -> TXIData:
    """Parse a TXI ASCII byte stream."""
    txi = TXIData()
    try:
        text = data.decode("latin-1", errors="replace")
    except Exception:
        return txi
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            txi.set(parts[0], parts[1].strip())
        elif len(parts) == 1:
            txi.set(parts[0], "1")
    return txi


def write_txi(txi: TXIData) -> bytes:
    """Serialise a :class:`TXIData` back to ASCII TXI format."""
    lines = []
    # Emit in a stable order for deterministic output
    for key, val in sorted(txi._fields.items()):
        lines.append(f"{key} {val}")
    return ("\n".join(lines) + "\n").encode("latin-1") if lines else b""


def read_txi_file(path: str) -> TXIData:
    """Read TXI from *path*.  Returns empty TXIData if file missing."""
    try:
        return read_txi(Path(path).read_bytes())
    except FileNotFoundError:
        return TXIData()


# ═══════════════════════════════════════════════════════════════════════════
#  VIS — Room Visibility File (ASCII)
#  "room_name count\n  visible_room\n  visible_room\n..."
#  Ref: PyKotor/formats/vis, swkotor.exe LoadVisibility @ 0x004568d0
# ═══════════════════════════════════════════════════════════════════════════

class VISData:
    """Room-to-room visibility graph.

    ``visible[A]`` is the set of room names visible when inside room A.
    This is the data used by GModular's LYT-vis parser for occlusion hints.
    """

    def __init__(self) -> None:
        self._visible: Dict[str, List[str]] = {}

    def add_room(self, room: str) -> None:
        name = room.strip().lower()
        if name not in self._visible:
            self._visible[name] = []

    def set_visible(self, observer: str, seen: str, *, visible: bool = True) -> None:
        obs = observer.strip().lower()
        s   = seen.strip().lower()
        self.add_room(obs)
        self.add_room(s)
        if visible:
            if s not in self._visible[obs]:
                self._visible[obs].append(s)
        else:
            self._visible[obs] = [r for r in self._visible[obs] if r != s]

    def is_visible(self, observer: str, seen: str) -> bool:
        return seen.lower() in self._visible.get(observer.lower(), [])

    def visible_from(self, room: str) -> List[str]:
        return list(self._visible.get(room.lower(), []))

    def all_rooms(self) -> List[str]:
        return list(self._visible.keys())

    def __repr__(self) -> str:
        return f"<VISData rooms={len(self._visible)}>"


def read_vis(data: bytes) -> VISData:
    """Parse a KotOR VIS ASCII file."""
    vis = VISData()
    try:
        text = data.decode("latin-1", errors="replace")
    except Exception:
        return vis

    pairs: List[Tuple[str, str]] = []
    lines_iter = iter(text.splitlines())
    for line in lines_iter:
        tokens = line.split()
        if not tokens:
            continue
        # Skip version-header lines like "room V3.28"
        if len(tokens) >= 2 and tokens[1].startswith("V"):
            continue
        observer = tokens[0]
        vis.add_room(observer)
        try:
            count = int(tokens[1]) if len(tokens) > 1 else 0
        except ValueError:
            count = 0
        for _ in range(count):
            seen_line = next(lines_iter, "").split()
            if seen_line:
                pairs.append((observer, seen_line[0]))

    for obs, seen in pairs:
        vis.set_visible(obs, seen, visible=True)
    return vis


def write_vis(vis: VISData) -> bytes:
    """Serialise a :class:`VISData` to KotOR VIS ASCII format."""
    lines: List[str] = []
    for room in vis.all_rooms():
        visible = vis.visible_from(room)
        lines.append(f"{room} {len(visible)}")
        for seen in visible:
            lines.append(f"  {seen}")
    return ("\n".join(lines) + "\n").encode("latin-1")


# ═══════════════════════════════════════════════════════════════════════════
#  PTH — Path Graph (GFF-wrapped)
#  Stored as a GFF with list of path points (X, Y, connections).
#  Ref: Kotor.NET/Resources/KotorPTH, PyKotor via GFF wrapper
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PTHPoint:
    """A single node in a KotOR path graph."""
    x:           float
    y:           float
    connections: List[int] = field(default_factory=list)  # indices of connected nodes


@dataclass
class PTHData:
    """Parsed KotOR path graph (.pth).

    PTH files are GFF-wrapped but we present a clean Python data model.
    Use :func:`read_pth_from_gff` / :func:`write_pth_to_gff` to convert.
    """
    points: List[PTHPoint] = field(default_factory=list)

    def add_point(self, x: float, y: float) -> int:
        """Add a path node and return its index."""
        idx = len(self.points)
        self.points.append(PTHPoint(x=float(x), y=float(y)))
        return idx

    def connect(self, a: int, b: int) -> None:
        """Add a bidirectional connection between nodes *a* and *b*."""
        if b not in self.points[a].connections:
            self.points[a].connections.append(b)
        if a not in self.points[b].connections:
            self.points[b].connections.append(a)

    def __len__(self) -> int:
        return len(self.points)

    def __repr__(self) -> str:
        return f"<PTHData points={len(self.points)}>"


def read_pth_from_gff(gff_data) -> PTHData:
    """Extract PTH path data from a parsed GFF object (gmodular.formats.gff_reader).

    Parameters
    ----------
    gff_data : GFFData  (from gff_reader.read_gff)
        Parsed GFF whose root struct contains a 'Path_Points' list.
    """
    pth = PTHData()
    try:
        root = gff_data.root
        points_list = root.get("Path_Points", [])
        for node_struct in points_list:
            x = float(node_struct.get("X", 0.0))
            y = float(node_struct.get("Y", 0.0))
            idx = pth.add_point(x, y)
            conns = node_struct.get("Conections", [])  # BioWare typo: "Conections"
            for conn_struct in conns:
                dest = int(conn_struct.get("Destination", -1))
                if dest >= 0:
                    pth.points[idx].connections.append(dest)
    except Exception as exc:
        log.warning("read_pth_from_gff: %s", exc)
    return pth


def write_pth_to_gff_dict(pth: PTHData) -> dict:
    """Produce a plain-dict GFF-like representation for PTH.

    Returns a dict suitable for serialisation with :func:`gmodular.formats.gff_writer`.
    """
    point_list = []
    for pt in pth.points:
        conn_list = [{"Destination": c} for c in pt.connections]
        point_list.append({
            "X":        pt.x,
            "Y":        pt.y,
            "Conections": conn_list,  # Match BioWare typo
        })
    return {"Path_Points": point_list}


def write_pth_to_bytes(pth: "PTHData") -> bytes:
    """Serialise a PTHData to a KotOR .pth GFF binary (file type 'PTH ', V3.2).

    Uses the GFF writer to produce a byte-for-byte valid .pth file.

    Parameters
    ----------
    pth : PTHData
        Path graph to serialise.

    Returns
    -------
    bytes
        Raw .pth GFF binary ready to write to disk or insert into an ERF/MOD.
    """
    from gmodular.formats.gff_writer import GFFWriter
    from gmodular.formats.gff_types import GFFRoot, GFFStruct, GFFField, GFFFieldType

    pt_structs = []
    for pt in pth.points:
        s = GFFStruct()
        s.fields["X"] = GFFField("X", GFFFieldType.FLOAT, float(pt.x))
        s.fields["Y"] = GFFField("Y", GFFFieldType.FLOAT, float(pt.y))
        conn_structs = []
        for dest_idx in pt.connections:
            cs = GFFStruct()
            cs.fields["Destination"] = GFFField("Destination", GFFFieldType.INT, int(dest_idx))
            conn_structs.append(cs)
        s.fields["Conections"] = GFFField("Conections", GFFFieldType.LIST, conn_structs)
        pt_structs.append(s)

    root = GFFRoot(file_type="PTH ")
    root.fields["Path_Points"] = GFFField("Path_Points", GFFFieldType.LIST, pt_structs)
    return GFFWriter(root).to_bytes()


#  2DA — Two-Dimensional Array
#  Binary v2.b format and plain ASCII writer
#  Ref: Kotor.NET/Formats/Kotor2DA/TwoDABinaryWriter, PyKotor/formats/twoda
# ═══════════════════════════════════════════════════════════════════════════

class TwoDAData:
    """In-memory 2DA table (columns × rows).

    This wraps the existing :class:`gmodular.formats.twoda_loader.TwoDALoader`
    data with write capability added.
    """

    def __init__(
        self,
        columns: Optional[List[str]] = None,
        rows: Optional[List[Dict[str, str]]] = None,
        # ``headers`` is an accepted alias for ``columns`` so callers can use
        # either keyword argument without raising a TypeError.
        headers: Optional[List[str]] = None,
    ) -> None:
        resolved_columns = columns if columns is not None else headers
        self.columns: List[str] = list(resolved_columns or [])
        self.rows:    List[Dict[str, str]] = list(rows or [])

    @property
    def headers(self) -> List[str]:
        """Alias for :attr:`columns` – allows ``twoda.headers`` access."""
        return self.columns

    @headers.setter
    def headers(self, value: List[str]) -> None:
        self.columns = list(value)

    @classmethod
    def from_loader(cls, loader) -> "TwoDAData":
        """Construct from a :class:`~gmodular.formats.twoda_loader.TwoDALoader`."""
        return cls(columns=list(loader.columns), rows=[dict(r) for r in loader.rows])

    @classmethod
    def from_bytes(cls, data: bytes) -> "TwoDAData":
        """Parse a binary (or ASCII) .2da byte-string and return a TwoDAData.

        Works by detecting the format and delegating to either the binary 2DA
        reader (``read_2da_binary``) or the text-based :class:`TwoDATable`
        parser.  Raises ``ValueError`` when the bytes cannot be parsed.
        """
        # Try binary format first (magic "2DA \x00V2.b" — note embedded NUL)
        # Our writer produces: b"2DA \x00V2.b\x00\x00<columns>..."
        # data[:4] == b"2DA " is the reliable discriminator.
        if data[:4] == b"2DA " and b"V2.b" in data[:12]:
            return _read_2da_binary_to_twoda(data)
        # Fallback: ASCII / text 2DA
        try:
            from gmodular.formats.twoda_loader import _parse_2da
            table = _parse_2da(data.decode("utf-8", errors="replace"), "__tmp__")
            if table is None:
                raise ValueError("Could not parse 2DA text")
            return cls(columns=list(table.columns),
                       rows=[dict(r) for r in table.rows])
        except Exception as exc:
            raise ValueError(f"TwoDAData.from_bytes failed: {exc}") from exc

    def get(self, row: int, column: str, default: str = "") -> str:
        if row < 0 or row >= len(self.rows):
            return default
        return self.rows[row].get(column, default)

    def set(self, row: int, column: str, value: str) -> None:
        while len(self.rows) <= row:
            self.rows.append({})
        if column not in self.columns:
            self.columns.append(column)
        self.rows[row][column] = str(value)

    def add_row(self, values: Optional[Dict[str, str]] = None) -> int:
        idx = len(self.rows)
        self.rows.append(dict(values or {}))
        return idx

    def add_column(self, name: str) -> None:
        if name not in self.columns:
            self.columns.append(name)

    def row_count(self) -> int:
        return len(self.rows)

    def column_count(self) -> int:
        return len(self.columns)

    def __repr__(self) -> str:
        return f"<TwoDAData rows={len(self.rows)} cols={len(self.columns)}>"


def _read_2da_binary_to_twoda(data: bytes) -> "TwoDAData":
    """Parse a KotOR binary 2DA (V2.b) byte-string into a :class:`TwoDAData`.

    Layout mirrors :func:`write_2da_binary`:
      b"2DA \\x00V2.b\\x00"  (9 bytes)
      extra NUL              (1 byte)
      column headers         (tab-sep, NUL-terminated)
      row_count              uint32 LE
      row headers            NUL-terminated strings × row_count
      cell offsets           uint16 LE × (row_count × col_count)
      pool_size              uint16 LE
      cell data pool         raw bytes
    """
    if data[:5] != b"2DA \x00" or data[5:9] != b"V2.b":
        raise ValueError("Not a binary 2DA V2.b file")

    pos = 9
    # Skip extra NUL(s) after version tag
    while pos < len(data) and data[pos] == 0:
        pos += 1

    # Column headers (tab-separated, NUL-terminated)
    end = data.index(b"\x00", pos)
    col_header_raw = data[pos:end].decode("latin-1")
    columns: List[str] = col_header_raw.split("\t") if col_header_raw else []
    pos = end + 1

    # Row count
    row_count: int = struct.unpack_from("<I", data, pos)[0]
    pos += 4

    # Row headers (NUL-terminated strings × row_count) – we just skip labels
    for _ in range(row_count):
        end = data.index(b"\x00", pos)
        pos = end + 1

    col_count = len(columns)
    total_cells = row_count * col_count

    # Cell offsets
    cell_offsets: List[int] = []
    for _ in range(total_cells):
        cell_offsets.append(struct.unpack_from("<H", data, pos)[0])
        pos += 2

    # Pool size
    _pool_size: int = struct.unpack_from("<H", data, pos)[0]
    pos += 2

    # Cell data pool
    pool = data[pos:]

    def _read_pool_str(offset: int) -> str:
        end_idx = pool.index(b"\x00", offset)
        return pool[offset:end_idx].decode("latin-1")

    rows: List[Dict[str, str]] = []
    for r in range(row_count):
        row: Dict[str, str] = {}
        for c, col in enumerate(columns):
            off = cell_offsets[r * col_count + c]
            row[col] = _read_pool_str(off)
        rows.append(row)

    return TwoDAData(columns=columns, rows=rows)


def write_2da_binary(twoda: "TwoDAData") -> bytes:
    """Serialise to KotOR binary 2DA (v2.b) format.

    Binary layout (Kotor.NET TwoDABinaryWriter as reference):
      Header: "2DA \0V2.b\0"
      NULL byte
      Column headers: tab-separated + NULL
      Row count (uint32)
      Row headers: NULL-separated row indices (0, 1, 2, ...)
      Cell offsets: uint16 per cell (row-major)
      Cell data pool: NULL-separated values
    """
    # ── Build string pool ─────────────────────────────────────────────────
    pool: List[bytes] = []
    pool_map: Dict[str, int] = {}   # value → pool offset
    cell_offsets: List[int] = []

    pool_data = bytearray()

    def intern(value: str) -> int:
        """Add *value* to pool if absent; return its offset."""
        key = value if value != "****" else ""
        if key not in pool_map:
            pool_map[key] = len(pool_data)
            pool_data.extend(key.encode("latin-1") + b"\x00")
        return pool_map[key]

    for row in twoda.rows:
        for col in twoda.columns:
            val = row.get(col, "")
            cell_offsets.append(intern(val))

    # ── Assemble binary ───────────────────────────────────────────────────
    buf = bytearray()

    # File header
    buf += b"2DA \x00"
    buf += b"V2.b\x00"
    buf += b"\x00"   # extra NUL after version

    # Column headers (tab-separated, NUL-terminated)
    col_hdr = "\t".join(twoda.columns).encode("latin-1") + b"\x00"
    buf += col_hdr

    # Row count
    row_count = len(twoda.rows)
    buf += struct.pack("<I", row_count)

    # Row headers: each row index as ASCII NUL-terminated (e.g. b"0\x00", b"1\x00" …)
    for i in range(row_count):
        buf += str(i).encode("latin-1") + b"\x00"

    # Cell offsets (uint16 per cell, row-major)
    for off in cell_offsets:
        buf += struct.pack("<H", min(off, 0xFFFF))

    # Pool size (uint16) — number of bytes in the data pool
    pool_size = len(pool_data)
    buf += struct.pack("<H", min(pool_size, 0xFFFF))

    # Cell data pool
    buf += bytes(pool_data)

    return bytes(buf)


def write_2da_ascii(twoda: TwoDAData) -> bytes:
    """Serialise to KotOR ASCII 2DA format (human-readable)."""
    lines: List[str] = []
    lines.append("2DA V2.0")
    lines.append("")   # blank line 2

    # Column header row — double-space padded
    col_widths = [max(len(c), 8) for c in twoda.columns]
    for i, row in enumerate(twoda.rows):
        for j, col in enumerate(twoda.columns):
            val = row.get(col, "")
            col_widths[j] = max(col_widths[j], len(val) + 1)

    hdr = "    "  # indent for row numbers
    for j, col in enumerate(twoda.columns):
        hdr += col.ljust(col_widths[j] + 1)
    lines.append(hdr.rstrip())

    # Data rows
    for i, row in enumerate(twoda.rows):
        row_str = str(i).ljust(4)
        for j, col in enumerate(twoda.columns):
            val = row.get(col, "****")
            if not val:
                val = "****"
            row_str += val.ljust(col_widths[j] + 1)
        lines.append(row_str.rstrip())

    return ("\n".join(lines) + "\n").encode("latin-1")


# ═══════════════════════════════════════════════════════════════════════════
#  TLK — Talk Table
#  Binary: "TLK V3.2" header + entries with string flags, sounds, StrRefs
#  Ref: Kotor.NET/Formats/KotorTLK, PyKotor/formats/tlk
# ═══════════════════════════════════════════════════════════════════════════

_TLK_HEADER_SIZE  = 20
_TLK_ENTRY_SIZE   = 40
_TLK_MAGIC        = b"TLK "
_TLK_VERSION      = b"V3.2"

TLK_FLAG_TEXT          = 0x01
TLK_FLAG_SOUND_RESREF  = 0x02
TLK_FLAG_SOUND_LENGTH  = 0x04


@dataclass
class TLKEntry:
    """A single talk-table entry."""
    flags:        int   = TLK_FLAG_TEXT
    sound_resref: str   = ""         # up to 16 chars
    volume_var:   int   = 0
    pitch_var:    int   = 0
    offset:       int   = 0          # offset into string data section
    length:       int   = 0          # byte length of string in data section
    sound_length: float = 0.0
    text:         str   = ""


class TLKData:
    """In-memory TLK talk table.

    Entries are indexed by StrRef (0-based integer).
    The sentinel value -1 / 0xFFFFFFFF means "no string".
    """

    def __init__(self, language_id: int = 0) -> None:
        self.language_id = language_id
        self._entries: List[TLKEntry] = []

    def get(self, strref: int) -> Optional[TLKEntry]:
        if strref < 0 or strref >= len(self._entries):
            return None
        return self._entries[strref]

    def get_text(self, strref: int, default: str = "") -> str:
        e = self.get(strref)
        return e.text if e else default

    def set(self, strref: int, entry: TLKEntry) -> None:
        while len(self._entries) <= strref:
            self._entries.append(TLKEntry())
        self._entries[strref] = entry

    def append(self, entry: TLKEntry) -> int:
        idx = len(self._entries)
        self._entries.append(entry)
        return idx

    def entry_count(self) -> int:
        return len(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[TLKEntry]:
        return iter(self._entries)

    def __repr__(self) -> str:
        return f"<TLKData entries={len(self._entries)} lang={self.language_id}>"


def read_tlk(data: bytes) -> TLKData:
    """Parse KotOR TLK binary data.

    This is a faster/lighter standalone parser that supplements
    :class:`gmodular.formats.tlk_reader.TLKReader` with a writable data model.
    """
    if len(data) < _TLK_HEADER_SIZE:
        raise ValueError("TLK data too short")
    if data[0:4] != _TLK_MAGIC:
        raise ValueError(f"Not a TLK file (magic={data[0:4]!r})")
    if data[4:8] != _TLK_VERSION:
        raise ValueError(f"Unsupported TLK version {data[4:8]!r}")

    (lang_id,)     = struct.unpack_from("<I", data, 8)
    (str_count,)   = struct.unpack_from("<I", data, 12)
    (str_off,)     = struct.unpack_from("<I", data, 16)

    tlk = TLKData(language_id=lang_id)
    entry_base = _TLK_HEADER_SIZE

    for i in range(str_count):
        ep = entry_base + i * _TLK_ENTRY_SIZE
        if ep + _TLK_ENTRY_SIZE > len(data):
            break
        (flags,)        = struct.unpack_from("<I",  data, ep)
        sound_raw        = data[ep+4 : ep+20]
        sound_resref     = sound_raw.rstrip(b"\x00").decode("latin-1", errors="replace")
        (vol_var,)      = struct.unpack_from("<I",  data, ep+20)
        (pitch_var,)    = struct.unpack_from("<I",  data, ep+24)
        (s_off,)        = struct.unpack_from("<I",  data, ep+28)
        (s_len,)        = struct.unpack_from("<I",  data, ep+32)
        (sound_len,)    = struct.unpack_from("<f",  data, ep+36)

        text = ""
        if flags & TLK_FLAG_TEXT and s_len > 0:
            abs_off = str_off + s_off
            raw_text = data[abs_off : abs_off + s_len]
            text = raw_text.decode("latin-1", errors="replace")

        entry = TLKEntry(
            flags        = flags,
            sound_resref = sound_resref,
            volume_var   = vol_var,
            pitch_var    = pitch_var,
            offset       = s_off,
            length       = s_len,
            sound_length = sound_len,
            text         = text,
        )
        tlk._entries.append(entry)

    return tlk


def write_tlk(tlk: TLKData) -> bytes:
    """Serialise a :class:`TLKData` to KotOR TLK binary format."""
    # Build string data pool
    strings_buf = bytearray()
    updated_entries: List[TLKEntry] = []

    for entry in tlk._entries:
        encoded = entry.text.encode("latin-1", errors="replace")
        new_entry = TLKEntry(
            flags        = entry.flags,
            sound_resref = entry.sound_resref,
            volume_var   = entry.volume_var,
            pitch_var    = entry.pitch_var,
            offset       = len(strings_buf),
            length       = len(encoded),
            sound_length = entry.sound_length,
            text         = entry.text,
        )
        if encoded:
            new_entry.flags |= TLK_FLAG_TEXT
        strings_buf.extend(encoded)
        updated_entries.append(new_entry)

    n = len(updated_entries)
    str_off = _TLK_HEADER_SIZE + n * _TLK_ENTRY_SIZE

    buf = bytearray()
    # Header
    buf += _TLK_MAGIC
    buf += _TLK_VERSION
    buf += struct.pack("<I", tlk.language_id)
    buf += struct.pack("<I", n)
    buf += struct.pack("<I", str_off)

    # Entry table
    for e in updated_entries:
        # sound_resref: 16 bytes, NUL-padded
        sr = e.sound_resref[:16].encode("latin-1", errors="replace").ljust(16, b"\x00")
        buf += struct.pack("<I", e.flags)
        buf += sr
        buf += struct.pack("<I", e.volume_var)
        buf += struct.pack("<I", e.pitch_var)
        buf += struct.pack("<I", e.offset)
        buf += struct.pack("<I", e.length)
        buf += struct.pack("<f", e.sound_length)

    # String pool
    buf += strings_buf
    return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════
#  NCS — NWScript Compiled Script Disassembler (read-only)
#  Ref: Kotor.NET/Formats/KotorNCS, PyKotor/formats/ncs
# ═══════════════════════════════════════════════════════════════════════════

class NCSOpcode(IntEnum):
    """KotOR NWScript VM opcode values."""
    CPDOWNSP    = 0x01
    RSADD       = 0x02
    CPTOPSP     = 0x03
    CONST       = 0x04
    ACTION      = 0x05
    LOGANDII    = 0x06
    LOGORII     = 0x07
    INCORII     = 0x08
    EXCLORII    = 0x09
    BOOLNOTII   = 0x0A
    EQ          = 0x0B
    NEQ         = 0x0C
    GEQ         = 0x0D
    GT          = 0x0E
    LT          = 0x0F
    LEQ         = 0x10
    SHLEFTII    = 0x11
    SHRIGHTII   = 0x12
    USHRIGHTII  = 0x13
    ADDII       = 0x14
    SUBII       = 0x15
    MULII       = 0x16
    DIVII       = 0x17
    MODII       = 0x18
    NEGII       = 0x19
    COMPII      = 0x1A
    MOVSP       = 0x1B
    STORE_STATEALL = 0x1C
    JMP         = 0x1D
    JSR         = 0x1E
    JZ          = 0x1F
    RETN        = 0x20
    DESTRUCT    = 0x21
    NOTI        = 0x22
    DECISP      = 0x23
    INCISP      = 0x24
    JNZ         = 0x25
    CPDOWNBP    = 0x26
    CPTOPBP     = 0x27
    DECIBP      = 0x28
    INCIBP      = 0x29
    SAVEBP      = 0x2A
    RESTOREBP   = 0x2B
    STORE_STATE = 0x2C
    NOP         = 0x2D
    T           = 0x42
    WRITEARRAY  = 0xFF   # unofficial


NCS_TYPE_NAMES = {
    0x03: "INT",
    0x04: "FLOAT",
    0x05: "STRING",
    0x06: "OBJECT",
    0x10: "EFFECT",
    0x11: "EVENT",
    0x12: "LOCATION",
    0x13: "TALENT",
    0x14: "VECTOR",
    0x15: "ACTION",
}

_NCS_MAGIC       = b"NCS V1.0"
_NCS_HEADER_SIZE = 13  # "NCS V1.0" (8) + magic_byte (1) + total_size (4)


@dataclass
class NCSInstruction:
    """A single decoded NWScript VM instruction."""
    offset:  int
    opcode:  int
    subtype: int
    operands: bytes
    label:   str = ""


class NCSData:
    """Disassembled NWScript compiled bytecode.

    Read-only representation.  Provides a flat list of instructions and
    helper methods for analysis.  Compilation is handled by GhostScripter.
    """

    def __init__(self) -> None:
        self.instructions: List[NCSInstruction] = []
        self.code_size:    int = 0

    def __len__(self) -> int:
        return len(self.instructions)

    def __iter__(self) -> Iterator[NCSInstruction]:
        return iter(self.instructions)

    def __repr__(self) -> str:
        return f"<NCSData instructions={len(self.instructions)} bytes={self.code_size}>"

    def disassembly_text(self) -> str:
        """Return a human-readable disassembly listing."""
        lines: List[str] = []
        for instr in self.instructions:
            try:
                op_name = NCSOpcode(instr.opcode).name
            except ValueError:
                op_name = f"0x{instr.opcode:02X}"
            type_name = NCS_TYPE_NAMES.get(instr.subtype, f"T{instr.subtype:02X}")
            operand_hex = instr.operands.hex() if instr.operands else ""
            line = f"  {instr.offset:06X}  {op_name:<16} {type_name:<8} {operand_hex}"
            if instr.label:
                line = f"{instr.label}:\n" + line
            lines.append(line)
        return "\n".join(lines)


def read_ncs(data: bytes) -> NCSData:
    """Disassemble a KotOR NCS bytecode blob.

    Returns an :class:`NCSData` with a flat instruction list.
    Only the header and top-level instruction stream are decoded;
    full control-flow reconstruction is left to GhostScripter.
    """
    ncs = NCSData()
    if len(data) < 13:
        return ncs
    if data[0:8] != _NCS_MAGIC:
        raise ValueError(f"Not an NCS file (magic={data[0:8]!r})")

    # Byte 8: type byte (0x42 for normal script)
    # Bytes 9-12: code size (int32)
    ncs.code_size = struct.unpack_from(">I", data, 9)[0]  # big-endian per NWN spec

    off = 13
    while off < len(data):
        if off >= len(data):
            break
        opcode  = data[off]; off += 1
        if off >= len(data):
            break
        subtype = data[off]; off += 1

        # Determine operand size from opcode
        operand_bytes = b""
        try:
            op = NCSOpcode(opcode)
        except ValueError:
            op = None

        # ── Operand layout per PyKotor ncs_data.py + Kotor.NET NCS.cs ──────────
        # Zero-operand opcodes (opcode + qualifier byte only):
        #   RETN, SAVEBP, RESTOREBP, NOP/NOP2, RSADD,
        #   all binary arithmetic/comparison/logic ops (0x06-0x1A), NOTI
        _ZERO_OPERAND = {
            NCSOpcode.RETN, NCSOpcode.SAVEBP, NCSOpcode.RESTOREBP,
            NCSOpcode.NOP,
            NCSOpcode.RSADD,
            NCSOpcode.LOGANDII, NCSOpcode.LOGORII, NCSOpcode.INCORII,
            NCSOpcode.EXCLORII, NCSOpcode.BOOLNOTII,
            NCSOpcode.EQ, NCSOpcode.NEQ, NCSOpcode.GEQ, NCSOpcode.GT,
            NCSOpcode.LT, NCSOpcode.LEQ,
            NCSOpcode.SHLEFTII, NCSOpcode.SHRIGHTII, NCSOpcode.USHRIGHTII,
            NCSOpcode.ADDII, NCSOpcode.SUBII, NCSOpcode.MULII,
            NCSOpcode.DIVII, NCSOpcode.MODII, NCSOpcode.NEGII,
            NCSOpcode.COMPII, NCSOpcode.NOTI,
        }

        if op is None:
            # Unknown opcode — store what we have and continue best-effort
            pass
        elif op in _ZERO_OPERAND:
            pass  # qualifier byte already consumed; no further operands
        elif op in (NCSOpcode.JMP, NCSOpcode.JSR, NCSOpcode.JZ, NCSOpcode.JNZ):
            # Jump target: signed int32 offset (big-endian)
            operand_bytes = data[off:off+4]; off += 4
        elif op == NCSOpcode.STORE_STATE:
            # Two int32 values: BP offset + SP offset
            operand_bytes = data[off:off+8]; off += 8
        elif op == NCSOpcode.STORE_STATEALL:
            # Same layout as STORE_STATE
            operand_bytes = data[off:off+8]; off += 8
        elif op == NCSOpcode.CONST:
            if subtype == 0x03:   # INT   — int32
                operand_bytes = data[off:off+4]; off += 4
            elif subtype == 0x04: # FLOAT — float32
                operand_bytes = data[off:off+4]; off += 4
            elif subtype == 0x05: # STRING — uint16 length + chars
                if off + 2 <= len(data):
                    slen = struct.unpack_from(">H", data, off)[0]; off += 2
                    operand_bytes = data[off:off+slen]; off += slen
            elif subtype == 0x06: # OBJECT — int32 object id
                operand_bytes = data[off:off+4]; off += 4
        elif op == NCSOpcode.MOVSP:
            # int32 stack offset
            operand_bytes = data[off:off+4]; off += 4
        elif op in (NCSOpcode.CPDOWNSP, NCSOpcode.CPTOPSP,
                    NCSOpcode.CPDOWNBP, NCSOpcode.CPTOPBP):
            # int32 offset + uint16 size
            operand_bytes = data[off:off+6]; off += 6
        elif op == NCSOpcode.ACTION:
            # uint16 routine id + uint8 arg count
            operand_bytes = data[off:off+3]; off += 3
        elif op in (NCSOpcode.DECISP, NCSOpcode.INCISP,
                    NCSOpcode.DECIBP, NCSOpcode.INCIBP):
            # int32 stack/bp offset
            operand_bytes = data[off:off+4]; off += 4
        elif op == NCSOpcode.DESTRUCT:
            # uint16 size_to_remove + uint16 offset_to_skip + uint16 size_to_skip
            operand_bytes = data[off:off+6]; off += 6
        elif op == NCSOpcode.T:
            # Special header type-byte — no additional operands
            pass
        else:
            # Truly unknown — skip this instruction and continue best-effort
            pass

        ncs.instructions.append(NCSInstruction(
            offset   = off - 2 - len(operand_bytes),
            opcode   = opcode,
            subtype  = subtype,
            operands = operand_bytes,
        ))

    return ncs



# ═══════════════════════════════════════════════════════════════════════════
#  LTR — Letter (Name Generator) Format
#  Binary: "LTR V1.0" header + Markov chain probability tables
#  Ref: PyKotor/formats/ltr, nwn-misc/nwnltr.c
# ═══════════════════════════════════════════════════════════════════════════

_LTR_MAGIC = b"LTR V1.0"
_LTR_LETTER_COUNT = 28   # KotOR uses 28 characters (a-z + '-' + '\'')

@dataclass
class LTRData:
    """Markov chain name-generator data from a KotOR .ltr file.

    Three probability tables:
      single  — shape (letter_count, 3):              start/middle/end prob for each char
      double  — shape (letter_count, letter_count, 3): start/middle/end given 1 prev char
      triple  — shape (letter_count, letter_count, letter_count, 3): given 2 prev chars
    All entries are 32-bit floats in the range [0.0, 1.0].
    """
    letter_count: int = _LTR_LETTER_COUNT
    # Flat storage: [letter_count, 3] → 84 floats
    single: List[float] = field(default_factory=lambda: [0.0] * (_LTR_LETTER_COUNT * 3))
    # Flat storage: [letter_count, letter_count, 3] → 2352 floats
    double: List[float] = field(default_factory=lambda: [0.0] * (_LTR_LETTER_COUNT ** 2 * 3))
    # Flat storage: [letter_count^3 * 3] → 65856 floats
    triple: List[float] = field(default_factory=lambda: [0.0] * (_LTR_LETTER_COUNT ** 3 * 3))

    def __repr__(self) -> str:
        return f"LTRData(letter_count={self.letter_count}, single[{len(self.single)}], double[{len(self.double)}], triple[{len(self.triple)}])"


def read_ltr(data: bytes) -> LTRData:
    """Parse a KotOR binary LTR blob and return an :class:`LTRData`.

    Binary layout (LTR V1.0):
      8 bytes  — "LTR V1.0" magic
      1 byte   — letter_count (uint8, typically 28)
      letter_count * 3 * 4 bytes   — single probabilities (float32 × count × 3)
      letter_count² * 3 * 4 bytes  — double probabilities
      letter_count³ * 3 * 4 bytes  — triple probabilities
    """
    if data[:8] != _LTR_MAGIC:
        raise ValueError(f"Not an LTR file (magic={data[:8]!r})")

    pos = 8
    letter_count: int = data[pos]
    pos += 1

    n = letter_count
    ltr = LTRData(letter_count=n)

    # Single letters: n × 3 floats
    count = n * 3
    ltr.single = list(struct.unpack_from(f"<{count}f", data, pos))
    pos += count * 4

    # Double letters: n² × 3 floats
    count = n * n * 3
    ltr.double = list(struct.unpack_from(f"<{count}f", data, pos))
    pos += count * 4

    # Triple letters: n³ × 3 floats
    count = n * n * n * 3
    ltr.triple = list(struct.unpack_from(f"<{count}f", data, pos))

    return ltr


def write_ltr(ltr: LTRData) -> bytes:
    """Serialise an :class:`LTRData` to KotOR LTR binary format."""
    n = ltr.letter_count
    buf = bytearray()
    buf += _LTR_MAGIC
    buf += struct.pack("<B", n)

    single_count = n * 3
    double_count = n * n * 3
    triple_count = n * n * n * 3

    # Pad/truncate to match expected sizes
    s = ltr.single[:single_count] + [0.0] * max(0, single_count - len(ltr.single))
    d = ltr.double[:double_count] + [0.0] * max(0, double_count - len(ltr.double))
    t = ltr.triple[:triple_count] + [0.0] * max(0, triple_count - len(ltr.triple))

    buf += struct.pack(f"<{single_count}f", *s)
    buf += struct.pack(f"<{double_count}f", *d)
    buf += struct.pack(f"<{triple_count}f", *t)

    return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════
#  NCS Write — compile a flat instruction list back to binary NCS
#  (lightweight; does not replace a full NWScript compiler)
# ═══════════════════════════════════════════════════════════════════════════

def write_ncs(ncs: NCSData) -> bytes:
    """Serialise an :class:`NCSData` back to KotOR NCS binary format.

    This is a "re-assembler" — it is useful for patching disassembled scripts
    (e.g., changing a constant value or NOP-ing out an instruction) rather than
    for compiling NWScript source.  The opcode byte + qualifier byte + raw
    operand bytes from each instruction are written as-is.
    """
    # Build instruction payload first (so we know the total_size)
    code = bytearray()
    for instr in ncs:
        code += bytes([instr.opcode & 0xFF])
        code += bytes([instr.subtype & 0xFF])
        if instr.operands:
            code += instr.operands

    # NCS header: "NCS V1.0" (8 bytes) + magic byte 0x42 + total_size (uint32 BE)
    total_size = _NCS_HEADER_SIZE + len(code)   # includes the 13-byte header
    buf = bytearray()
    buf += _NCS_MAGIC                           # b"NCS V1.0"
    buf += bytes([0x42])                        # magic byte
    buf += struct.pack(">I", total_size)        # big-endian uint32
    buf += code
    return bytes(buf)




def detect_and_read(data: bytes, ext_hint: str = "") -> object:
    """Auto-detect format from *data* magic / *ext_hint* and return parsed object.

    Returns one of:
      SSFData | LIPData | NCSData | TLKData | VISData | TXIData
    or raises ValueError for unknown formats.
    """
    ext = ext_hint.lstrip(".").lower()

    if data[:4] == b"SSF ":
        return read_ssf(data)
    if data[:4] == b"LIP ":
        return read_lip(data)
    if data[:8] == _NCS_MAGIC:
        return read_ncs(data)
    if data[:4] == _TLK_MAGIC:
        return read_tlk(data)

    # ASCII formats
    if ext == "vis":
        return read_vis(data)
    if ext == "txi":
        return read_txi(data)

    if ext == "2da" or data[:4] == b"2DA ":
        return TwoDAData.from_bytes(data)
    if ext == "ltr" or data[:4] == b"LTR ":
        return read_ltr(data)

    raise ValueError(f"Cannot detect format (ext={ext_hint!r}, magic={data[:4]!r})")
