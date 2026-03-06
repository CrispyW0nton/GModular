"""
GModular — KotOR Archive Reader
Supports KEY/BIF (game data), ERF/MOD/RIM (modules), and plain file-system access.

Based on:
  - xoreos src/aurora/keyfile.cpp, biffile.cpp, erffile.cpp
  - xoreos-tools documentation
"""
from __future__ import annotations
import struct
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, BinaryIO, Set
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# ── Resource Type IDs (Odyssey) ────────────────────────────────────────────

RES_TYPE_MAP: Dict[int, str] = {
    1:   "bmp",
    3:   "tga",
    4:   "wav",
    6:   "plt",
    7:   "ini",
    10:  "txt",
    2002:"mdl",
    2009:"nss",
    2010:"ncs",
    2012:"mod",
    2017:"are",
    2018:"set",
    2019:"ifo",
    2020:"bic",
    2021:"wok",
    2022:"2da",
    2023:"tlk",
    2025:"txi",
    2026:"git",
    2027:"bti",
    2028:"uti",
    2029:"btc",
    2030:"utc",
    2032:"dlg",
    2033:"itp",
    2034:"utt",
    2035:"dds",
    2036:"uts",
    2037:"ltr",
    2038:"gff",
    2039:"fac",
    2040:"ute",
    2041:"utd",
    2042:"uto",
    2043:"utp",
    2044:"dft",
    2045:"gic",
    2046:"gui",
    2047:"css",
    2048:"ccs",
    2049:"uty",
    2050:"ssf",
    2051:"hak",
    2052:"nwm",
    2053:"bik",
    2056:"tpc",
    2057:"mdx",
    2058:"wlk",
    2059:"xml",
    2060:"slt",
    3000:"ndb",
    3001:"ptm",
    3002:"ptt",
}

EXT_TO_TYPE: Dict[str, int] = {v: k for k, v in RES_TYPE_MAP.items()}


@dataclass
class ResourceEntry:
    """A single resource located somewhere (BIF, ERF, file-system)."""
    resref:   str           # ResRef without extension (≤16 chars)
    res_type: int           # Resource type ID
    source:   str           # "bif", "erf", "file"
    # For BIF-backed resources:
    bif_path: str = ""
    offset:   int = 0    # For BIF resources: resource table index (not raw file offset)
                         # Passed as res_idx to _read_bif() which handles the lookup
    size:     int = 0
    # For file-backed resources:
    file_path: str = ""

    @property
    def ext(self) -> str:
        return RES_TYPE_MAP.get(self.res_type, "bin")

    @property
    def filename(self) -> str:
        return f"{self.resref}.{self.ext}"

    def __repr__(self):
        return f"ResourceEntry({self.resref!r}.{self.ext})"


# ─────────────────────────────────────────────────────────────────────────────
#  KEY/BIF Reader
# ─────────────────────────────────────────────────────────────────────────────

