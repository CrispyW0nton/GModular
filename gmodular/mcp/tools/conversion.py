"""MCP tools — format conversion: read_gff, read_2da, read_tlk."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game


def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_read_gff",
            "description": (
                "Return a GFF resource (ARE, UTC, DLG, …) as a JSON tree. "
                "Use max_depth/max_fields to stay under response limits. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string"},
                    "restype": {"type": "string", "description": "Extension (dlg, utc, are, …)"},
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 20},
                    "max_fields": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "required": ["game", "resref", "restype"],
            },
        },
        {
            "name": "kotor_read_2da",
            "description": (
                "Return a 2DA table as JSON with optional row range and column filter. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string", "description": "2DA table name (e.g. appearance)"},
                    "row_start": {"type": "integer", "minimum": 0},
                    "row_end": {"type": "integer", "minimum": 0},
                    "columns": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "kotor_read_tlk",
            "description": (
                "Return TLK (dialog.tlk) entries by strref range or text search. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "strref_start": {"type": "integer", "minimum": 0},
                    "strref_end": {"type": "integer", "minimum": 0},
                    "text_search": {"type": "string", "description": "Substring search"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                },
                "required": ["game"],
            },
        },
    ]


# ── Helpers ────────────────────────────────────────────────────────────────

def _find_resource_bytes(inst: Any, resref: str, ext: str) -> bytes:
    """Thin shim → delegates to the canonical ``find_resource_bytes`` in discovery."""
    from gmodular.mcp.tools.discovery import find_resource_bytes
    return find_resource_bytes(inst, resref, ext)


def _gff_to_dict(struct: Any, max_depth: Optional[int], max_fields: Optional[int],
                 depth: int, counter: List[int]) -> Dict[str, Any]:
    """Recursively convert a GFFStruct to a plain dict."""
    out: Dict[str, Any] = {}
    for label, gff_field in struct.fields.items():
        # GFFStruct.fields maps label -> GFFField; extract .value
        value = gff_field.value if hasattr(gff_field, "value") else gff_field
        if max_fields is not None and counter[0] >= max_fields:
            out["_truncated"] = True
            break
        counter[0] += 1
        if hasattr(value, "fields"):  # nested GFFStruct
            if max_depth is not None and depth + 1 > max_depth:
                out[label] = "<struct, max_depth>"
            else:
                out[label] = _gff_to_dict(value, max_depth, max_fields, depth + 1, counter)
        elif isinstance(value, list) and value and hasattr(value[0], "fields"):
            if max_depth is not None and depth + 1 > max_depth:
                out[label] = f"<list[{len(value)}], max_depth>"
            else:
                out[label] = [
                    _gff_to_dict(item, max_depth, max_fields, depth + 1, counter)
                    for item in value
                ]
        elif isinstance(value, bytes):
            out[label] = f"<bytes:{len(value)}>"
        else:
            out[label] = value
    return out


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_read_gff(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()
    restype = (arguments.get("restype") or "gff").lower().lstrip(".")
    max_depth: Optional[int] = arguments.get("max_depth")
    max_fields: Optional[int] = arguments.get("max_fields")

    data = _find_resource_bytes(inst, resref, restype)
    from gmodular.formats.gff_reader import GFFReader
    reader = GFFReader(data)
    root = reader.parse()
    if root is None:
        raise ValueError(f"Failed to parse {resref}.{restype} as GFF.")

    counter = [0]
    tree = _gff_to_dict(root, max_depth, max_fields, 0, counter)

    return json_content({
        "resref": resref,
        "restype": restype.upper(),
        "file_type": root.file_type,
        "truncated": max_fields is not None and counter[0] >= max_fields,
        "root": tree,
    })


async def handle_read_2da(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()
    row_start = int(arguments.get("row_start") or 0)
    row_end_arg = arguments.get("row_end")
    col_filter = arguments.get("columns")

    data = _find_resource_bytes(inst, resref, "2da")
    text = data.decode("latin-1", errors="replace")
    from gmodular.formats.twoda_loader import _parse_2da
    table = _parse_2da(text, resref)
    if table is None:
        raise ValueError(f"Failed to parse {resref}.2da.")

    columns = table.columns
    if col_filter:
        columns = [c for c in columns if c in col_filter]

    total = len(table.rows)
    row_end = min(int(row_end_arg) if row_end_arg is not None else total, total)
    rows: List[Dict[str, str]] = []
    for i in range(row_start, row_end):
        row = table.rows.get(i, {})
        rows.append({c: row.get(c, "") for c in columns})

    return json_content({
        "resref": resref,
        "columns": columns,
        "total_rows": total,
        "row_start": row_start,
        "row_count": len(rows),
        "rows": rows,
    })


async def handle_read_tlk(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    tlk_path = inst.tlk_path()
    if tlk_path is None:
        raise ValueError("dialog.tlk not found.")

    data = tlk_path.read_bytes()
    from gmodular.formats.tlk_reader import TLKReader
    tlk = TLKReader.from_bytes(data)

    limit = min(int(arguments.get("limit") or 100), 500)
    text_search: Optional[str] = arguments.get("text_search")
    strref_start = int(arguments.get("strref_start") or 0)
    strref_end = arguments.get("strref_end")
    total_entries = len(tlk.entries)
    strref_end_val = int(strref_end) if strref_end is not None else total_entries

    entries_out: List[Dict[str, Any]] = []

    if text_search:
        search_lower = text_search.lower()
        for i, entry in enumerate(tlk.entries):
            if len(entries_out) >= limit:
                break
            if search_lower in entry.text.lower():
                entries_out.append({
                    "strref": i,
                    "text": entry.text[:500],
                    "sound_resref": entry.sound_resref,
                })
    else:
        for i in range(strref_start, min(strref_start + limit, strref_end_val)):
            if i < total_entries:
                entry = tlk.entries[i]
                entries_out.append({
                    "strref": i,
                    "text": entry.text[:500],
                    "sound_resref": entry.sound_resref,
                })

    return json_content({
        "language_id": tlk.language_id,
        "total_entries": total_entries,
        "count": len(entries_out),
        "entries": entries_out,
    })
