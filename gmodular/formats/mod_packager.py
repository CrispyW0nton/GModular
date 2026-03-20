"""
GModular — MOD Packager (P6)
Builds a KotOR .mod ERF archive from an open module.

Pipeline:
  1. Dependency walk: starting from .git, collect ALL resrefs referenced by
     the module (scripts, blueprints, sounds, textures not in base game).
  2. Validation: tag uniqueness, resref length, missing files, etc.
  3. Pack: write ERF/MOD binary with correct header ("MOD ", "V1.0").

Reference: PyKotor pykotor/resource/formats/erf/
           xoreos src/aurora/erffile.cpp
           KotOR mod format: Little-endian, FileType "MOD ", Version "V1.0"
"""
from __future__ import annotations
import struct
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .archives import EXT_TO_TYPE, RES_TYPE_MAP
from .gff_types import (
    GITData, GITPlaceable, GITCreature, GITDoor, GITTrigger,
    GITWaypoint, GITSoundObject, GITStoreObject,
    AREData, IFOData,
)

log = logging.getLogger(__name__)

# ── Severity constants ──────────────────────────────────────────────────────

ERROR   = "error"
WARNING = "warning"
INFO    = "info"


@dataclass
class ValidationIssue:
    severity: str   # ERROR, WARNING, INFO
    message: str

    def __str__(self):
        icon = {"error": "[E]", "warning": "[W]", "info": "[i]"}.get(self.severity, "[?]")
        return f"{icon} {self.message}"


@dataclass
class PackageResource:
    """One resource to be packed into the .mod."""
    resref: str              # ResRef (≤16 chars, no extension)
    res_type: int            # numeric type ID
    ext: str                 # file extension without dot
    data: bytes              # file content
    source_path: str = ""    # original path on disk (for display)


# ── ERF/MOD Binary Writer ─────────────────────────────────────────────────

class ERFWriter:
    """
    Writes a KotOR ERF/MOD binary file.

    ERF V1.0 layout (little-endian):
        Header   (160 bytes)
        KeyList  (24 bytes per resource)
        ResList  (8 bytes per resource)
        ResData  (variable)

    Header fields (offsets from 0):
        0x00  FileType    4s   "MOD "
        0x04  Version     4s   "V1.0"
        0x08  LangCount   I    0
        0x0C  LocalStrSz  I    0
        0x10  EntryCount  I    number of resources
        0x14  OffsetToLocalStrings I  always 0xA0 (160)
        0x18  OffsetToKeyList     I  0xA0
        0x1C  OffsetToResourceList I 0xA0 + 24*N
        0x20  BuildYear   I    year - 1900
        0x24  BuildDay    I    day of year
        0x28  DescStrRef  I    0xFFFFFFFF
        0x2C  Pad         116s  zeros
    """

    HEADER_SIZE   = 160      # 0xA0
    KEY_ENTRY_SZ  = 24       # ResRef[16] + ResID[4] + ResType[2] + Unused[2]
    RES_ENTRY_SZ  = 8        # Offset[4] + FileSize[4]

    def __init__(self, file_type: str = "MOD "):
        self.file_type = file_type[:4].ljust(4)
        self._resources: List[PackageResource] = []

    def add(self, res: PackageResource):
        self._resources.append(res)

    def to_bytes(self) -> bytes:
        n = len(self._resources)
        import datetime
        now = datetime.datetime.now()
        year = now.year - 1900
        day  = now.timetuple().tm_yday - 1

        offset_key  = self.HEADER_SIZE
        offset_res  = offset_key + n * self.KEY_ENTRY_SZ
        offset_data = offset_res + n * self.RES_ENTRY_SZ

        # Build resource data block
        data_block = bytearray()
        offsets = []
        for res in self._resources:
            offsets.append(offset_data + len(data_block))
            data_block.extend(res.data)

        # Header
        hdr = struct.pack(
            "<4s4sIIIIIIII116s",
            self.file_type.encode("ascii"),
            b"V1.0",
            0,                  # LangCount
            0,                  # LocalStrSz
            n,                  # EntryCount
            offset_key,         # OffsetToLocalStrings (same as key, no localstrings)
            offset_key,         # OffsetToKeyList
            offset_res,         # OffsetToResourceList
            year,               # BuildYear
            day,                # BuildDay
            b"\x00" * 116       # DescStrRef + Pad
        )
        # Fix: pack DescStrRef as 0xFFFFFFFF separately
        hdr_list = bytearray(hdr)
        struct.pack_into("<I", hdr_list, 0x28, 0xFFFFFFFF)
        hdr = bytes(hdr_list)

        # KeyList
        key_block = bytearray()
        for i, res in enumerate(self._resources):
            resref_bytes = res.resref[:16].encode("ascii", errors="replace").ljust(16, b"\x00")
            key_block.extend(struct.pack("<16sIHH", resref_bytes, i, res.res_type, 0))

        # ResourceList
        res_block = bytearray()
        for i, res in enumerate(self._resources):
            res_block.extend(struct.pack("<II", offsets[i], len(res.data)))

        return hdr + bytes(key_block) + bytes(res_block) + bytes(data_block)

    def write(self, path: str | Path):
        data = self.to_bytes()
        Path(path).write_bytes(data)
        log.info(f"MOD written: {path} ({len(data):,} bytes, {len(self._resources)} resources)")