class KEYReader:
    """
    Reads chitin.key (index) + .bif archives.
    chitin.key maps ResRef → BIF file + offset.
    """

    # Real chitin.key header is 64 bytes:
    # 4s  file_type ("KEY ")
    # 4s  version   ("V1  ")
    # I   bif_count
    # I   key_count
    # I   bif_table_offset
    # I   key_table_offset
    # I   build_year
    # I   build_day
    # 32s reserved
    _KEY_HEADER_FMT  = "<4s4sIIIIII32s"
    _KEY_HEADER_SIZE = struct.calcsize("<4s4sIIIIII32s")  # == 64

    def __init__(self, key_path: str):
        self.key_path = key_path
        self.game_dir = str(Path(key_path).parent)
        self.resources: Dict[str, ResourceEntry] = {}   # "resref.ext" → entry
        self._bif_paths: List[str] = []

    def load(self) -> int:
        """Parse chitin.key and index all BIF resources. Returns count."""
        try:
            with open(self.key_path, "rb") as f:
                data = f.read()
        except OSError as e:
            log.error(f"Cannot open KEY: {self.key_path}: {e}")
            return 0

        if len(data) < self._KEY_HEADER_SIZE:
            log.error("KEY file too small")
            return 0

        (file_type, version,
         bif_count, key_count,
         bif_offset, key_offset,
         _build_year, _build_day, _reserved) = \
            struct.unpack_from(self._KEY_HEADER_FMT, data, 0)

        ftype_s = file_type.rstrip(b"\x00 ").decode("ascii", errors="replace")
        ver_s   = version.rstrip(b"\x00 ").decode("ascii", errors="replace")
        if ftype_s not in ("KEY", "KEY "):
            log.warning(f"Unexpected KEY file_type: {ftype_s!r}")
        if ver_s not in ("V1", "V1  ", "V1.0"):
            log.warning(f"Unexpected KEY version: {ver_s!r}")

        # Parse BIF file table
        self._bif_paths = []
        pos = bif_offset
        for _ in range(bif_count):
            file_size, name_offset, name_size, drives = struct.unpack_from("<IIHH", data, pos)
            pos += 12
            # name_size includes the null terminator; guard against 0
            read_len = max(0, name_size - 1) if name_size > 0 else name_size
            bif_name = data[name_offset:name_offset + read_len]
            bif_name = bif_name.decode("ascii", errors="replace").replace("\\", "/")
            full_bif = self._resolve_bif_path(bif_name)
            self._bif_paths.append(full_bif)

        # Parse key table: each entry is (resref[16], res_type[2], res_id[4])
        count = 0
        pos = key_offset
        for _ in range(key_count):
            raw_resref = data[pos:pos + 16]
            res_type   = struct.unpack_from("<H", data, pos + 16)[0]
            res_id     = struct.unpack_from("<I", data, pos + 18)[0]
            pos += 22

            resref = raw_resref.rstrip(b"\x00").decode("ascii", errors="replace")
            bif_idx = (res_id >> 20) & 0xFFF
            res_idx = res_id & 0xFFFFF

            if bif_idx >= len(self._bif_paths):
                continue
            bif_path = self._bif_paths[bif_idx]

            entry = ResourceEntry(
                resref=resref,
                res_type=res_type,
                source="bif",
                bif_path=bif_path,
                offset=res_idx,   # BIF uses "index" not raw offset, resolved at read time
            )
            key = f"{resref.lower()}.{RES_TYPE_MAP.get(res_type, 'bin')}"
            self.resources[key] = entry
            count += 1

        log.info(f"KEY loaded: {count} resources, {len(self._bif_paths)} BIF files")
        return count

    def _resolve_bif_path(self, rel_path: str) -> str:
        """
        Resolve a BIF path relative to game_dir, performing a case-insensitive
        file-system search on Linux.  Returns the best match found, or the
        original joined path if nothing is located.
        """
        direct = os.path.join(self.game_dir, rel_path)
        if os.path.exists(direct):
            return direct
        # Try lower-case (common on Linux installs)
        lower = os.path.join(self.game_dir, rel_path.lower())
        if os.path.exists(lower):
            return lower
        # Walk the components and match case-insensitively
        parts = rel_path.replace("\\", "/").split("/")
        current = self.game_dir
        for part in parts:
            if not part:
                continue
            try:
                entries = os.listdir(current)
            except OSError:
                return direct
            match = next((e for e in entries if e.lower() == part.lower()), None)
            if match is None:
                return direct
            current = os.path.join(current, match)
        return current

    def read_resource(self, entry: ResourceEntry) -> Optional[bytes]:
        """Read a BIF resource by opening the .bif file."""
        try:
            return self._read_bif(entry.bif_path, entry.offset)
        except Exception as e:
            log.debug(f"BIF read error for {entry.resref}: {e}")
            return None

    @staticmethod
    def _read_bif(bif_path: str, res_idx: int) -> Optional[bytes]:
        """Read a resource from a .bif archive by resource table index."""
        try:
            with open(bif_path, "rb") as f:
                data = f.read()
        except OSError:
            return None

        file_type = data[:4]
        version   = data[4:8]

        if file_type == b"BIFF":
            # Standard BIFF V1
            var_count, fix_count, var_offset = struct.unpack_from("<III", data, 8)
            pos = var_offset + res_idx * 16
            if pos + 16 > len(data):
                return None
            r_id, r_offset, r_size, r_type = struct.unpack_from("<IIII", data, pos)
            return data[r_offset:r_offset + r_size]

        log.debug(f"Unknown BIF format: {file_type!r}")
        return None

    def get(self, resref: str, res_type: int) -> Optional[bytes]:
        """Convenience: look up and read a resource."""
        ext  = RES_TYPE_MAP.get(res_type, "bin")
        key  = f"{resref.lower()}.{ext}"
        entry = self.resources.get(key)
        if entry:
            return self.read_resource(entry)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  ERF/MOD/RIM Reader
