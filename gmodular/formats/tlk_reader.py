"""
GModular — KotOR TLK (Talk Table) Reader/Writer
================================================
Reads and writes KotOR dialog.tlk (V3.0) files.

Binary Format (from xoreos src/aurora/talktable_tlk.cpp):
  Header (20 bytes):
    file_type       [4]  "TLK "
    file_version    [4]  "V3.0"
    language_id     u32  0=English, 1=French, 2=German, 3=Italian,
                         4=Spanish, 5=Polish, 128=Korean, 129=ChineseTrad,
                         130=ChineseSimp, 131=Japanese
    string_count    u32
    string_entries_offset u32

  String Data Table (40 bytes per entry, at offset 20):
    flags           u32  bit0=text present, bit1=sound present,
                         bit2=sound_length present
    sound_resref   [16]  NUL-padded ASCII ResRef
    volume_variance u32  (unused)
    pitch_variance  u32  (unused)
    text_offset     u32  offset from string_entries_offset
    text_length     u32  bytes in text
    sound_length    f32  seconds

  String data: variable-length null-terminated UTF-8 strings.

References:
  PyKotor Libraries/PyKotor/src/pykotor/resource/formats/tlk/io_tlk.py
  xoreos src/aurora/talktable_tlk.cpp
  KotOR modding wiki: https://kotor-modding.fandom.com/wiki/TLK_Format
"""
from __future__ import annotations

import struct
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterator

log = logging.getLogger(__name__)

# ── TLK constants ─────────────────────────────────────────────────────────────
TLK_MAGIC   = b"TLK "
TLK_VERSION = b"V3.0"
TLK_HEADER_SIZE  = 20
TLK_ENTRY_SIZE   = 40   # bytes per string data table entry

# Language IDs
LANG_ENGLISH          = 0
LANG_FRENCH           = 1
LANG_GERMAN           = 2
LANG_ITALIAN          = 3
LANG_SPANISH          = 4
LANG_POLISH           = 5
LANG_KOREAN           = 128
LANG_CHINESE_TRAD     = 129
LANG_CHINESE_SIMP     = 130
LANG_JAPANESE         = 131

# Entry flag bits
TLK_FLAG_TEXT_PRESENT   = 0x0001
TLK_FLAG_SOUND_PRESENT  = 0x0002
TLK_FLAG_SOUND_LENGTH   = 0x0004

# Sentinel StrRef meaning "no string"
TLK_INVALID_STRREF = 0xFFFFFFFF


# ─────────────────────────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TLKEntry:
    """A single entry in a talk table."""
    text:                str   = ""
    sound_resref:        str   = ""        # ResRef for associated VO audio (≤16 chars)
    sound_length:        float = 0.0       # duration in seconds
    text_present:        bool  = False
    sound_present:       bool  = False
    soundlength_present: bool  = False

    @property
    def flags(self) -> int:
        f = 0
        if self.text_present:        f |= TLK_FLAG_TEXT_PRESENT
        if self.sound_present:       f |= TLK_FLAG_SOUND_PRESENT
        if self.soundlength_present: f |= TLK_FLAG_SOUND_LENGTH
        return f

    def __repr__(self) -> str:
        return (f"TLKEntry(text={self.text!r}, sound={self.sound_resref!r}, "
                f"len={self.sound_length:.2f})")