# ── Dependency Walker ─────────────────────────────────────────────────────

# Resource types that are always included in a module package
CORE_RESREFS_TYPES = [
    ("are", "ARE"),
    ("ifo", "IFO"),
    ("git", "GIT"),
    ("lyt", "LYT"),
    ("vis", "VIS"),
]

# Script extensions to collect
SCRIPT_TYPES = {"nss", "ncs"}

# Blueprint extension map (GIT object type → blueprint file extension)
# Used by _get_all_resrefs to determine which blueprint ext matches each object
OBJECT_BLUEPRINT_EXT: Dict[str, str] = {
    "placeables": "utp",
    "creatures":  "utc",
    "doors":      "utd",
    "triggers":   "utt",
    "waypoints":  "utw",
    "sounds":     "uts",
    "stores":     "utm",
}

# All script hook attribute names present on GIT objects
_SCRIPT_ATTRS = (
    "on_used", "on_heartbeat", "on_open", "on_open2",
    "on_closed", "on_lock", "on_unlock", "on_damaged",
    "on_death", "on_end_conversation", "on_inventory_disturbed",
    "on_melee_attacked", "on_user_defined", "on_enter",
    "on_exit", "on_fail_to_open", "on_notice",
    "on_conversation", "on_disturbed", "on_blocked",
    "on_attacked", "on_spawn",
)


def _get_all_resrefs(git: GITData) -> List[Tuple[str, str]]:
    """
    Walk a GITData object and collect all (resref, ext) tuples for
    blueprints, scripts, and dialogs referenced by the module.

    Returns a de-duplicated list of (resref_lower, ext) tuples
    in dependency order: core blueprints first, scripts second, dialogs last.
    """
    found: List[Tuple[str, str]] = []

    def add(resref: str, ext: str):
        r = (resref or "").strip().lower()
        if r:
            found.append((r, ext))

    def walk_scripts(obj):
        for attr in _SCRIPT_ATTRS:
            val = getattr(obj, attr, "")
            if val:
                add(val, "ncs")

    if git is None:
        return found

    for p in getattr(git, "placeables", []):
        add(p.resref, "utp")
        walk_scripts(p)
        add(getattr(p, "conversation", ""), "dlg")

    for c in getattr(git, "creatures", []):
        add(c.resref, "utc")
        walk_scripts(c)
        add(getattr(c, "conversation", ""), "dlg")

    for d in getattr(git, "doors", []):
        add(d.resref, "utd")
        walk_scripts(d)
        add(getattr(d, "conversation", ""), "dlg")

    for t in getattr(git, "triggers", []):
        add(t.resref, "utt")
        walk_scripts(t)

    for w in getattr(git, "waypoints", []):
        add(w.resref, "utw")

    for s in getattr(git, "sounds", []):
        add(s.resref, "uts")

    for m in getattr(git, "stores", []):
        add(m.resref, "utm")

    # Deduplicate while preserving order
    seen: Set[Tuple[str, str]] = set()
    unique: List[Tuple[str, str]] = []
    for item in found:
        if item not in seen and item[0]:
            seen.add(item)
            unique.append(item)
    return unique


# ── chitin.key reader (base-game asset lookup) ────────────────────────────