# ─────────────────────────────────────────────────────────────────────────────

class ERFReader:
    """
    Reads .erf, .mod, .rim archives.
    ERF V1.0 format (GFF-like header with resource table).
    """

    def __init__(self, path: str):
        self.path = path
        self.resources: Dict[str, ResourceEntry] = {}

    def load(self) -> int:
        try:
            with open(self.path, "rb") as f:
                data = f.read()
        except OSError as e:
            log.error(f"ERF open error {self.path}: {e}")
            return 0

        if len(data) < 160:
            return 0

        file_type = data[:4].decode("ascii", errors="replace").strip()
        version   = data[4:8].decode("ascii", errors="replace").strip()

        if version not in ("V1.0", "V1.1"):
            log.warning(f"ERF version {version!r} at {self.path}")

        # RIM format differs slightly
        if file_type == "RIM ":
            return self._load_rim(data)

        # Standard ERF layout (160-byte header)
        (lang_count, lang_size, entry_count,
         loc_off, key_off, res_off,
         build_year, build_day, desc_strref,
         ) = struct.unpack_from("<9I", data, 8)

        count = 0
        for i in range(entry_count):
            # Key entry: ResRef(16) + ResID(4) + ResType(2) + unused(2)
            koff = key_off + i * 24
            raw_resref = data[koff:koff + 16].rstrip(b"\x00").decode("ascii", errors="replace")
            res_id     = struct.unpack_from("<I", data, koff + 16)[0]
            res_type   = struct.unpack_from("<H", data, koff + 20)[0]

            # Res entry: offset(4) + size(4)
            roff = res_off + i * 8
            r_offset, r_size = struct.unpack_from("<II", data, roff)

            ext = RES_TYPE_MAP.get(res_type, "bin")
            entry = ResourceEntry(
                resref=raw_resref,
                res_type=res_type,
                source="erf",
                file_path=self.path,
                offset=r_offset,
                size=r_size,
            )
            self.resources[f"{raw_resref.lower()}.{ext}"] = entry
            count += 1

        log.info(f"ERF loaded: {count} resources from {Path(self.path).name}")
        return count

    def _load_rim(self, data: bytes) -> int:
        """RIM format: 120-byte header."""
        entry_count = struct.unpack_from("<I", data, 20)[0]
        res_off     = struct.unpack_from("<I", data, 24)[0]
        count = 0
        for i in range(entry_count):
            pos = res_off + i * 32
            raw_resref = data[pos:pos + 16].rstrip(b"\x00").decode("ascii", errors="replace")
            res_type   = struct.unpack_from("<I", data, pos + 16)[0]
            res_id     = struct.unpack_from("<I", data, pos + 20)[0]
            r_offset   = struct.unpack_from("<I", data, pos + 24)[0]
            r_size     = struct.unpack_from("<I", data, pos + 28)[0]
            ext = RES_TYPE_MAP.get(res_type, "bin")
            entry = ResourceEntry(
                resref=raw_resref,
                res_type=res_type,
                source="erf",
                file_path=self.path,
                offset=r_offset,
                size=r_size,
            )
            self.resources[f"{raw_resref.lower()}.{ext}"] = entry
            count += 1
        return count

    def read_resource(self, entry: ResourceEntry) -> Optional[bytes]:
        try:
            with open(entry.file_path, "rb") as f:
                f.seek(entry.offset)
                return f.read(entry.size)
        except Exception as e:
            log.debug(f"ERF read error: {e}")
            return None

    def get(self, resref: str, res_type: int) -> Optional[bytes]:
        ext = RES_TYPE_MAP.get(res_type, "bin")
        entry = self.resources.get(f"{resref.lower()}.{ext}")
        if entry:
            return self.read_resource(entry)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Unified Resource Manager
