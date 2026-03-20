"""
GModular — Module I/O Service
================================
Separates *loading logic* from *state management*.

Before this refactor, ``ModuleState.load_from_mod()`` contained ~200 lines of
ERF extraction, path guessing, resref detection, and temp-file writing — all
inside the state container.  This created Functional coupling between the state
layer and the archive format details (Khononov §3.1: high Strength + medium
Distance = high maintenance effort).

This module extracts that logic into a pure I/O service:

- ``ModuleIO`` is stateless — it does not hold references to ``ModuleState``.
- It returns a plain ``ModuleLoadResult`` dataclass that the caller can inspect
  and decide how to apply.
- It can be unit-tested without Qt or a live ``ModuleState`` instance.
- ``ModuleState.load_from_mod`` now delegates to ``ModuleIO`` (see core/module_state.py).

Khononov balance:
  Before:  Strength=Functional(3) × Distance=2 × Volatility=High  → 6H
  After:   Strength=Model(2)      × Distance=1 × Volatility=High  → 2H
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

log = logging.getLogger(__name__)


# ── Content-signature helpers ─────────────────────────────────────────────────

# GFF type tags (first 4 bytes) → canonical extension
_GFF_SIG_TO_EXT = {
    b"ARE ": "are",
    b"GIT ": "git",
    b"IFO ": "ifo",
    b"UTC ": "utc",
    b"UTD ": "utd",
    b"UTP ": "utp",
    b"UTO ": "uto",
    b"DFT ": "dft",
    b"UTT ": "utt",
    b"UTS ": "uts",
    b"UTE ": "ute",
    b"UTI ": "uti",
    b"UTM ": "utm",
    b"UTW ": "utw",
    b"DLG ": "dlg",
    b"JRL ": "jrl",
    b"FAC ": "fac",
    b"GIC ": "gic",
    b"GFF ": "gff",
}

# Binary MDL starts with 4 zero bytes (file-type placeholder)
_MDL_MAGIC = b"\x00\x00\x00\x00"
# BWM (walkmesh) starts with "BWM V"
_BWM_MAGIC = b"BWM V"
# TLK starts with "TLK V"
_TLK_MAGIC = b"TLK V"


def _remap_resources_by_signature(erf) -> dict:
    """
    Re-scan every ERF resource whose extension is 'bin', 'rev', 'mod', 'tlk'
    or any other ambiguous type that might actually contain GFF, MDL, MDX, or
    walkmesh data.

    Strategy:
      1. Read the first 8 bytes of each candidate resource.
      2. If it matches a known GFF signature  → remap to the correct GFF ext.
      3. If it starts with \\x00\\x00\\x00\\x00  → treat as binary MDL (.mdl).
      4. If it starts with "BWM V"            → treat as walkmesh (.wok).
      5. If the resource is named *.mdl and there is a matching *.rev of the
         same length as the MDL's own model_raw_size field → treat .rev as .mdx.

    Returns a new resources dict with corrected keys.
    """
    from ..formats.archives import ERFReader, ResourceEntry, RES_TYPE_MAP, EXT_TO_TYPE

    # Extensions that may be misidentified by ERF type-ID fallback.
    # 'tlk', 'mod', 'bin', 'rev' are common victims; never re-scan known binary
    # formats like tga, mdl, ncs, nss, 2da, etc.
    AMBIGUOUS_EXTS = {"bin", "rev", "mod", "tlk", "gff", "lyt", "vis", "ndb"}
    new_resources: dict = {}

    # Build a lookup of the original resources (lower-key → entry)
    originals = dict(erf.resources)

    for key, entry in originals.items():
        resref, _, cur_ext = key.rpartition(".")
        if not resref:
            resref, cur_ext = key, "bin"

        # Only probe ambiguous extensions to avoid re-parsing known good ones
        if cur_ext not in AMBIGUOUS_EXTS:
            new_resources[key] = entry
            continue

        # Read the first 8 bytes
        try:
            raw = erf.read_resource(entry)
            if not raw:
                new_resources[key] = entry
                continue
        except Exception:
            new_resources[key] = entry
            continue

        sig8 = raw[:8]
        sig4 = raw[:4]

        # ── GFF signature match ──────────────────────────────────────────
        new_ext = _GFF_SIG_TO_EXT.get(sig4)
        if new_ext and len(raw) >= 8 and raw[4:8] in (b"V3.2", b"V3.3"):
            new_type = EXT_TO_TYPE.get(new_ext, entry.res_type)
            new_entry = ResourceEntry(
                resref=entry.resref,
                res_type=new_type,
                source=entry.source,
                file_path=entry.file_path,
                offset=entry.offset,
                size=entry.size,
                bif_path=entry.bif_path,
            )
            new_key = f"{resref.lower()}.{new_ext}"
            new_resources[new_key] = new_entry
            if new_ext != cur_ext:
                log.debug(
                    "Remapped resource %s.%s → .%s (GFF signature %s)",
                    resref, cur_ext, new_ext, sig4.decode("ascii", errors="replace").strip(),
                )
            continue

        # ── Walkmesh (BWM) ──────────────────────────────────────────────
        if sig8[:5] == _BWM_MAGIC:
            new_type = EXT_TO_TYPE.get("wok", entry.res_type)
            new_entry = ResourceEntry(
                resref=entry.resref,
                res_type=new_type,
                source=entry.source,
                file_path=entry.file_path,
                offset=entry.offset,
                size=entry.size,
                bif_path=entry.bif_path,
            )
            new_key = f"{resref.lower()}.wok"
            new_resources[new_key] = new_entry
            log.debug("Remapped resource %s.%s → .wok (BWM signature)", resref, cur_ext)
            continue

        # ── Binary MDL (starts 0x00000000) ──────────────────────────────
        if sig4 == _MDL_MAGIC and cur_ext != "mdl":
            new_type = EXT_TO_TYPE.get("mdl", entry.res_type)
            new_entry = ResourceEntry(
                resref=entry.resref,
                res_type=new_type,
                source=entry.source,
                file_path=entry.file_path,
                offset=entry.offset,
                size=entry.size,
                bif_path=entry.bif_path,
            )
            new_key = f"{resref.lower()}.mdl"
            new_resources[new_key] = new_entry
            log.debug("Remapped resource %s.%s → .mdl (binary MDL signature)", resref, cur_ext)
            continue

        # ── .rev as MDX: cross-reference with MDL model_data_size ────────
        if cur_ext == "rev":
            # Binary MDX: size == model_data_size stored at MDL offset 8
            # (NOT model_raw_size at offset 12, which is the uncompressed buffer)
            mdl_key = f"{resref.lower()}.mdl"
            mdl_entry = originals.get(mdl_key)
            if mdl_entry:
                try:
                    mdl_raw = erf.read_resource(mdl_entry)
                    if mdl_raw and len(mdl_raw) >= 16:
                        import struct as _struct
                        # Offset 8 = model_data_size (actual MDX file size)
                        model_data_size = _struct.unpack_from("<I", mdl_raw, 8)[0]
                        # Also try offset 12 = model_raw_size as fallback
                        model_raw_size  = _struct.unpack_from("<I", mdl_raw, 12)[0]
                        if model_data_size > 0 and abs(len(raw) - model_data_size) < 64:
                            match_reason = f"model_data_size {model_data_size}"
                        elif model_raw_size > 0 and abs(len(raw) - model_raw_size) < 64:
                            match_reason = f"model_raw_size {model_raw_size}"
                        else:
                            match_reason = None
                        if match_reason:
                            new_type = EXT_TO_TYPE.get("mdx", entry.res_type)
                            new_entry = ResourceEntry(
                                resref=entry.resref,
                                res_type=new_type,
                                source=entry.source,
                                file_path=entry.file_path,
                                offset=entry.offset,
                                size=entry.size,
                                bif_path=entry.bif_path,
                            )
                            new_key = f"{resref.lower()}.mdx"
                            new_resources[new_key] = new_entry
                            log.debug(
                                "Remapped resource %s.rev → .mdx (size %d matches %s)",
                                resref, len(raw), match_reason,
                            )
                            continue
                except Exception as exc:
                    log.debug("module_io: rev→mdx remap failed for %s: %s", resref, exc)

        # No match — keep as-is
        new_resources[key] = entry

    return new_resources


def _create_texture_aliases(extract_dir: str, resources: dict) -> None:
    """
    Detect texture name mismatches between MDL references and on-disk TGA/TPC
    files, then create alias copies so the viewport can find them.

    Problem: Some SLEM/custom .mod archives ship TGA files with one prefix (e.g.
    ``sle_dirt02.tga``) while the MDL mesh nodes reference a different prefix
    (e.g. ``lsl_dirt02``).  The KotOR engine resolves this via its own override
    search, but GModular's viewport loads textures directly from the extract dir
    by exact resref match, so it misses them.

    Strategy:
      1. Find all .mdl files in the extract dir and scan their texture names.
      2. For each texture name that has NO exact match on disk, search for a
         file whose name differs only in the first component before ``_``.
      3. If found, copy (not symlink — Windows compat) the file under the alias
         name that the MDL expects.

    This is a one-time post-extraction step with no perf impact at runtime.
    """
    if not extract_dir or not os.path.isdir(extract_dir):
        return

    # Build a fast lookup of all TGA/TPC files in the extract dir
    on_disk: dict = {}   # lower_stem → full_path
    for fn in os.listdir(extract_dir):
        fn_lo = fn.lower()
        if fn_lo.endswith(('.tga', '.tpc')):
            stem = fn_lo[:-4]
            on_disk[stem] = os.path.join(extract_dir, fn)

    if not on_disk:
        return

    # Build the set of all texture names referenced by MDL files in the extract dir
    try:
        from ..formats.mdl_parser import MDLParser
    except ImportError:
        return

    needed_textures: set = set()
    for fn in os.listdir(extract_dir):
        if not fn.lower().endswith('.mdl'):
            continue
        mdl_path = os.path.join(extract_dir, fn)
        try:
            mdl_bytes = open(mdl_path, 'rb').read()
            # Quick texture scan — no MDX needed
            parser = MDLParser(mdl_bytes, b'')
            mesh_data = parser.parse()
            for node in mesh_data.mesh_nodes():
                tx = node.texture_clean.lower()
                if tx and tx not in ('null', ''):
                    needed_textures.add(tx)
        except Exception:
            continue

    if not needed_textures:
        return

    # For each texture that's missing, try to find a fuzzy match
    aliases_created = 0
    for tex_name in needed_textures:
        if tex_name in on_disk:
            continue  # already present — no alias needed

        # Strategy A: swap the first component before the first '_'
        # e.g. "lsl_dirt02" → try every on_disk key with the same suffix
        underscore = tex_name.find('_')
        if underscore > 0:
            suffix = tex_name[underscore:]   # e.g. "_dirt02"
            # Find any on-disk file that ends with this suffix
            for disk_stem, disk_path in on_disk.items():
                if disk_stem.endswith(suffix):
                    # Copy disk file as alias
                    ext = '.tga' if disk_path.lower().endswith('.tga') else '.tpc'
                    alias_path = os.path.join(extract_dir, tex_name + ext)
                    if not os.path.exists(alias_path):
                        try:
                            import shutil
                            shutil.copy2(disk_path, alias_path)
                            log.debug(
                                "Texture alias: '%s' → '%s'",
                                tex_name, os.path.basename(disk_path),
                            )
                            # Also add the alias to on_disk so we don't re-process
                            on_disk[tex_name] = alias_path
                            aliases_created += 1
                        except Exception as e:
                            log.debug("Texture alias copy failed: %s", e)
                    break

    if aliases_created:
        log.info(
            "Texture aliases: created %d alias file(s) in '%s'",
            aliases_created, extract_dir,
        )


def _synthesize_lyt_from_mdl_name(resref: str, world_x: float = 0.0,
                                   world_y: float = 0.0,
                                   world_z: float = 0.0) -> str:
    """
    Build a minimal KotOR LYT text for a module that has no .lyt resource.

    Uses the primary MDL resref as the single room.  This gives the viewport
    a valid RoomInstance to render even when the LYT wasn't shipped inside
    the archive (common in some editor-produced .mod files).
    """
    return (
        f"# Auto-synthesized LYT — no .lyt found in archive\n"
        f"roomcount 1\n"
        f"  {resref.lower()} {world_x:.4f} {world_y:.4f} {world_z:.4f}\n"
        f"trackcount 0\n"
        f"obstaclecount 0\n"
        f"doorhookcount 0\n"
        f"END\n"
    )


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ModuleLoadResult:
    """
    Plain data bag returned by ``ModuleIO``.

    Callers (``ModuleState``) inspect these fields and apply them to the
    live in-memory state.  The I/O layer never mutates ``ModuleState`` directly.
    """
    mod_path:    str = ""
    extract_dir: str = ""
    resref:      str = ""
    resources:   List[str] = field(default_factory=list)
    lyt_text:    Optional[str] = None
    vis_text:    Optional[str] = None
    errors:      List[str] = field(default_factory=list)

    # Loaded game-data objects (None if parse failed)
    git: object = None   # GITData | None
    are: object = None   # AREData | None
    ifo: object = None   # IFOData | None


# ── ModuleIO service ──────────────────────────────────────────────────────────

class ModuleIO:
    """
    Stateless service that loads KotOR module data from disk.

    Dependency on archive readers and GFF parsers lives *here*, keeping
    ``ModuleState`` free of format details.
    """

    # ── Public interface ──────────────────────────────────────────────────

    def load_from_files(
        self,
        git_path: str,
        are_path: str = "",
        ifo_path: str = "",
    ) -> "ModuleLoadResult":
        """Load GIT, ARE, and IFO directly from individual file paths.

        Replaces the inline loading code that was previously scattered inside
        ``ModuleState.load_from_files()``.  Returns a ``ModuleLoadResult``
        so the caller (``ModuleState``) can apply the data to live state
        without touching file I/O.
        """
        from ..formats.gff_reader import load_git, load_are, load_ifo
        from ..formats.gff_types import GITData, AREData, IFOData

        result = ModuleLoadResult()
        errors = result.errors

        # ── GIT (required) ────────────────────────────────────────────────
        try:
            result.git = load_git(git_path)
        except Exception as exc:
            log.error("GIT load error: %s", exc)
            errors.append(str(exc))
            result.git = GITData()

        # ── ARE (optional) ────────────────────────────────────────────────
        if are_path and os.path.exists(are_path):
            try:
                result.are = load_are(are_path)
            except Exception as exc:
                log.error("ARE load error: %s", exc)
                errors.append(str(exc))
                result.are = AREData()
        else:
            result.are = AREData()

        # ── IFO (optional) ────────────────────────────────────────────────
        if ifo_path and os.path.exists(ifo_path):
            try:
                result.ifo = load_ifo(ifo_path)
            except Exception as exc:
                log.error("IFO load error: %s", exc)
                errors.append(str(exc))
                result.ifo = IFOData()
        else:
            result.ifo = IFOData()

        log.info(
            "Module loaded from files: %d objects",
            result.git.object_count if result.git else 0,
        )
        return result

    def load_from_mod(
        self,
        mod_path: str,
        extract_dir: Optional[str] = None,
    ) -> ModuleLoadResult:
        """
        Load a KotOR .mod (ERF) archive and return a ``ModuleLoadResult``.

        Extracts GIT / ARE / IFO (and optionally LYT / VIS) into
        *extract_dir* (a sibling folder of the .mod by default).
        """
        from ..formats.archives import ERFReader
        from ..formats.gff_reader import load_git, load_are, load_ifo, GFFReader
        from ..formats.gff_types import GITData, AREData, IFOData

        result = ModuleLoadResult(mod_path=mod_path)
        errors = result.errors

        # 1 ── Open the archive ────────────────────────────────────────────
        erf = ERFReader(mod_path)
        count = erf.load()
        if count == 0:
            errors.append(f"No resources found in {mod_path}")
            log.error("MOD load: empty archive %s", mod_path)

        # 1b ── Content-signature remapping ───────────────────────────────
        # Some KotOR mod-building tools (e.g. the SLEM toolchain) store resources
        # with non-standard type IDs so the ERFReader maps them to unexpected
        # extensions (.bin→wok, .mod→are, .tlk→git, .rev→mdx).
        # We fix this by reading the first 8 bytes of each ambiguous resource
        # and matching known GFF/binary signatures.
        erf.resources = _remap_resources_by_signature(erf)

        result.resources = sorted(erf.resources.keys())

        # 2 ── Choose / create extraction directory ────────────────────────
        if extract_dir is None:
            mod_stem = Path(mod_path).stem
            base_dir = Path(mod_path).parent
            extract_dir = str(base_dir / f"_{mod_stem}_extracted")
        os.makedirs(extract_dir, exist_ok=True)
        result.extract_dir = extract_dir

        # ── helpers ───────────────────────────────────────────────────────

        def _extract(resref: str, ext: str):
            key = f"{resref.lower().strip()}.{ext}"
            entry = erf.resources.get(key)
            if entry is None:
                for k, e in erf.resources.items():
                    if k.lower() == key.lower():
                        entry = e
                        break
            return erf.read_resource(entry) if entry else None

        def _find_by_ext(ext: str):
            for k, e in erf.resources.items():
                if k.lower().endswith(f".{ext}"):
                    return k, e
            return None, None

        def _write(filename: str, data: bytes) -> str:
            p = os.path.join(extract_dir, filename)
            with open(p, "wb") as fh:
                fh.write(data)
            return p

        # 3 ── Determine resref from first .are ────────────────────────────
        resref = Path(mod_path).stem.lower()
        _are_key, _are_entry = _find_by_ext("are")
        if _are_key:
            resref = _are_key[:-4].strip()
            log.debug("MOD: area resref detected as '%s'", resref)
        result.resref = resref

        # 4 ── Load GIT ────────────────────────────────────────────────────
        git_data = _extract(resref, "git")
        if git_data is None:
            _git_key, _git_entry = _find_by_ext("git")
            if _git_entry:
                git_data = erf.read_resource(_git_entry)
                resref = _git_key[:-4].strip()
                result.resref = resref

        if git_data:
            git_path = _write(f"{resref}.git", git_data)
            try:
                result.git = load_git(git_path)
                log.info("MOD: GIT loaded — %d objects", result.git.object_count)
            except Exception as exc:
                errors.append(f"GIT parse error: {exc}")
                log.error("MOD GIT parse error: %s", exc)
                result.git = GITData()
        else:
            errors.append("No .git resource found in archive")
            log.warning(
                "MOD: No .git found; archive keys: %s",
                sorted(erf.resources.keys())[:10],
            )
            result.git = GITData()

        # 5 ── Load ARE ────────────────────────────────────────────────────
        are_data = _extract(resref, "are")
        if are_data is None and _are_entry:
            are_data = erf.read_resource(_are_entry)
        if are_data:
            are_path = _write(f"{resref}.are", are_data)
            try:
                result.are = load_are(are_path)
            except Exception as exc:
                errors.append(f"ARE parse error: {exc}")
                result.are = AREData()
        else:
            result.are = AREData()

        # 6 ── Load IFO ────────────────────────────────────────────────────
        ifo_data = _extract("module", "ifo") or _extract(resref, "ifo")
        if ifo_data is None:
            _, _ifo_entry = _find_by_ext("ifo")
            if _ifo_entry:
                ifo_data = erf.read_resource(_ifo_entry)

        if ifo_data:
            ifo_path = _write("module.ifo", ifo_data)
            try:
                result.ifo = load_ifo(ifo_path)
                if result.ifo and result.ifo.entry_area:
                    result.resref = result.ifo.entry_area.lower().strip()
            except Exception as exc:
                errors.append(f"IFO parse error: {exc}")
                result.ifo = IFOData()
        else:
            result.ifo = IFOData()

        # 7 ── Extract LYT / VIS ───────────────────────────────────────────
        lyt_resref = result.resref
        lyt_data   = _extract(lyt_resref, "lyt")
        if lyt_data is None:
            for key in erf.resources:
                if key.endswith(".lyt"):
                    lyt_data   = erf.read_resource(erf.resources[key])
                    lyt_resref = key[:-4]
                    break

        if lyt_data:
            lyt_text = lyt_data.decode("utf-8", errors="replace")
            result.lyt_text = lyt_text
            _write(f"{lyt_resref}.lyt", lyt_data)
            log.info("MOD: LYT extracted (%d chars)", len(lyt_text))
        else:
            # ── Synthesize LYT from MDL when no .lyt is present ──────────
            # Look for the primary MDL to determine the room model name.
            # Prefer an MDL whose resref matches the area resref, otherwise
            # use the first MDL that is NOT a "light" mesh (short resref ending in 'l').
            primary_mdl: Optional[str] = None
            for key in erf.resources:
                if key.endswith(".mdl"):
                    mdl_resref = key[:-4].lower()
                    if mdl_resref == lyt_resref.lower():
                        primary_mdl = mdl_resref
                        break
                    # Prefer longer (more complex) MDL over light/LOD variants
                    if primary_mdl is None:
                        primary_mdl = mdl_resref
                    elif len(mdl_resref) > len(primary_mdl):
                        primary_mdl = mdl_resref

            if primary_mdl:
                synth_lyt = _synthesize_lyt_from_mdl_name(primary_mdl)
                result.lyt_text = synth_lyt
                _write(f"{lyt_resref}.lyt", synth_lyt.encode("utf-8"))
                log.info(
                    "MOD: no .lyt found — synthesized from MDL '%s' (%d chars)",
                    primary_mdl, len(synth_lyt),
                )
            else:
                log.warning("MOD: no .lyt and no MDL found — viewport will be empty")

        vis_data = _extract(lyt_resref, "vis")
        if vis_data:
            result.vis_text = vis_data.decode("utf-8", errors="replace")
            _write(f"{lyt_resref}.vis", vis_data)

        # 8 ── Extract remaining resources ─────────────────────────────────
        for key, entry in erf.resources.items():
            dest = os.path.join(extract_dir, key)
            if not os.path.exists(dest):
                try:
                    raw = erf.read_resource(entry)
                    if raw:
                        with open(dest, "wb") as fh:
                            fh.write(raw)
                except Exception as exc:
                    log.debug("MOD extract %s: %s", key, exc)

        # 9 ── Texture alias remapping ────────────────────────────────────────
        # Some archives ship textures with a different name prefix than what
        # the MDL files reference.  For example, slem_ar.mod contains TGA files
        # named "sle_*" but its MDL nodes reference "lsl_*" textures (a common
        # SLEM-series naming convention).  We detect this mismatch by scanning
        # the primary MDL and creating alias copies so the viewport finds them.
        _create_texture_aliases(extract_dir, erf.resources)

        log.info(
            "MOD loaded: %s → %d objects, %d resources",
            mod_path,
            result.git.object_count if result.git else 0,
            len(result.resources),
        )
        return result