def _read_chitin_key(game_dir: Path) -> Set[Tuple[str, str]]:
    """
    Parse KotOR's chitin.key to build a set of (resref, ext) tuples for all
    resources included in the base game's BIF archives.

    This lets the packager skip resources that are already in the base game
    (no need to re-package them into the .mod).

    chitin.key binary layout (little-endian):
      Header:
        +0   4s  file_type "KEY "
        +4   4s  version   "V1  "
        +8   I   bif_count
        +12  I   key_count
        +16  I   offset_to_bif_table
        +20  I   offset_to_key_table
        +24  I   build_year
        +28  I   build_day
        +32  32s reserved

      BIF entry (12 bytes each at offset_to_bif_table):
        +0   I   file_size
        +4   I   filename_offset  (from start of file)
        +8   H   filename_size
        +10  H   drives

      Key entry (22 bytes each at offset_to_key_table):
        +0   16s  resref
        +16  H    res_type
        +18  I    res_id

    Returns empty set on any parse error.
    """
    key_path = game_dir / "chitin.key"
    if not key_path.exists():
        return set()

    base_set: Set[Tuple[str, str]] = set()
    try:
        data = key_path.read_bytes()
        if len(data) < 64 or data[:4] not in (b"KEY ", b"KEY\x00"):
            return base_set

        key_count        = struct.unpack_from("<I", data, 12)[0]
        offset_key_table = struct.unpack_from("<I", data, 20)[0]

        for i in range(min(key_count, 200_000)):
            off = offset_key_table + i * 22
            if off + 22 > len(data):
                break
            raw_resref = data[off:off+16]
            res_type   = struct.unpack_from("<H", data, off + 16)[0]

            # Decode resref
            end = raw_resref.find(b'\x00')
            if end < 0:
                end = 16
            resref = raw_resref[:end].decode("ascii", errors="replace").lower().strip()
            if not resref:
                continue

            # Decode extension
            ext = RES_TYPE_MAP.get(res_type, "")
            if resref and ext:
                base_set.add((resref, ext))

    except Exception as e:
        log.debug(f"chitin.key parse error: {e}")

    log.debug(f"chitin.key: {len(base_set)} base-game resources indexed")
    return base_set


# ── Module Packager ──────────────────────────────────────────────────────

@dataclass
class PackagerResult:
    """Result from ModPackager.build()."""
    success: bool
    output_path: str
    file_size_bytes: int
    resources_packed: int
    issues: List[ValidationIssue]
    resource_list: List[PackageResource]

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def resource_count(self) -> int:
        """Alias for resources_packed — convenience property."""
        return self.resources_packed

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'FAILED'}: {self.output_path}",
            f"  {self.resources_packed} resources packed"
            + (f", {self.file_size_bytes:,} bytes" if self.success else ""),
        ]
        if self.errors:
            lines.append(f"  {len(self.errors)} error(s):")
            for e in self.errors[:10]:
                lines.append(f"    {e}")
        if self.warnings:
            lines.append(f"  {len(self.warnings)} warning(s):")
            for w in self.warnings[:10]:
                lines.append(f"    {w}")
        return "\n".join(lines)


