"""MCP tools — module operations: list, describe, resources."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game


def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_list_modules",
            "description": (
                "List all modules in the installation with their ARE area names. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                },
                "required": ["game"],
            },
        },
        {
            "name": "kotor_describe_module",
            "description": (
                "Full module analysis: ARE fields, room count, resource type "
                "breakdown, NCS script list. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "module_root": {
                        "type": "string",
                        "description": "Module root (e.g. 003ebo, danm13)",
                    },
                },
                "required": ["game", "module_root"],
            },
        },
        {
            "name": "kotor_module_resources",
            "description": (
                "Paginated list of all resources in a module composite "
                "(.rim + _s.rim + _dlg.erf). Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "module_root": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["game", "module_root"],
            },
        },
    ]


# ── Helpers ────────────────────────────────────────────────────────────────

def _module_entries(inst: Any, root: str) -> List[Any]:
    """Return all ResourceEntry objects belonging to modules whose name starts with *root*."""
    idx = inst.index
    root_lower = root.lower()
    result: List[Any] = []
    for source, entries in idx["by_source"].items():
        if not source.lower().startswith("module:"):
            continue
        # e.g. "module:003ebo.rim" → match when root_lower in filename stem
        capsule_name = source[7:]  # strip "module:"
        capsule_stem = Path(capsule_name).stem.lower()
        # match: exact stem OR stem starts with root OR root is prefix
        if capsule_stem == root_lower or capsule_stem.startswith(root_lower):
            result.extend(entries)
    return result


def _unique_modules(inst: Any) -> List[str]:
    """Return deduplicated module roots (stems without game-variant suffixes)."""
    idx = inst.index
    roots: set = set()
    for source in idx["by_source"]:
        if source.lower().startswith("module:"):
            capsule_name = source[7:]
            stem = Path(capsule_name).stem.lower()
            # Strip _s, _dlg suffixes
            base = re.sub(r"_(s|dlg)$", "", stem)
            roots.add(base)
    return sorted(roots)


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_list_modules(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    roots = _unique_modules(inst)
    modules_info: List[Dict[str, Any]] = []
    for root in roots:
        entries = _module_entries(inst, root)
        area_name = _get_area_name(inst, root, entries)
        caps = sorted({e.source[7:] for e in entries})
        modules_info.append({
            "module_root": root,
            "area_name": area_name or root,
            "files": caps,
        })

    return json_content({"count": len(modules_info), "modules": modules_info})


async def handle_describe_module(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    root = (arguments.get("module_root") or "").lower()
    entries = _module_entries(inst, root)
    if not entries:
        raise ValueError(f"Module '{root}' not found.")

    # Resource type counts + script list
    type_counts: Dict[str, int] = defaultdict(int)
    scripts: List[str] = []
    for e in entries:
        type_counts[e.ext.upper()] += 1
        if e.ext.lower() == "ncs":
            scripts.append(e.resref)

    # ARE analysis
    are_analysis: Optional[Dict[str, Any]] = None
    are_entries = [e for e in entries if e.ext.lower() == "are" and e.resref == root]
    if are_entries:
        try:
            from gmodular.mcp.tools.discovery import _read_entry_data
            from gmodular.formats.gff_reader import GFFReader
            data = _read_entry_data(are_entries[0])
            reader = GFFReader(data)
            rr = reader.parse()
            if rr:
                are_analysis = {
                    "file_type": rr.file_type,
                    "field_count": len(rr.fields),
                    "top_fields": list(rr.fields.keys())[:20],
                }
        except Exception as exc:
            are_analysis = {"error": str(exc)}

    # LYT room count
    lyt_count: Optional[int] = None
    lyt_entries = [e for e in entries if e.ext.lower() == "lyt" and e.resref == root]
    if lyt_entries:
        try:
            from gmodular.mcp.tools.discovery import _read_entry_data
            data = _read_entry_data(lyt_entries[0])
            text = data.decode("latin-1", errors="replace")
            lyt_count = sum(1 for ln in text.splitlines() if ln.strip().startswith("room"))
        except Exception:
            pass

    capsule_files = sorted({e.source[7:] for e in entries})
    return json_content({
        "module_root": root,
        "files": capsule_files,
        "are": are_analysis,
        "lyt_room_count": lyt_count,
        "resource_counts": dict(type_counts),
        "script_list": sorted(set(scripts))[:100],
    })


async def handle_module_resources(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    root = (arguments.get("module_root") or "").lower()
    limit = min(int(arguments.get("limit", 50)), 500)
    offset = int(arguments.get("offset", 0))

    entries = _module_entries(inst, root)
    if not entries:
        return json_content({"count": 0, "total": 0, "offset": 0, "items": [],
                             "has_more": False, "next_offset": None})

    total = len(entries)
    page = entries[offset: offset + limit]
    items = [
        {
            "resref": e.resref,
            "type": e.ext.upper(),
            "extension": e.ext,
            "size": e.size,
            "source_file": e.source[7:] if e.source.startswith("module:") else e.source,
        }
        for e in page
    ]
    has_more = total > offset + limit
    return json_content({
        "count": len(items),
        "total": total,
        "offset": offset,
        "items": items,
        "has_more": has_more,
        "next_offset": offset + len(items) if has_more else None,
    })


# ── Area name helper ───────────────────────────────────────────────────────

def _get_area_name(inst: Any, root: str, entries: List[Any]) -> Optional[str]:
    """Try to extract the area's display name from the ARE file."""
    are_matches = [e for e in entries if e.ext.lower() == "are" and e.resref == root]
    if not are_matches:
        return None
    try:
        from gmodular.mcp.tools.discovery import _read_entry_data
        from gmodular.formats.gff_reader import GFFReader
        data = _read_entry_data(are_matches[0])
        reader = GFFReader(data)
        rr = reader.parse()
        if rr is None:
            return None
        name_val = rr.get("Name")
        if name_val is not None:
            text = str(name_val)
            if text and text not in ("0", "None"):
                return text
    except Exception:
        pass
    return None