@dataclass
class TLKFile:
    """
    In-memory KotOR Talk Table.

    StrRef numbers are 0-based array indices. StrRef 0xFFFFFFFF / -1 means
    "no string" and is handled specially.

    Usage::
        tlk = TLKFile()
        idx = tlk.add("Hello there!")
        text = tlk.get_text(idx)

        # Read from file
        tlk = TLKReader.from_file("dialog.tlk")
        text = tlk.get_text(1234)

        # Write to bytes
        data = TLKWriter(tlk).to_bytes()
    """
    language_id: int          = LANG_ENGLISH
    entries:     List[TLKEntry] = field(default_factory=list)

    # ── Lookup ──────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Tuple[int, TLKEntry]]:
        return enumerate(self.entries).__iter__()

    def __getitem__(self, strref: int) -> TLKEntry:
        return self.entries[strref]

    def get_text(self, strref: int) -> Optional[str]:
        """Return the text for a StrRef, or None if invalid / out of range."""
        if strref == TLK_INVALID_STRREF or strref < 0:
            return None
        if strref >= len(self.entries):
            return None
        return self.entries[strref].text or None

    def get_sound(self, strref: int) -> Optional[str]:
        """Return the sound ResRef for a StrRef, or None."""
        if strref == TLK_INVALID_STRREF or strref < 0:
            return None
        if strref >= len(self.entries):
            return None
        e = self.entries[strref]
        return e.sound_resref if e.sound_present and e.sound_resref else None

    # ── Mutation ────────────────────────────────────────────────────────────

    def add(self, text: str, sound_resref: str = "",
            sound_length: float = 0.0) -> int:
        """Append a new entry. Returns the StrRef index."""
        e = TLKEntry(
            text=text,
            sound_resref=sound_resref[:16],
            sound_length=sound_length,
            text_present=bool(text),
            sound_present=bool(sound_resref),
            soundlength_present=sound_length > 0.0,
        )
        self.entries.append(e)
        return len(self.entries) - 1

    def set(self, strref: int, text: str, sound_resref: str = "",
            sound_length: float = 0.0) -> None:
        """Set or overwrite entry at strref, padding with blanks if needed."""
        while len(self.entries) <= strref:
            self.entries.append(TLKEntry())
        e = self.entries[strref]
        e.text         = text
        e.sound_resref = sound_resref[:16]
        e.sound_length = sound_length
        e.text_present         = bool(text)
        e.sound_present        = bool(sound_resref)
        e.soundlength_present  = sound_length > 0.0

    def resize(self, count: int) -> None:
        """Resize entries list to count, padding with blank entries."""
        while len(self.entries) < count:
            self.entries.append(TLKEntry())
        self.entries = self.entries[:count]

    def find_text(self, text: str) -> Optional[int]:
        """Return the first StrRef whose text matches, or None."""
        for i, e in enumerate(self.entries):
            if e.text == text:
                return i
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  TLK Reader
# ─────────────────────────────────────────────────────────────────────────────

class TLKReader:
    """
    Reads KotOR TLK V3.0 binary files.

    Reference: PyKotor Libraries/PyKotor/src/pykotor/resource/formats/tlk/io_tlk.py
    """

    @staticmethod
    def from_bytes(data: bytes) -> TLKFile:
        """Parse TLK from a bytes object. Returns a TLKFile."""
        tlk = TLKFile()
        TLKReader._parse(data, tlk)
        return tlk

    @staticmethod
    def from_file(path: str) -> TLKFile:
        """Load and parse a .tlk file. Returns a TLKFile."""
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            log.error(f"TLK: cannot read {path!r}: {e}")
            return TLKFile()
        return TLKReader.from_bytes(data)

    @staticmethod
    def _parse(data: bytes, tlk: TLKFile) -> None:
        if len(data) < TLK_HEADER_SIZE:
            log.warning(f"TLK: too short ({len(data)} bytes)")
            return

        magic   = data[:4]
        version = data[4:8]

        if magic != TLK_MAGIC:
            log.warning(f"TLK: bad magic {magic!r}, expected b'TLK '")
            return
        if version != TLK_VERSION:
            log.warning(f"TLK: unsupported version {version!r}")
            return

        language_id, string_count, strings_offset = struct.unpack_from(
            "<III", data, 8
        )
        tlk.language_id = language_id
        tlk.resize(string_count)

        # ── Read all entry headers in one batch (optimised like PyKotor) ─────
        entries_start = TLK_HEADER_SIZE
        entries_size  = string_count * TLK_ENTRY_SIZE

        if entries_start + entries_size > len(data):
            log.warning("TLK: truncated entry table")
            return

        entries_data = data[entries_start: entries_start + entries_size]

        # Cache text offset+length for each entry
        text_headers: List[Tuple[int, int]] = []

        for i in range(string_count):
            off = i * TLK_ENTRY_SIZE
            entry = tlk.entries[i]

            entry_flags,         = struct.unpack_from("<I", entries_data, off)
            sound_resref_bytes    = entries_data[off + 4: off + 20]
            _vol, _pitch, text_offset, text_length = struct.unpack_from(
                "<IIII", entries_data, off + 20
            )
            sound_length,        = struct.unpack_from("<f", entries_data, off + 36)

            # Decode ResRef (null-terminated ASCII, max 16 chars)
            null_pos = sound_resref_bytes.find(b"\x00")
            if null_pos >= 0:
                sound_resref_bytes = sound_resref_bytes[:null_pos]
            entry.sound_resref        = sound_resref_bytes.decode("ascii", errors="ignore")
            entry.sound_length        = sound_length
            entry.text_present        = bool(entry_flags & TLK_FLAG_TEXT_PRESENT)
            entry.sound_present       = bool(entry_flags & TLK_FLAG_SOUND_PRESENT)
            entry.soundlength_present = bool(entry_flags & TLK_FLAG_SOUND_LENGTH)
            text_headers.append((text_offset, text_length))

        # ── Read text strings ──────────────────────────────────────────────────
        for i, (text_offset, text_length) in enumerate(text_headers):
            if not tlk.entries[i].text_present or text_length == 0:
                tlk.entries[i].text = ""
                continue
            abs_off = strings_offset + text_offset
            if abs_off + text_length > len(data):
                log.debug(f"TLK entry {i}: text out of bounds")
                tlk.entries[i].text = ""
                continue
            raw = data[abs_off: abs_off + text_length]
            # Strip trailing null terminator if present
            if raw.endswith(b"\x00"):
                raw = raw[:-1]
            try:
                tlk.entries[i].text = raw.decode("utf-8")
            except UnicodeDecodeError:
                tlk.entries[i].text = raw.decode("latin-1", errors="replace")

        log.debug(f"TLK: loaded {string_count} entries (lang={language_id})")


