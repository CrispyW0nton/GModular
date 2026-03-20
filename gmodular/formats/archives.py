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


# ── Resource Type IDs (Odyssey / KotOR 1 & 2) ─────────────────────────────
# Source: PyKotor ResourceType enum (authoritative game-verified IDs)
# github.com/NickHugi/PyKotor  Libraries/PyKotor/src/pykotor/resource/type.py

RES_TYPE_MAP: Dict[int, str] = {
    # ── low IDs ───────────────────────────────────────────────────────────
    1:   "bmp",
    3:   "tga",
    4:   "wav",
    6:   "plt",
    7:   "ini",
    8:   "bmu",   # obfuscated mp3
    10:  "txt",
    # ── Odyssey 2000-series (KotOR 1 & 2) ────────────────────────────────
    2000:"plh",
    2001:"tex",
    2002:"mdl",
    2003:"thg",
    2005:"fnt",
    2007:"lua",
    2008:"slt",
    2009:"nss",   # NWScript source                     [canonical: NSS=2009]
    2010:"ncs",   # NWScript compiled                  [canonical: NCS=2010]
    2011:"mod",   # module archive (ERF variant)
    2012:"are",   # area GFF
    2013:"set",
    2014:"ifo",   # module info GFF
    2015:"bic",
    2016:"wok",   # walkmesh
    2017:"2da",
    2018:"tlk",
    2022:"txi",   # texture info
    2023:"git",   # game instance template GFF
    2024:"bti",
    2025:"uti",   # item template
    2026:"btc",
    2027:"utc",   # creature template
    2029:"dlg",   # dialog GFF
    2030:"itp",   # item palette
    2031:"btt",   # base trigger template
    2032:"utt",   # trigger template
    2033:"dds",
    2035:"uts",   # sound template
    2036:"ltr",   # name generation (letter probability)
    2037:"gff",   # generic GFF
    2038:"fac",   # faction GFF
    2039:"bte",   # base encounter template    [PyKotor: BTE=2039]
    2040:"ute",   # encounter template          [PyKotor: UTE=2040]
    2041:"btd",   # base door template          [PyKotor: BTD=2041]
    2042:"utd",   # door template               [PyKotor: UTD=2042]
    2043:"btp",   # base placeable template     [PyKotor: BTP=2043]
    2044:"utp",   # placeable template          [PyKotor: UTP=2044]
    2045:"dft",   # defaults                    [PyKotor: DFT=2045]
    2046:"gic",   # game instance comments GFF  [PyKotor: GIC=2046]
    2047:"gui",   # GUI GFF                     [PyKotor: GUI=2047]
    2048:"css",   # client side script
    2049:"ccs",   # client side script compiled
    2050:"btm",   # base merchant template      [PyKotor: BTM=2050]
    2051:"utm",   # merchant template           [PyKotor: UTM=2051]
    2052:"dwk",   # door walkmesh               [PyKotor: DWK=2052]
    2053:"pwk",   # placeable walkmesh          [PyKotor: PWK=2053]
    2055:"wlk",   # walkmesh (NWN)
    2056:"jrl",   # journal GFF                 [PyKotor: JRL=2056]
    2057:"sav",   # save ERF                    [PyKotor: SAV=2057]
    2058:"utw",   # waypoint template           [PyKotor: UTW=2058]
    2059:"4pc",   # 4-bit packed colour texture [PyKotor: FourPC=2059]
    2060:"ssf",   # soundset                    [PyKotor: SSF=2060]
    2061:"hak",   # hak module                  [PyKotor: HAK=2061]
    2062:"nwm",   # NWN module                  [PyKotor: NWM=2062]
    2063:"bik",   # bink video                  [PyKotor: BIK=2063]
    2064:"ndb",   # NDB (debug)                 [PyKotor: NDB=2064]
    2065:"ptm",   # plot manager                [PyKotor: PTM=2065]
    2066:"ptt",   # plot wizard blueprint       [PyKotor: PTT=2066]
    # ── KotOR-specific high IDs (verified against canonical resource type list) ─
    # Canonical: LYT=3000, VIS=3001, RIM=3002, PTH=3003, LIP=3004, TPC=3007, MDX=3008
    3000:"lyt",   # KotOR room layout (plain text)    [PyKotor: LYT=3000]
    3001:"vis",   # KotOR room visibility (plain text) [PyKotor: VIS=3001]
    3002:"rim",   # KotOR module resource image         [canonical: RIM=3002]
    3003:"pth",   # KotOR area path/waypoints (GFF)     [canonical: PTH=3003]
    3004:"lip",   # KotOR lip-sync animation            [canonical: LIP=3004]
    3007:"tpc",   # KotOR TPC texture (binary)         [PyKotor: TPC=3007]
    3008:"mdx",   # model mesh (binary)                [PyKotor: MDX=3008]
    # ── container formats ─────────────────────────────────────────────────
    9997:"erf",   # Encapsulated Resource Format
    9998:"bif",   # BIF data archive
    9999:"key",   # KEY index file
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
            # Strip both null bytes AND spaces — some KotOR tools pad with spaces
            raw_resref = (data[koff:koff + 16]
                          .rstrip(b"\x00")
                          .decode("ascii", errors="replace")
                          .strip())
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
            # Strip both null bytes AND spaces
            raw_resref = (data[pos:pos + 16]
                          .rstrip(b"\x00")
                          .decode("ascii", errors="replace")
                          .strip())
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
#  ERF Writer — build ERF V1.0 archives from in-memory resources
# ─────────────────────────────────────────────────────────────────────────────

class ERFWriter:
    """
    Build a KotOR ERF V1.0 archive from in-memory resources.

    Usage::
        w = ERFWriter()
        w.add_resource("manm26ab", "wok", wok_bytes)
        w.add_resource("manm26ab", "are", are_bytes)
        erf_bytes = w.to_bytes()
        Path("module.mod").write_bytes(erf_bytes)

    ERF V1.0 layout (160-byte header):
      [0]  4 bytes  file_type   (e.g. b"ERF ")
      [4]  4 bytes  version     (b"V1.0")
      [8]  uint32   lang_count
      [12] uint32   lang_size
      [16] uint32   entry_count
      [20] uint32   localised_string_offset
      [24] uint32   key_list_offset
      [28] uint32   resource_offset
      [32] uint32   build_year
      [36] uint32   build_day
      [40] uint32   description_strref
      [44] 116 bytes reserved (zeros)
      [160] key_list:   entry_count × 24 bytes each
                        ResRef(16) + ResID(4) + ResType(2) + Unused(2)
      [160+entry_count*24] resource_list: entry_count × 8 bytes each
                        Offset(4) + Size(4)
      [data section]  resource data, concatenated
    """

    _ERF_HEADER_SIZE = 160  # Standard ERF V1.0 header

    def __init__(self, file_type: str = "ERF "):
        self._file_type = file_type[:4].ljust(4)
        self._entries: List[Tuple[str, int, bytes]] = []  # (resref, res_type, data)

    def add_resource(self, resref: str, ext: str, data: bytes) -> None:
        """Add a resource to the archive.

        Args:
            resref: Resource reference name (≤16 chars, no extension).
            ext:    File extension / type name (e.g. "wok", "are", "gff").
            data:   Raw resource bytes.
        """
        res_type = EXT_TO_TYPE.get(ext.lower(), 0)
        clean = resref[:16].lower()
        self._entries.append((clean, res_type, data))

    # Convenience alias
    add = add_resource

    def to_bytes(self) -> bytes:
        """Serialise the archive to a bytes object."""
        n = len(self._entries)
        key_list_off = self._ERF_HEADER_SIZE
        res_list_off = key_list_off + n * 24
        data_off     = res_list_off + n * 8

        buf = bytearray()

        # ── Header (160 bytes) ────────────────────────────────────────────
        buf += self._file_type.encode("ascii")[:4]
        buf += b"V1.0"
        buf += struct.pack("<IIIIIIIII",
                           0,              # lang_count
                           0,              # lang_size
                           n,              # entry_count
                           self._ERF_HEADER_SIZE,  # localised_string_offset (no strings)
                           key_list_off,   # key_list_offset
                           res_list_off,   # resource_offset
                           0,              # build_year
                           0,              # build_day
                           0xFFFFFFFF,     # description_strref
                           )
        buf += b"\x00" * (self._ERF_HEADER_SIZE - len(buf))

        # ── Key list ──────────────────────────────────────────────────────
        for i, (resref, res_type, _) in enumerate(self._entries):
            rr = resref.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
            buf += rr
            buf += struct.pack("<I", i)          # res_id
            buf += struct.pack("<H", res_type)   # res_type
            buf += b"\x00\x00"                   # unused

        # ── Resource list ─────────────────────────────────────────────────
        cur_off = data_off
        for _, _, data in self._entries:
            buf += struct.pack("<II", cur_off, len(data))
            cur_off += len(data)

        # ── Resource data ─────────────────────────────────────────────────
        for _, _, data in self._entries:
            buf += data

        return bytes(buf)

    def to_file(self, path: str) -> None:
        """Write the archive to a file."""
        Path(path).write_bytes(self.to_bytes())


class ERFReaderMem:
    """
    Read a KotOR ERF V1.0 archive from an in-memory bytes object.

    This is a convenience wrapper around the bytes-based ERF parsing logic,
    complementary to the file-based ERFReader.

    Usage::
        reader = ERFReaderMem(erf_bytes)
        wok_data = reader.get_resource("manm26ab", "wok")
    """

    def __init__(self, data: bytes):
        self._data = data
        self._resources: Dict[str, Tuple[int, int]] = {}  # key → (offset, size)
        self._load()

    def _load(self) -> None:
        data = self._data
        if len(data) < 160:
            return
        try:
            (lang_count, lang_size, entry_count,
             loc_off, key_off, res_off,
             build_year, build_day, desc_strref,
             ) = struct.unpack_from("<9I", data, 8)

            for i in range(entry_count):
                koff = key_off + i * 24
                raw_resref = (data[koff:koff + 16]
                              .rstrip(b"\x00")
                              .decode("ascii", errors="replace")
                              .strip().lower())
                res_type = struct.unpack_from("<H", data, koff + 20)[0]

                roff = res_off + i * 8
                r_offset, r_size = struct.unpack_from("<II", data, roff)

                ext = RES_TYPE_MAP.get(res_type, "bin")
                self._resources[f"{raw_resref}.{ext}"] = (r_offset, r_size)
        except Exception as e:
            log.debug(f"ERFReaderMem parse error: {e}")

    def get_resource(self, resref: str, ext: str) -> Optional[bytes]:
        """Return raw bytes for a resource, or None if not found."""
        key = f"{resref.lower()}.{ext.lower()}"
        entry = self._resources.get(key)
        if entry is None:
            return None
        offset, size = entry
        return self._data[offset:offset + size]

    def list_resources(self) -> List[str]:
        """Return list of 'resref.ext' strings for all resources in the archive."""
        return list(self._resources.keys())


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
                except OSError as exc:
                    log.debug("archives: could not read override file %s: %s", p, exc)

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
