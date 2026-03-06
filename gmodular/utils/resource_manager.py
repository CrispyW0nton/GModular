"""
GModular — Resource Manager
Provides access to KotOR 1 & 2 game resource archives (BIF/KEY, ERF, RIM).

This is a stub implementation. Full BIF/KEY parsing is planned for Phase 3.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Resource type extensions used in KotOR
RESTYPE_EXTENSIONS: Dict[int, str] = {
    0x0001: "bmp",
    0x0003: "tga",
    0x0004: "wav",
    0x0006: "plt",
    0x000A: "ini",
    0x000C: "bmu",
    0x000F: "txt",
    0x07D0: "mdl",
    0x07D1: "mdx",
    0x07D2: "walkmesh",
    0x07D5: "xeox",
    0x07DA: "are",
    0x07DB: "set",
    0x07DC: "ifo",
    0x07DD: "bic",
    0x07DE: "wok",
    0x07DF: "2da",
    0x07E1: "ltr",
    0x07E2: "gff",
    0x07E3: "fac",
    0x07E6: "dlg",
    0x07E7: "itp",
    0x07E9: "git",
    0x07EA: "uti",
    0x07EB: "utc",
    0x07ED: "utt",
    0x07EE: "dds",
    0x07EF: "uts",
    0x07F0: "lff",
    0x07F1: "ssf",
    0x07F3: "ndb",
    0x07F4: "ptm",
    0x07F5: "ptt",
    0x0800: "utp",
    0x0801: "utd",
    0x0803: "utr",
    0x0804: "utm",
    0x0805: "uta",
    0x0806: "utw",
    0x0807: "ute",
    0x0809: "txi",
    0x080A: "ncs",
    0x080C: "nss",
}


@dataclass
class ResourceEntry:
    """A single named resource located in the game data."""
    resref: str          # resource name (up to 16 chars, no extension)
    restype: int         # numeric resource type
    source: str          # human-readable source path
    offset: int = 0      # byte offset in source file
    size: int   = 0      # byte size of resource

    @property
    def extension(self) -> str:
        return RESTYPE_EXTENSIONS.get(self.restype, f"res{self.restype:04x}")

    @property
    def filename(self) -> str:
        return f"{self.resref}.{self.extension}"

    def __repr__(self) -> str:
        return f"<ResourceEntry {self.filename!r} @ {self.source}>"


@dataclass
class ResourceManager:
    """
    Lightweight resource manager for KotOR 1 & 2 game data.

    Usage:
        rm = ResourceManager()
        rm.add_game_directory("/path/to/kotor")
        placeables = rm.find_by_type(0x0800)   # UTPs
        creatures  = rm.find_by_type(0x07EB)   # UTCs

    Full BIF/KEY parsing is a Phase 3 feature; this class currently
    discovers loose files in the Override and Modules directories.
    """
    _entries: Dict[str, ResourceEntry] = field(default_factory=dict)
    _game_dir: Optional[str] = None

    # ── Public API ──────────────────────────────────────────────────────────

    def add_game_directory(self, game_dir: str) -> bool:
        """
        Scan a KotOR installation directory for resources.

        Checks for chitin.key (validates this looks like a real install),
        then indexes loose files from Override/ and Modules/.

        Returns True if the directory looks valid.
        """
        path = Path(game_dir)
        if not (path / "chitin.key").exists():
            log.warning(f"No chitin.key found in {game_dir!r} — not a valid KotOR directory")
            return False

        self._game_dir = str(path)
        log.info(f"Indexing game directory: {game_dir}")

        # Scan Override/
        override_dir = path / "Override"
        if override_dir.exists():
            n = self._scan_directory(override_dir, "Override")
            log.info(f"  Override: {n} resources")

        # Scan Modules/
        modules_dir = path / "Modules"
        if modules_dir.exists():
            n = self._scan_directory(modules_dir, "Modules")
            log.info(f"  Modules: {n} resources")

        # Stub: BIF/KEY parsing would happen here in Phase 3
        log.info(f"  Total indexed: {len(self._entries)} resources "
                 f"(BIF archives not yet parsed — Phase 3)")
        return True

    def find(self, resref: str, restype: Optional[int] = None) -> Optional[ResourceEntry]:
        """
        Find a resource by resref and optional type.

        resref is matched case-insensitively.
        """
        key = resref.lower()
        if restype is not None:
            full_key = f"{key}.{RESTYPE_EXTENSIONS.get(restype, str(restype))}"
            return self._entries.get(full_key)
        # Try to find any entry matching the resref
        for k, v in self._entries.items():
            if k.split(".")[0] == key:
                return v
        return None

    def find_by_type(self, restype: int) -> List[ResourceEntry]:
        """Return all resources of a given numeric type."""
        ext = RESTYPE_EXTENSIONS.get(restype, f"res{restype:04x}")
        return [e for k, e in self._entries.items() if k.endswith(f".{ext}")]

    def find_placeables(self) -> List[ResourceEntry]:
        """Return all UTP (placeable template) resources."""
        return self.find_by_type(0x0800)

    def find_creatures(self) -> List[ResourceEntry]:
        """Return all UTC (creature template) resources."""
        return self.find_by_type(0x07EB)

    def find_doors(self) -> List[ResourceEntry]:
        """Return all UTD (door template) resources."""
        return self.find_by_type(0x0801)

    def find_waypoints(self) -> List[ResourceEntry]:
        """Return all UTW (waypoint template) resources."""
        return self.find_by_type(0x0806)

    def find_scripts(self) -> List[ResourceEntry]:
        """Return all NCS (compiled script) resources."""
        return self.find_by_type(0x080A)

    def find_script_sources(self) -> List[ResourceEntry]:
        """Return all NSS (script source) resources."""
        return self.find_by_type(0x080C)

    def read(self, resref: str, restype: int) -> Optional[bytes]:
        """
        Read raw bytes for a resource.

        Currently only supports loose files; BIF reading is Phase 3.
        """
        entry = self.find(resref, restype)
        if entry is None:
            return None
        try:
            with open(entry.source, "rb") as f:
                if entry.size > 0:
                    f.seek(entry.offset)
                    return f.read(entry.size)
                return f.read()
        except OSError as e:
            log.error(f"Failed to read {entry!r}: {e}")
            return None

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def game_directory(self) -> Optional[str]:
        return self._game_dir

    def clear(self):
        """Remove all indexed resources."""
        self._entries.clear()
        self._game_dir = None

    # ── Internal ────────────────────────────────────────────────────────────

    def _scan_directory(self, directory: Path, label: str) -> int:
        """
        Index all loose resource files in a directory (non-recursive).
        Returns number of entries added.
        """
        count = 0
        ext_to_type = {v: k for k, v in RESTYPE_EXTENSIONS.items()}
        try:
            for file_path in sorted(directory.iterdir()):
                if not file_path.is_file():
                    continue
                stem = file_path.stem.lower()[:16]
                ext  = file_path.suffix.lstrip(".").lower()
                restype = ext_to_type.get(ext)
                if restype is None:
                    continue
                key = f"{stem}.{ext}"
                entry = ResourceEntry(
                    resref  = stem,
                    restype = restype,
                    source  = str(file_path),
                    offset  = 0,
                    size    = file_path.stat().st_size,
                )
                self._entries[key] = entry
                count += 1
        except PermissionError as e:
            log.warning(f"Cannot scan {directory}: {e}")
        return count

    def _add_entry(self, resref: str, restype: int, source: str,
                   offset: int = 0, size: int = 0):
        """Add a single entry (used internally and for testing)."""
        ext = RESTYPE_EXTENSIONS.get(restype, f"res{restype:04x}")
        key = f"{resref.lower()}.{ext}"
        self._entries[key] = ResourceEntry(resref, restype, source, offset, size)


# Module-level singleton
_default_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    """Return the module-level ResourceManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ResourceManager()
    return _default_manager
