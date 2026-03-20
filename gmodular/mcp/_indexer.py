"""GModular MCP — installation resource indexer.

Scans a KotOR game directory and builds a flat index of all discoverable
resources across override/, modules/, and chitin.key/BIF.

The index maps (resref, ext) → list[ResourceEntry], ordered by priority
(override first, then modules, then chitin).

Khononov §4.3 note: MCP tools that only need to *read* resources should use
``KotorInstallation.resource_manager()`` (which wraps the canonical
``formats.archives.ResourceManager``) rather than calling ``build_index()``
directly.  ``build_index()`` is retained for tools that need the full flat
listing (e.g. ``listResources`` with pagination over all capsule entries).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass
class ResourceEntry:
    """Single located resource."""
    resref: str           # lower-case, no extension
    ext: str              # lower-case, no dot
    source: str           # 'override' | 'module:<capsule_name>' | 'chitin'
    filepath: Path        # host file
    size: int = 0
    inside_capsule: bool = False
    data_offset: int = 0   # byte offset inside capsule (ERF/RIM)
    data_length: int = 0   # byte length inside capsule
    # for KEY/BIF: raw ResourceEntry from KEYReader
    _key_entry: Any = field(default=None, repr=False)


def build_index(game_path: Path) -> Dict[str, Any]:
    """Build a resource index for the game installation at *game_path*.

    Returns a dict:
        'by_key'    : Dict[(resref, ext)] -> List[ResourceEntry]
        'by_source' : Dict[source_label]  -> List[ResourceEntry]
        'path'      : game_path
    """
    by_key: Dict[Tuple[str, str], List[ResourceEntry]] = {}
    by_source: Dict[str, List[ResourceEntry]] = {}

    def _add(entry: ResourceEntry) -> None:
        k = (entry.resref, entry.ext)
        by_key.setdefault(k, []).append(entry)
        by_source.setdefault(entry.source, []).append(entry)

    # 1. Override (highest priority) ──────────────────────────────────────
    override_dir = game_path / "override"
    if override_dir.is_dir():
        for f in sorted(override_dir.iterdir()):
            if f.is_file() and "." in f.name:
                stem = f.stem.lower()
                ext = f.suffix.lstrip(".").lower()
                _add(ResourceEntry(
                    resref=stem,
                    ext=ext,
                    source="override",
                    filepath=f,
                    size=f.stat().st_size,
                    inside_capsule=False,
                ))

    # 2. Module capsules ───────────────────────────────────────────────────
    modules_dir = game_path / "modules"
    if modules_dir.is_dir():
        for capsule in sorted(modules_dir.iterdir()):
            if capsule.suffix.lower() not in (".rim", ".erf", ".mod"):
                continue
            source_label = f"module:{capsule.name}"
            try:
                for entry in _index_capsule(capsule, source_label):
                    _add(entry)
            except Exception as exc:
                log.debug("MCP indexer: skipping %s: %s", capsule.name, exc)

    # 3. KEY / BIF ─────────────────────────────────────────────────────────
    key_file = game_path / "chitin.key"
    if key_file.exists():
        try:
            for entry in _index_key(key_file):
                _add(entry)
        except Exception as exc:
            log.debug("MCP indexer: chitin.key failed: %s", exc)

    return {"by_key": by_key, "by_source": by_source, "path": game_path}


# ── Capsule indexer ────────────────────────────────────────────────────────

def _index_capsule(capsule_path: Path, source: str) -> List[ResourceEntry]:
    """Index an ERF / RIM / MOD capsule using gmodular.formats.archives."""
    from gmodular.formats.archives import ERFReader

    entries: List[ResourceEntry] = []
    reader = ERFReader(str(capsule_path))
    reader.load()

    for _key, res_entry in reader.resources.items():
        from gmodular.formats.archives import RES_TYPE_MAP
        ext = RES_TYPE_MAP.get(res_entry.res_type, "bin")
        entries.append(ResourceEntry(
            resref=res_entry.resref.lower(),
            ext=ext,
            source=source,
            filepath=capsule_path,
            size=res_entry.size,
            inside_capsule=True,
            data_offset=res_entry.offset,
            data_length=res_entry.size,
        ))
    return entries


# ── KEY indexer ────────────────────────────────────────────────────────────

def _index_key(key_path: Path) -> List[ResourceEntry]:
    """Index resources listed in chitin.key (BIF-backed)."""
    from gmodular.formats.archives import KEYReader, RES_TYPE_MAP

    reader = KEYReader(str(key_path))
    reader.load()

    entries: List[ResourceEntry] = []
    for _key, res_entry in reader.resources.items():
        ext = RES_TYPE_MAP.get(res_entry.res_type, "bin")
        bif_path = Path(res_entry.bif_path) if res_entry.bif_path else key_path
        entries.append(ResourceEntry(
            resref=res_entry.resref.lower(),
            ext=ext,
            source="chitin",
            filepath=bif_path,
            size=0,            # size only known at BIF read time
            inside_capsule=True,
            data_offset=res_entry.offset,  # BIF resource index
            data_length=0,
            _key_entry=res_entry,
        ))
    return entries