# ─────────────────────────────────────────────────────────────────────────────
#  TLK Writer
# ─────────────────────────────────────────────────────────────────────────────

class TLKWriter:
    """
    Writes KotOR TLK V3.0 binary files from a TLKFile object.

    Reference: PyKotor Libraries/PyKotor/src/pykotor/resource/formats/tlk/io_tlk.py
    """

    def __init__(self, tlk: TLKFile):
        self.tlk = tlk

    def to_bytes(self) -> bytes:
        """Serialise the TLKFile to a bytes object."""
        n = len(self.tlk.entries)

        # Build the string data section first so we have offsets
        string_data = bytearray()
        text_offsets: List[int] = []
        text_lengths: List[int] = []

        for entry in self.tlk.entries:
            if entry.text_present and entry.text:
                raw = entry.text.encode("utf-8")
                text_offsets.append(len(string_data))
                text_lengths.append(len(raw))
                string_data.extend(raw)
            else:
                text_offsets.append(0)
                text_lengths.append(0)

        # Offsets
        header_size    = TLK_HEADER_SIZE
        entry_table_size = n * TLK_ENTRY_SIZE
        strings_offset = header_size + entry_table_size

        buf = bytearray()

        # ── Header ────────────────────────────────────────────────────────────
        buf += TLK_MAGIC
        buf += TLK_VERSION
        buf += struct.pack("<III",
                           self.tlk.language_id,
                           n,
                           strings_offset)

        assert len(buf) == TLK_HEADER_SIZE

        # ── Entry table ───────────────────────────────────────────────────────
        for i, entry in enumerate(self.tlk.entries):
            rr = (entry.sound_resref[:16]
                  .encode("ascii", errors="replace")
                  .ljust(16, b"\x00"))
            buf += struct.pack("<I", entry.flags)
            buf += rr
            buf += struct.pack("<IIII",
                               0,                   # volume_variance (unused)
                               0,                   # pitch_variance  (unused)
                               text_offsets[i],
                               text_lengths[i])
            buf += struct.pack("<f", entry.sound_length)

        # ── String data ───────────────────────────────────────────────────────
        buf += string_data

        return bytes(buf)

    def to_file(self, path: str) -> None:
        """Write to a file."""
        Path(path).write_bytes(self.to_bytes())


# ─────────────────────────────────────────────────────────────────────────────
#  Convenience functions
# ─────────────────────────────────────────────────────────────────────────────

def read_tlk(source: bytes) -> TLKFile:
    """Parse TLK bytes and return a TLKFile."""
    return TLKReader.from_bytes(source)


def write_tlk(tlk: TLKFile) -> bytes:
    """Serialise a TLKFile to bytes."""
    return TLKWriter(tlk).to_bytes()
