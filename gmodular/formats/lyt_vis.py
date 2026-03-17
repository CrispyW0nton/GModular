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
    rooms:      List[RoomPlacement] = field(default_factory=list)
    door_hooks: List[DoorHookEntry] = field(default_factory=list)
    tracks:     List[str]           = field(default_factory=list)
    obstacles:  List[str]           = field(default_factory=list)
    source:     str                 = ""   # path or resref it was loaded from

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


@dataclass
class VisibilityData:
    """
    Parsed contents of a .vis file.

    Maps each room ResRef to the set of rooms visible from it.
    """
    visibility: Dict[str, List[str]] = field(default_factory=dict)
    source:     str = ""

    def visible_from(self, room: str) -> List[str]:
        """Return list of rooms visible from ``room``."""
        return self.visibility.get(room.lower(), [])

    def are_visible(self, room_a: str, room_b: str) -> bool:
        """Return True if room_b is visible from room_a (or vice versa)."""
        a, b = room_a.lower(), room_b.lower()
        return b in self.visibility.get(a, []) or a in self.visibility.get(b, [])


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
                        layout.tracks.append(row)

            # ── obstaclecount <N> ────────────────────────────────────────────
            elif keyword == 'obstaclecount':
                count = _safe_int(parts, 1, 0)
                for _ in range(count):
                    if i >= n:
                        break
                    row = lines[i].strip(); i += 1
                    if row and not row.startswith('#'):
                        layout.obstacles.append(row)

            # Ignore unknown keywords
            else:
                log.debug(f"LYT: unknown keyword {keyword!r} at line {i}")

        log.debug(f"LYT '{source}': {len(layout.rooms)} rooms, "
                  f"{len(layout.door_hooks)} door hooks")
        return layout

    @staticmethod
    def _parse_room_line(row: str) -> Optional[RoomPlacement]:
        """Parse a line like: <resref> <x> <y> <z>"""
        parts = row.split()
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

        current_room: Optional[str] = None

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith('#'):
                # A blank line ends the current room's visible list
                # but we continue — some .vis files have no blank separators
                continue

            parts = line.lower().split()
            # If there is exactly one token and it looks like a room resref,
            # it could be the start of a new room block.
            # Heuristic: if line has one word and it's a simple identifier,
            # treat it as a new room declaration.
            if len(parts) == 1 and _is_resref(parts[0]):
                current_room = parts[0]
                if current_room not in vis.visibility:
                    vis.visibility[current_room] = []
            elif current_room is not None:
                # These are the rooms visible from current_room
                for p in parts:
                    rlo = p.lower()
                    if rlo not in vis.visibility[current_room]:
                        vis.visibility[current_room].append(rlo)
            else:
                # Could be a room name on the first line without prior context
                if parts:
                    current_room = parts[0]
                    vis.visibility[current_room] = []
                    for p in parts[1:]:
                        vis.visibility[current_room].append(p.lower())

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
    Writes a LayoutData object to a .lyt file string.

    Usage::
        layout = LayoutData()
        layout.rooms.append(RoomPlacement("danm13aa", 0.0, 0.0, 0.0))
        text = LYTWriter.to_string(layout)
        with open("danm13.lyt", "w") as f:
            f.write(text)
    """

    @staticmethod
    def to_string(layout: LayoutData) -> str:
        lines = []

        # Rooms
        lines.append(f"roomcount {len(layout.rooms)}")
        for r in layout.rooms:
            lines.append(f"  {r.resref} {r.x:.4f} {r.y:.4f} {r.z:.4f}")
        lines.append("")

        # Door hooks
        lines.append(f"doorhookcount {len(layout.door_hooks)}")
        for dh in layout.door_hooks:
            lines.append(
                f"  {dh.name} {dh.room} "
                f"{dh.x:.4f} {dh.y:.4f} {dh.z:.4f} "
                f"{dh.qx:.4f} {dh.qy:.4f} {dh.qz:.4f} {dh.qw:.4f}"
            )
        lines.append("")

        # Tracks (usually empty)
        lines.append(f"trackcount {len(layout.tracks)}")
        for t in layout.tracks:
            lines.append(f"  {t}")
        lines.append("")

        # Obstacles (usually empty)
        lines.append(f"obstaclecount {len(layout.obstacles)}")
        for o in layout.obstacles:
            lines.append(f"  {o}")
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def to_file(layout: LayoutData, path: str):
        text = LYTWriter.to_string(layout)
        with open(path, 'w', encoding='ascii') as f:
            f.write(text)
