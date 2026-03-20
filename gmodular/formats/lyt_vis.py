"""
GModular — KotOR LYT / VIS Room Layout Parser
==============================================
Parses KotOR module .lyt (layout) and .vis (visibility) plain-text files.

.lyt Format:
  Plain ASCII text containing room positions, door hooks and track data.

  Layout file sections (each section starts with a keyword + count line):
    roomcount <N>
    <resref> <x> <y> <z>    (N lines)

    trackcount <N>
    ...

    obstaclecount <N>
    ...

    doorhookcount <N>
    <name> <room> <x> <y> <z> <qx> <qy> <qz> <qw>

  Empty lines and lines starting with '#' are ignored.
  Keywords are case-insensitive.

.vis Format:
  Lists which rooms are visible from each room.

    <roomA>
    <roomB1> <roomB2> ...

  Each room is followed by one or more lines listing visible rooms.
  The first line after a room definition lists rooms that can see the named room.

Reference:
  https://kotor-modding.fandom.com/wiki/LYT_Format
  xoreos src/engines/kotor/module.cpp
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RoomPlacement:
    """A room placed in the module layout."""
    resref:  str    # MDL ResRef (lowercase, no extension)
    x:       float = 0.0
    y:       float = 0.0
    z:       float = 0.0

    @property
    def position(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def __repr__(self) -> str:
        return f"RoomPlacement({self.resref!r}, {self.x:.2f},{self.y:.2f},{self.z:.2f})"


@dataclass
class TrackEntry:
    """A track entry in the .lyt file (animated geometry path)."""
    model: str          # MDL ResRef
    x:     float = 0.0
    y:     float = 0.0
    z:     float = 0.0

    @property
    def position(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def __repr__(self) -> str:
        return f"TrackEntry({self.model!r}, {self.x:.2f},{self.y:.2f},{self.z:.2f})"


@dataclass
class ObstacleEntry:
    """An obstacle entry in the .lyt file (static blocking geometry)."""
    model: str          # MDL ResRef
    x:     float = 0.0
    y:     float = 0.0
    z:     float = 0.0

    @property
    def position(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def __repr__(self) -> str:
        return f"ObstacleEntry({self.model!r}, {self.x:.2f},{self.y:.2f},{self.z:.2f})"


@dataclass
class DoorHookEntry:
    """A door hook entry from the .lyt file."""
    name:     str
    room:     str                  # room resref
    x:        float = 0.0
    y:        float = 0.0
    z:        float = 0.0
    qx:       float = 0.0
    qy:       float = 0.0
    qz:       float = 0.0
    qw:       float = 1.0

    @property
    def position(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def rotation(self) -> Tuple[float, float, float, float]:
        return (self.qx, self.qy, self.qz, self.qw)


@dataclass
class LayoutData:
    """
    Parsed contents of a .lyt file.

    Attributes:
        rooms:      List of room placements (resref + world position).
        door_hooks: List of door hook connection points.
        tracks:     List of track entries (usually empty in custom modules).
        obstacles:  List of obstacle entries (usually empty).
    """
    rooms:      List[RoomPlacement]  = field(default_factory=list)
    door_hooks: List[DoorHookEntry]  = field(default_factory=list)
    tracks:     List[TrackEntry]     = field(default_factory=list)
    obstacles:  List[ObstacleEntry]  = field(default_factory=list)
    source:     str                 = ""   # path or resref it was loaded from

    # ── Convenience factory methods (delegate to LYTParser) ──────────────────

    @classmethod
    def from_string(cls, text: str, source: str = "") -> "LayoutData":
        """Parse a .lyt file from a string. Delegates to LYTParser."""
        return LYTParser.from_string(text, source)

    @classmethod
    def from_bytes(cls, data: bytes, source: str = "") -> "LayoutData":
        """Parse a .lyt file from bytes. Delegates to LYTParser."""
        return LYTParser.from_bytes(data, source)

    @classmethod
    def from_file(cls, path: str) -> "LayoutData":
        """Read and parse a .lyt file. Delegates to LYTParser."""
        return LYTParser.from_file(path)

    @property
    def room_count(self) -> int:
        return len(self.rooms)

    def get_room(self, resref: str) -> Optional[RoomPlacement]:
        """Find a room by ResRef (case-insensitive)."""
        rlo = resref.lower()
        for r in self.rooms:
            if r.resref.lower() == rlo:
                return r
        return None

    def get_door_hooks(self, room: str) -> List[DoorHookEntry]:
        """Return all door hooks belonging to a room."""
        rlo = room.lower()
        return [dh for dh in self.door_hooks if dh.room.lower() == rlo]

    # ── Convenience factory methods (delegate to LYTParser) ──────────────────

    @classmethod
    def from_string(cls, text: str, source: str = "") -> "LayoutData":
        """Parse a .lyt file from a string."""
        return LYTParser.from_string(text, source)

    @classmethod
    def from_bytes(cls, data: bytes, source: str = "") -> "LayoutData":
        """Parse a .lyt file from bytes."""
        return LYTParser.from_bytes(data, source)

    @classmethod
    def from_file(cls, path: str) -> "LayoutData":
        """Parse a .lyt file from disk."""
        return LYTParser.from_file(path)


@dataclass
class VisibilityData:
    """
    Parsed contents of a .vis file.

    Maps each room ResRef to the set of rooms visible from it.
    """
    visibility: Dict[str, List[str]] = field(default_factory=dict)
    source:     str = ""

    # ── Convenience factory methods (delegate to VISParser) ──────────────────

    @classmethod
    def from_string(cls, text: str, source: str = "") -> "VisibilityData":
        """Parse a .vis file from a string. Delegates to VISParser."""
        return VISParser.from_string(text, source)

    @classmethod
    def from_bytes(cls, data: bytes, source: str = "") -> "VisibilityData":
        """Parse a .vis file from bytes. Delegates to VISParser."""
        return VISParser.from_bytes(data, source)

    @classmethod
    def from_file(cls, path: str) -> "VisibilityData":
        """Read and parse a .vis file. Delegates to VISParser."""
        return VISParser.from_file(path)

    def visible_from(self, room: str) -> List[str]:
        """Return list of rooms visible from ``room``."""
        return self.visibility.get(room.lower(), [])

    def are_visible(self, room_a: str, room_b: str) -> bool:
        """Return True if room_b is visible from room_a (or vice versa)."""
        a, b = room_a.lower(), room_b.lower()
        return b in self.visibility.get(a, []) or a in self.visibility.get(b, [])

    # ── Convenience factory methods (delegate to VISParser) ──────────────────

    @classmethod
    def from_string(cls, text: str, source: str = "") -> "VisibilityData":
        """Parse a .vis file from a string."""
        return VISParser.from_string(text, source)

    @classmethod
    def from_bytes(cls, data: bytes, source: str = "") -> "VisibilityData":
        """Parse a .vis file from bytes."""
        return VISParser.from_bytes(data, source)

    @classmethod
    def from_file(cls, path: str) -> "VisibilityData":
        """Parse a .vis file from disk."""
        return VISParser.from_file(path)


# ─────────────────────────────────────────────────────────────────────────────
#  LYT Parser
# ─────────────────────────────────────────────────────────────────────────────

class LYTParser:
    """
    Parses KotOR .lyt files into LayoutData objects.

    Usage::
        layout = LYTParser.from_string(lyt_text)
        layout = LYTParser.from_bytes(lyt_bytes)
        layout = LYTParser.from_file("module/danm13.lyt")

    The parser is lenient: malformed lines are skipped with debug logging.
    """

    @staticmethod
    def from_string(text: str, source: str = "") -> LayoutData:
        """Parse a .lyt file from a string."""
        return LYTParser._parse(text.splitlines(), source)

    @staticmethod
    def from_bytes(data: bytes, source: str = "") -> LayoutData:
        """Parse a .lyt file from bytes."""
        try:
            text = data.decode('ascii', errors='replace')
        except Exception as e:
            log.debug(f"LYT decode error: {e}")
            text = ""
        return LYTParser.from_string(text, source)

    @staticmethod
    def from_file(path: str) -> LayoutData:
        """Read and parse a .lyt file."""
        try:
            with open(path, 'r', encoding='ascii', errors='replace') as f:
                text = f.read()
        except OSError as e:
            log.error(f"LYT: cannot read {path!r}: {e}")
            return LayoutData(source=path)
        return LYTParser.from_string(text, source=path)

    @staticmethod
    def _parse(lines: List[str], source: str) -> LayoutData:
        layout = LayoutData(source=source)

        i = 0
        n = len(lines)

        while i < n:
            raw = lines[i].strip()
            i += 1

            # Skip blank lines and comments
            if not raw or raw.startswith('#'):
                continue

            parts = raw.lower().split()
            if not parts:
                continue

            keyword = parts[0]

            # ── roomcount <N> ────────────────────────────────────────────────
            if keyword == 'roomcount':
                count = _safe_int(parts, 1, 0)
                for _ in range(count):
                    if i >= n:
                        break
                    row = lines[i].strip(); i += 1
                    if not row or row.startswith('#'):
                        continue
                    rp = LYTParser._parse_room_line(row)
                    if rp:
                        layout.rooms.append(rp)

            # ── doorhookcount <N> ─────────────────────────────────────────────
            elif keyword == 'doorhookcount':
                count = _safe_int(parts, 1, 0)
                for _ in range(count):
                    if i >= n:
                        break
                    row = lines[i].strip(); i += 1
                    if not row or row.startswith('#'):
                        continue
                    dh = LYTParser._parse_doorhook_line(row)
                    if dh:
                        layout.door_hooks.append(dh)

            # ── trackcount <N> ───────────────────────────────────────────────
            elif keyword == 'trackcount':
                count = _safe_int(parts, 1, 0)
                for _ in range(count):
                    if i >= n:
                        break
                    row = lines[i].strip(); i += 1
                    if row and not row.startswith('#'):
                        te = LYTParser._parse_track_obstacle_line(row, TrackEntry)
                        if te:
                            layout.tracks.append(te)

            # ── obstaclecount <N> ────────────────────────────────────────────
            elif keyword == 'obstaclecount':
                count = _safe_int(parts, 1, 0)
                for _ in range(count):
                    if i >= n:
                        break
                    row = lines[i].strip(); i += 1
                    if row and not row.startswith('#'):
                        oe = LYTParser._parse_track_obstacle_line(row, ObstacleEntry)
                        if oe:
                            layout.obstacles.append(oe)

            # Ignore unknown keywords
            else:
                log.debug(f"LYT: unknown keyword {keyword!r} at line {i}")

        log.debug(f"LYT '{source}': {len(layout.rooms)} rooms, "
                  f"{len(layout.door_hooks)} door hooks")
        return layout

    @staticmethod
    def _parse_room_line(row: str) -> Optional[RoomPlacement]:
        """
        Parse a room line in any of these formats:

          Standard KotOR:    ``<resref> <x> <y> <z>``
          GModular indexed:  ``room <index> <resref> <x> <y> <z>``
          GModular keyed:    ``room <resref> <x> <y> <z>``
        """
        parts = row.split()
        # Detect "room <index> <resref> <x> <y> <z>" (6 tokens, parts[1] is int)
        if len(parts) >= 6 and parts[0].lower() == 'room':
            try:
                int(parts[1])          # confirm parts[1] is the index
                return RoomPlacement(
                    resref=parts[2].lower(),
                    x=float(parts[3]),
                    y=float(parts[4]),
                    z=float(parts[5]),
                )
            except (ValueError, IndexError):
                pass
        # Detect "room <resref> <x> <y> <z>" (5 tokens, no index)
        if len(parts) >= 5 and parts[0].lower() == 'room':
            try:
                return RoomPlacement(
                    resref=parts[1].lower(),
                    x=float(parts[2]),
                    y=float(parts[3]),
                    z=float(parts[4]),
                )
            except (ValueError, IndexError):
                pass
        # Standard KotOR: <resref> <x> <y> <z>
        if len(parts) < 4:
            log.debug(f"LYT: malformed room line: {row!r}")
            return None
        try:
            return RoomPlacement(
                resref=parts[0].lower(),
                x=float(parts[1]),
                y=float(parts[2]),
                z=float(parts[3]),
            )
        except ValueError as e:
            log.debug(f"LYT: room parse error {row!r}: {e}")
            return None

    @staticmethod
    def _parse_track_obstacle_line(row: str, cls):
        """Parse a track or obstacle line: <model> <x> <y> <z>"""
        parts = row.split()
        if len(parts) < 4:
            log.debug(f"LYT: malformed track/obstacle line: {row!r}")
            return None
        try:
            return cls(
                model=parts[0].lower(),
                x=float(parts[1]),
                y=float(parts[2]),
                z=float(parts[3]),
            )
        except ValueError as e:
            log.debug(f"LYT: track/obstacle parse error {row!r}: {e}")
            return None

    @staticmethod
    def _parse_doorhook_line(row: str) -> Optional[DoorHookEntry]:
        """Parse a line like: <name> <room> <x> <y> <z> [<qx> <qy> <qz> <qw>]"""
        parts = row.split()
        if len(parts) < 5:
            log.debug(f"LYT: malformed doorhook line: {row!r}")
            return None
        try:
            dh = DoorHookEntry(
                name=parts[0],
                room=parts[1].lower(),
                x=float(parts[2]),
                y=float(parts[3]),
                z=float(parts[4]),
            )
            if len(parts) >= 9:
                dh.qx = float(parts[5])
                dh.qy = float(parts[6])
                dh.qz = float(parts[7])
                dh.qw = float(parts[8])
            return dh
        except ValueError as e:
            log.debug(f"LYT: doorhook parse error {row!r}: {e}")
            return None


# ─────────────────────────────────────────────────────────────────────────────
#  VIS Parser
# ─────────────────────────────────────────────────────────────────────────────

class VISParser:
    """
    Parses KotOR .vis (visibility) files.

    .vis format:
      Each block starts with a room resref on its own line,
      followed by one or more lines listing rooms visible from it.
      Blocks are separated by blank lines.

    Usage::
        vis = VISParser.from_string(vis_text)
        vis = VISParser.from_file("module/danm13.vis")
        rooms = vis.visible_from("danm13_room1")
    """

    @staticmethod
    def from_string(text: str, source: str = "") -> VisibilityData:
        return VISParser._parse(text.splitlines(), source)

    @staticmethod
    def from_bytes(data: bytes, source: str = "") -> VisibilityData:
        try:
            text = data.decode('ascii', errors='replace')
        except Exception:
            text = ""
        return VISParser.from_string(text, source)

    @staticmethod
    def from_file(path: str) -> VisibilityData:
        try:
            with open(path, 'r', encoding='ascii', errors='replace') as f:
                text = f.read()
        except OSError as e:
            log.error(f"VIS: cannot read {path!r}: {e}")
            return VisibilityData(source=path)
        return VISParser.from_string(text, source=path)

    @staticmethod
    def _parse(lines: List[str], source: str) -> VisibilityData:
        vis = VisibilityData(source=source)

        # KotOR .vis format: alternating pairs of lines
        #   Line 1: room resref
        #   Line 2: space-separated list of rooms visible from Line 1's room
        # Each room may see multiple others listed on one or more continuation lines.
        # Blank lines separate room blocks.

        # First pass: collect non-empty, non-comment lines
        content_lines: List[str] = []
        for raw in lines:
            line = raw.strip()
            if line and not line.startswith('#'):
                content_lines.append(line.lower())

        # Second pass: parse alternating room/visibility pairs
        # Strategy: each room block starts with a single resref token.
        # The following line(s) until the next single-token room are visibility lists.
        i = 0
        n = len(content_lines)
        while i < n:
            line = content_lines[i]
            parts = line.split()

            if len(parts) == 1 and _is_resref(parts[0]):
                # This is a room declaration
                current_room = parts[0]
                if current_room not in vis.visibility:
                    vis.visibility[current_room] = []
                i += 1
                # Next line(s) are visibility lists for this room
                while i < n:
                    next_line = content_lines[i]
                    next_parts = next_line.split()
                    # If next line is a single resref, it could be:
                    # (a) a new room declaration, OR
                    # (b) a single-room visibility list
                    # KotOR vis format: visibility list follows immediately,
                    # so we treat the very next line as a visibility list
                    # regardless of token count.
                    if len(next_parts) == 1 and _is_resref(next_parts[0]):
                        # Check if the line after THIS one is also a single resref
                        # (indicating the next room starts here) or a multi-token list.
                        # Heuristic: if i+1 < n and next line is single token,
                        # peek ahead – if line i+1 looks like a vis list (multiple tokens
                        # or doesn't start a room pattern), treat i as vis list.
                        # Simplest correct approach: treat the first line after a room
                        # as its visibility list unconditionally.
                        for p in next_parts:
                            rlo = p.lower()
                            if rlo not in vis.visibility[current_room]:
                                vis.visibility[current_room].append(rlo)
                        i += 1
                        break   # one visibility line per room in standard format
                    elif len(next_parts) > 1:
                        # Multi-token line: definitely a visibility list
                        for p in next_parts:
                            rlo = p.lower()
                            if rlo not in vis.visibility[current_room]:
                                vis.visibility[current_room].append(rlo)
                        i += 1
                        # Keep reading continuation lines for this room
                    else:
                        # Empty or unrecognised
                        i += 1
                        break
            else:
                # Multi-token line without a preceding room (shouldn't happen in well-formed vis)
                # Try treating as a new room with inline visibility
                if parts:
                    current_room = parts[0]
                    if current_room not in vis.visibility:
                        vis.visibility[current_room] = []
                    for p in parts[1:]:
                        vis.visibility[current_room].append(p.lower())
                i += 1

        log.debug(f"VIS '{source}': {len(vis.visibility)} rooms")
        return vis


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_int(parts: List[str], idx: int, default: int = 0) -> int:
    try:
        return int(parts[idx])
    except (IndexError, ValueError):
        return default


def _is_resref(s: str) -> bool:
    """Return True if s looks like a valid ResRef (alphanumeric + _ + no spaces)."""
    if not s or len(s) > 32:
        return False
    return all(c.isalnum() or c in '_-.' for c in s)


# ─────────────────────────────────────────────────────────────────────────────
#  LYT writer (create .lyt from LayoutData)
# ─────────────────────────────────────────────────────────────────────────────

class LYTWriter:
    """
    Writes a LayoutData object to a canonical KotOR .lyt file string.

    The canonical format (LoadLayout @ 0x005de900 in swkotor.exe) uses
    ``beginlayout`` / ``donelayout`` bookmarks and tab-indented sections.

    Follows the same write order as PyKotor's LYTAsciiWriter:
      beginlayout
        roomcount N
          model x y z  (one per room)
        trackcount N
          model x y z
        obstaclecount N
          model x y z
        doorhookcount N
          room door 0 x y z qx qy qz qw
      donelayout

    Usage::
        layout = LayoutData()
        layout.rooms.append(RoomPlacement("danm13aa", 0.0, 0.0, 0.0))
        text = LYTWriter.to_string(layout)
        with open("danm13.lyt", "w") as f:
            f.write(text)
    """

    _SEP = "\r\n"   # KotOR uses CRLF line endings
    _I1  = "   "    # one level of indentation (3 spaces, matching PyKotor)
    _I2  = "      " # two levels

    @staticmethod
    def to_string(layout: LayoutData) -> str:
        sep = LYTWriter._SEP
        i1  = LYTWriter._I1
        i2  = LYTWriter._I2
        lines = [f"beginlayout{sep}"]

        # Rooms
        lines.append(f"{i1}roomcount {len(layout.rooms)}{sep}")
        for r in layout.rooms:
            lines.append(f"{i2}{r.resref} {r.x:.6f} {r.y:.6f} {r.z:.6f}{sep}")

        # Tracks
        lines.append(f"{i1}trackcount {len(layout.tracks)}{sep}")
        for t in layout.tracks:
            model = getattr(t, 'model', getattr(t, 'resref', str(t)))
            x = getattr(t, 'x', 0.0); y = getattr(t, 'y', 0.0); z = getattr(t, 'z', 0.0)
            lines.append(f"{i2}{model} {x:.6f} {y:.6f} {z:.6f}{sep}")

        # Obstacles
        lines.append(f"{i1}obstaclecount {len(layout.obstacles)}{sep}")
        for o in layout.obstacles:
            model = getattr(o, 'model', getattr(o, 'resref', str(o)))
            x = getattr(o, 'x', 0.0); y = getattr(o, 'y', 0.0); z = getattr(o, 'z', 0.0)
            lines.append(f"{i2}{model} {x:.6f} {y:.6f} {z:.6f}{sep}")

        # Door hooks — canonical format: room door 0 x y z qx qy qz qw
        lines.append(f"{i1}doorhookcount {len(layout.door_hooks)}{sep}")
        for dh in layout.door_hooks:
            lines.append(
                f"{i2}{dh.room} {dh.name} 0 "
                f"{dh.x:.6f} {dh.y:.6f} {dh.z:.6f} "
                f"{dh.qx:.6f} {dh.qy:.6f} {dh.qz:.6f} {dh.qw:.6f}{sep}"
            )

        lines.append(f"donelayout{sep}")
        return "".join(lines)

    @staticmethod
    def to_file(layout: LayoutData, path: str):
        text = LYTWriter.to_string(layout)
        with open(path, 'w', encoding='ascii', newline='') as f:
            f.write(text)