class ModPackager:
    """
    Packages a GModular module into a .mod ERF archive.

    Usage:
        packager = ModPackager(module_dir, module_name, git, are, ifo)
        result = packager.build(output_path)
    """

    def __init__(
        self,
        module_dir: str | Path,
        module_name: str,
        git: Optional[GITData],
        are: Optional[AREData],
        ifo: Optional[IFOData],
        game_dir: Optional[str | Path] = None,
        override_dir: Optional[str | Path] = None,
    ):
        self._module_dir  = Path(module_dir)
        self._module_name = module_name.lower()
        self._git  = git
        self._are  = are
        self._ifo  = ifo
        self._game_dir     = Path(game_dir)     if game_dir     else None
        self._override_dir = Path(override_dir) if override_dir else None
        self._issues: List[ValidationIssue] = []

    # ── Public ────────────────────────────────────────────────────────────

    def validate_only(self) -> List[ValidationIssue]:
        """Run validation without building the archive."""
        self._issues = []
        self._run_validation()
        return list(self._issues)

    def build(self, output_path: str | Path) -> PackagerResult:
        """
        Build the .mod file.
        Returns a PackagerResult even if errors occur; check result.success.
        """
        self._issues = []
        output_path = Path(output_path)

        # Step 1: Validate
        self._run_validation()
        if any(i.severity == ERROR for i in self._issues):
            return PackagerResult(
                success=False,
                output_path=str(output_path),
                file_size_bytes=0,
                resources_packed=0,
                issues=list(self._issues),
                resource_list=[],
            )

        # Step 2: Collect resources
        resources = self._collect_resources()
        if not resources:
            self._issues.append(ValidationIssue(ERROR, "No resources to pack"))
            return PackagerResult(
                success=False,
                output_path=str(output_path),
                file_size_bytes=0,
                resources_packed=0,
                issues=list(self._issues),
                resource_list=[],
            )

        # Step 3: Write MOD file
        try:
            writer = ERFWriter("MOD ")
            for res in resources:
                writer.add(res)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            writer.write(output_path)
            size = output_path.stat().st_size
            return PackagerResult(
                success=True,
                output_path=str(output_path),
                file_size_bytes=size,
                resources_packed=len(resources),
                issues=list(self._issues),
                resource_list=resources,
            )
        except Exception as e:
            self._issues.append(ValidationIssue(ERROR, f"Write error: {e}"))
            return PackagerResult(
                success=False,
                output_path=str(output_path),
                file_size_bytes=0,
                resources_packed=0,
                issues=list(self._issues),
                resource_list=[],
            )

    # ── Validation ────────────────────────────────────────────────────────

    def _run_validation(self):
        """Run all validation checks and append to self._issues."""
        if self._git is None:
            self._issues.append(ValidationIssue(ERROR, "No GIT data — open a module first"))
            return

        self._check_tag_uniqueness()
        self._check_resref_lengths()
        self._check_script_presence()
        self._check_door_links()
        self._check_patrol_waypoints()
        self._check_object_counts()

    def _check_tag_uniqueness(self):
        """Tags must be unique (case-insensitive) across all object types."""
        seen: Dict[str, str] = {}   # tag_lower -> "type:resref"
        git = self._git

        all_objects = (
            [(p, "Placeable") for p in getattr(git, "placeables", [])] +
            [(c, "Creature")  for c in getattr(git, "creatures",  [])] +
            [(d, "Door")      for d in getattr(git, "doors",      [])] +
            [(t, "Trigger")   for t in getattr(git, "triggers",   [])] +
            [(w, "Waypoint")  for w in getattr(git, "waypoints",  [])] +
            [(s, "Store")     for s in getattr(git, "stores",     [])]
        )
        for obj, kind in all_objects:
            tag = getattr(obj, "tag", "").strip().lower()
            if not tag:
                self._issues.append(ValidationIssue(
                    WARNING, f"{kind} {getattr(obj, 'resref', '?')!r} has no tag"))
                continue
            if tag in seen:
                self._issues.append(ValidationIssue(
                    ERROR, f"Duplicate tag {tag!r}: {kind} conflicts with {seen[tag]}"))
            else:
                seen[tag] = f"{kind}:{getattr(obj, 'resref', '?')}"

    def _check_resref_lengths(self):
        """All ResRefs must be ≤16 characters."""
        git = self._git
        all_objects = (
            [(p, "Placeable") for p in getattr(git, "placeables", [])] +
            [(c, "Creature")  for c in getattr(git, "creatures",  [])] +
            [(d, "Door")      for d in getattr(git, "doors",      [])] +
            [(t, "Trigger")   for t in getattr(git, "triggers",   [])] +
            [(w, "Waypoint")  for w in getattr(git, "waypoints",  [])] +
            [(s, "Store")     for s in getattr(git, "stores",     [])]
        )
        script_attrs = [
            "on_used", "on_heartbeat", "on_open", "on_closed", "on_damaged",
            "on_death", "on_end_conversation", "on_inventory_disturbed",
            "on_melee_attacked", "on_user_defined", "on_enter", "on_exit",
            "on_fail_to_open", "on_notice", "on_conversation", "on_disturbed",
            "on_blocked", "on_attacked", "on_spawn", "on_open2", "on_lock",
            "on_unlock",
        ]
        for obj, kind in all_objects:
            resref = getattr(obj, "resref", "")
            if len(resref) > 16:
                self._issues.append(ValidationIssue(
                    ERROR, f"{kind} resref {resref!r} is {len(resref)} chars (max 16)"))
            for attr in script_attrs:
                val = getattr(obj, attr, "")
                if val and len(val) > 16:
                    self._issues.append(ValidationIssue(
                        ERROR, f"{kind} {getattr(obj,'tag','?')!r}: {attr}={val!r} is {len(val)} chars (max 16)"))

    def _check_script_presence(self):
        """Check that assigned .ncs scripts exist on disk."""
        if not self._module_dir.exists():
            return
        deps = _get_all_resrefs(self._git)
        for resref, ext in deps:
            if ext not in ("ncs", "nss"):
                continue
            found = self._find_resource(resref, ext)
            if not found:
                self._issues.append(ValidationIssue(
                    WARNING, f"Script {resref}.{ext} not found in module or Override"))

    def _check_door_links(self):
        """Doors with LinkedTo must refer to an existing door tag."""
        git = self._git
        door_tags = {getattr(d, "tag", "").lower() for d in getattr(git, "doors", [])}
        for d in getattr(git, "doors", []):
            linked = getattr(d, "linked_to", "").strip().lower()
            if linked and linked not in door_tags:
                self._issues.append(ValidationIssue(
                    WARNING, f"Door {getattr(d,'tag','?')!r}: LinkedTo={linked!r} not found in module"))

    def _check_patrol_waypoints(self):
        """
        For each creature, if it has WalkWayPoints (on_spawn contains
        'walkwaypoints' or waypoints named WP_[TAG]_01 exist), verify that
        WP_[TAG]_01 exists in the GIT.
        """
        git = self._git
        wp_tags = {getattr(w, "tag", "").lower() for w in getattr(git, "waypoints", [])}
        for c in getattr(git, "creatures", []):
            tag = getattr(c, "tag", "").strip().lower()
            if not tag:
                continue
            on_spawn = getattr(c, "on_spawn", "").lower()
            expected_wp = f"wp_{tag}_01"
            if "walkwaypoints" in on_spawn or expected_wp in wp_tags:
                if expected_wp not in wp_tags:
                    self._issues.append(ValidationIssue(
                        WARNING, f"Creature {tag!r}: OnSpawn references waypoints but "
                                 f"{expected_wp!r} not found in GIT"))

    def _check_object_counts(self):
        """Warn if module is empty."""
        git = self._git
        total = (
            len(getattr(git, "placeables", [])) +
            len(getattr(git, "creatures",  [])) +
            len(getattr(git, "doors",      [])) +
            len(getattr(git, "triggers",   [])) +
            len(getattr(git, "waypoints",  [])) +
            len(getattr(git, "sounds",     [])) +
            len(getattr(git, "stores",     []))
        )
        if total == 0:
            self._issues.append(ValidationIssue(INFO, "Module is empty (0 objects)"))

    # ── Resource Collection ────────────────────────────────────────────────

    def _collect_resources(self) -> List[PackageResource]:
        """Collect all resources to be packed into the .mod."""
        resources: List[PackageResource] = []
        name = self._module_name

        # Load chitin.key index (base-game assets to skip)
        base_game_assets: Set[Tuple[str, str]] = set()
        if self._game_dir:
            base_game_assets = _read_chitin_key(self._game_dir)
            if base_game_assets:
                self._issues.append(ValidationIssue(
                    INFO, f"chitin.key indexed {len(base_game_assets):,} base-game assets"))

        # Core files: .are, .ifo, .git, .lyt, .vis
        core_exts = ["are", "ifo", "git", "lyt", "vis"]
        for ext in core_exts:
            data = self._read_core_file(name, ext)
            if data:
                type_id = EXT_TO_TYPE.get(ext, 0)
                resources.append(PackageResource(
                    resref=name, res_type=type_id, ext=ext,
                    data=data, source_path=f"{name}.{ext}"))
            else:
                # Non-fatal: lyt/vis might not exist yet
                if ext in ("are", "ifo", "git"):
                    self._issues.append(ValidationIssue(
                        WARNING, f"Core file {name}.{ext} not found — module may be incomplete"))

        # Dependency files (blueprints, scripts, dialogs)
        deps = _get_all_resrefs(self._git)
        seen: Set[Tuple[str, str]] = set()
        skipped_base = 0
        for resref, ext in deps:
            key = (resref, ext)
            if key in seen:
                continue
            seen.add(key)

            # Skip resources that are in the base game (no need to re-pack)
            if key in base_game_assets:
                skipped_base += 1
                log.debug(f"Packager: skipping base-game asset {resref}.{ext}")
                continue

            data = self._find_resource(resref, ext)
            if data:
                type_id = EXT_TO_TYPE.get(ext, 0)
                resources.append(PackageResource(
                    resref=resref, res_type=type_id, ext=ext,
                    data=data, source_path=f"{resref}.{ext}"))
                # If it's an MDL, also collect its texture dependencies
                if ext == "mdl":
                    self._collect_mdl_textures(
                        resref, data, resources, seen, base_game_assets)
            else:
                # Check if it's in base game before warning
                # (The chitin.key may have missed it due to type mapping gaps)
                in_base = any(
                    r == resref for (r, e) in base_game_assets if e == ext)
                if not in_base:
                    self._issues.append(ValidationIssue(
                        WARNING, f"Dependency {resref}.{ext} not found (will not be packed)"))

        if skipped_base:
            self._issues.append(ValidationIssue(
                INFO, f"{skipped_base} dependency(ies) skipped — already in base game"))

        return resources

    def _collect_mdl_textures(
            self,
            mdl_resref: str,
            mdl_data: bytes,
            resources: List[PackageResource],
            seen: Set[Tuple[str, str]],
            base_game_assets: Set[Tuple[str, str]],
    ):
        """
        Scan an MDL file for texture references (TGA/TPC) and add them
        to the resource list if they exist in the module or override dirs.

        KotOR loads TPC over TGA if both exist; we prefer TPC → TGA fallback.
        """
        try:
            from .mdl_parser import list_mdl_dependencies
            deps = list_mdl_dependencies(mdl_data)
            tex_names = deps.get('textures', [])
        except Exception:
            return

        for tex in tex_names:
            for ext in ("tpc", "tga"):
                key = (tex, ext)
                if key in seen:
                    break
                if key in base_game_assets:
                    seen.add(key)
                    log.debug(f"Packager: texture {tex}.{ext} in base game, skipping")
                    break
                data = self._find_resource(tex, ext)
                if data:
                    seen.add(key)
                    type_id = EXT_TO_TYPE.get(ext, 0)
                    resources.append(PackageResource(
                        resref=tex, res_type=type_id, ext=ext,
                        data=data, source_path=f"{tex}.{ext}"))
                    log.debug(f"Packager: packed texture {tex}.{ext} "
                              f"(from MDL {mdl_resref})")
                    break   # found as TPC, no need to try TGA

    def _read_core_file(self, name: str, ext: str) -> Optional[bytes]:
        """Read a core module file from disk (tries module_dir first)."""
        for directory in [self._module_dir, self._override_dir]:
            if directory is None:
                continue
            path = directory / f"{name}.{ext}"
            if path.exists():
                return path.read_bytes()
        return None

    def _find_resource(self, resref: str, ext: str) -> Optional[bytes]:
        """
        Try to find a resource file in priority order:
          1. module_dir/resref.ext
          2. override_dir/resref.ext
          3. game_dir/Override/resref.ext
          4. game_dir/Modules/resref.ext
        Does NOT dig into BIF archives (base-game assets — use chitin.key check).
        Uses case-insensitive filename matching for cross-platform compatibility.
        """
        search_dirs = [
            self._module_dir,
            self._override_dir,
        ]
        if self._game_dir:
            search_dirs.append(self._game_dir / "Override")
            search_dirs.append(self._game_dir / "Modules")
            search_dirs.append(self._game_dir / "modules")

        target_lower = f"{resref}.{ext}".lower()

        for directory in search_dirs:
            if directory is None:
                continue
            # Exact path first
            path = directory / f"{resref}.{ext}"
            if path.exists():
                try:
                    return path.read_bytes()
                except Exception:
                    continue
            # Case-insensitive fallback
            try:
                for f in directory.iterdir():
                    if f.name.lower() == target_lower:
                        return f.read_bytes()
            except Exception as exc:
                log.debug("mod_packager: iterdir failed for %s: %s", directory, exc)
        return None