# ─────────────────────────────────────────────────────────────────────────────

class ResourceManager:
    """
    Priority-ordered resource resolver:
    1. Override folder (plain files)
    2. Open ERF/MOD/RIM archives
    3. KEY/BIF game archives
    """

    def __init__(self):
        self._overrides: List[str]      = []   # plain file directories
        self._erfs:      List[ERFReader] = []
        self._keys:      List[KEYReader] = []
        self._game_dir:  Optional[str]   = None
        self._game_tag:  str             = "K1"

    def set_game(self, game_dir: str, tag: str = "K1"):
        """Load chitin.key from a KotOR game directory."""
        self._game_dir = game_dir
        self._game_tag = tag
        key_path = os.path.join(game_dir, "chitin.key")
        if not os.path.exists(key_path):
            log.warning(f"chitin.key not found in {game_dir}")
            return
        kr = KEYReader(key_path)
        kr.load()
        self._keys.append(kr)
        # Add override folder
        override = os.path.join(game_dir, "override")
        if os.path.isdir(override):
            self._overrides.insert(0, override)

    def load_erf(self, path: str):
        """Load an additional ERF/MOD/RIM."""
        er = ERFReader(path)
        er.load()
        self._erfs.insert(0, er)

    def add_override_dir(self, path: str):
        if os.path.isdir(path):
            self._overrides.insert(0, path)

    def get(self, resref: str, res_type: int) -> Optional[bytes]:
        """Resolve a resource by ResRef + type ID. Returns bytes or None."""
        ext = RES_TYPE_MAP.get(res_type, "bin")
        filename = f"{resref.lower()}.{ext}"

        # 1. Override directories
        for d in self._overrides:
            p = os.path.join(d, filename)
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        return f.read()
                except OSError:
                    pass

        # 2. Open ERF archives
        for er in self._erfs:
            data = er.get(resref, res_type)
            if data:
                return data

        # 3. KEY/BIF
        for kr in self._keys:
            data = kr.get(resref, res_type)
            if data:
                return data

        return None

    def get_file(self, resref: str, ext: str) -> Optional[bytes]:
        """Get resource by ResRef + extension string (e.g. 'mdl')."""
        type_id = EXT_TO_TYPE.get(ext.lower().lstrip("."))
        if type_id is None:
            log.debug(f"Unknown extension: {ext!r}")
            return None
        return self.get(resref, type_id)

    def list_resources(self, res_type: int) -> List[str]:
        """List all known ResRefs for a given type."""
        ext  = RES_TYPE_MAP.get(res_type, "bin")
        seen: Set[str] = set()
        results: List[str] = []

        for d in self._overrides:
            for f in os.listdir(d):
                name, fext = os.path.splitext(f)
                if fext.lower().lstrip(".") == ext.lower():
                    if name.lower() not in seen:
                        seen.add(name.lower())
                        results.append(name)

        for er in self._erfs:
            for key, entry in er.resources.items():
                if entry.res_type == res_type and entry.resref.lower() not in seen:
                    seen.add(entry.resref.lower())
                    results.append(entry.resref)

        for kr in self._keys:
            for key, entry in kr.resources.items():
                if entry.res_type == res_type and entry.resref.lower() not in seen:
                    seen.add(entry.resref.lower())
                    results.append(entry.resref)

        results.sort()
        return results

    @property
    def is_loaded(self) -> bool:
        return bool(self._keys or self._erfs or self._overrides)

    @property
    def game_tag(self) -> str:
        return self._game_tag


# Singleton resource manager used by the rest of GModular
_global_rm: Optional[ResourceManager] = None

def get_resource_manager() -> ResourceManager:
    global _global_rm
    if _global_rm is None:
        _global_rm = ResourceManager()
    return _global_rm
