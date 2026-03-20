"""MCP tools — game data: journal overview, 2DA lookup, TLK lookup."""
from __future__ import annotations

from typing import Any, Dict, List

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game


def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "journalOverview",
            "description": (
                "Return a summary of global.jrl plot categories and quest entries "
                "for the loaded installation. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "Game alias: k1 or k2"},
                },
                "required": ["game"],
            },
        },
        {
            "name": "kotor_lookup_2da",
            "description": (
                "Query a 2DA table by row index, column name, or value search. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "table_name": {"type": "string", "description": "2DA resref (e.g. appearance)"},
                    "row_index": {"type": "integer", "minimum": 0},
                    "column": {"type": "string"},
                    "value_search": {"type": "string"},
                },
                "required": ["game", "table_name"],
            },
        },
        {
            "name": "kotor_lookup_tlk",
            "description": (
                "Resolve a TLK strref to its display text from dialog.tlk. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "strref": {"type": "integer", "description": "TLK string reference ID"},
                },
                "required": ["game", "strref"],
            },
        },
    ]


# ── Helpers ────────────────────────────────────────────────────────────────

def _find_resource_bytes(inst: Any, resref: str, ext: str) -> bytes:
    """Thin shim → delegates to the canonical ``find_resource_bytes`` in discovery.

    Kept for backward compatibility; new code should import and call
    ``find_resource_bytes`` from ``gmodular.mcp.tools.discovery`` directly.
    """
    from gmodular.mcp.tools.discovery import find_resource_bytes
    return find_resource_bytes(inst, resref, ext)


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_journal_overview(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game parameter (k1/k2).")
    inst = load_installation(game_key)

    data = _find_resource_bytes(inst, "global", "jrl")
    from gmodular.formats.gff_reader import GFFReader
    reader = GFFReader(data)
    root = reader.parse()
    if root is None:
        raise ValueError("Failed to parse global.jrl.")

    # Walk GFF structure for journal categories / entries
    categories: List[Dict[str, Any]] = []
    cat_list = root.get("Categories") or []
    for cat_struct in (cat_list if isinstance(cat_list, list) else []):
        cat: Dict[str, Any] = {
            "name": cat_struct.get("Name") or "",
            "tag": cat_struct.get("Tag") or "",
            "comment": cat_struct.get("Comment") or "",
            "priority": cat_struct.get("Priority") or 0,
            "entries": [],
        }
        entry_list = cat_struct.get("EntryList") or []
        for quest in (entry_list if isinstance(entry_list, list) else []):
            cat["entries"].append({
                "id": quest.get("ID") or 0,
                "text": str(quest.get("Text") or "")[:400],
                "comment": quest.get("Comment") or "",
                "completes_plot": bool(quest.get("End") or 0),
            })
        categories.append(cat)

    return json_content({"count": len(categories), "categories": categories})


async def handle_lookup_2da(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    table_name: str = arguments.get("table_name", "")
    row_index = arguments.get("row_index")
    column = arguments.get("column")
    value_search = arguments.get("value_search")

    data = _find_resource_bytes(inst, table_name, "2da")
    text = data.decode("latin-1", errors="replace")
    from gmodular.formats.twoda_loader import _parse_2da
    table = _parse_2da(text, table_name)
    if table is None:
        raise ValueError(f"Failed to parse {table_name}.2da.")

    if row_index is not None:
        row = table.rows.get(int(row_index))
        if row is None:
            raise ValueError(f"Row {row_index} out of range (table has {len(table.rows)} rows).")
        return json_content({"table": table_name, "row_index": row_index, "row": row})

    if value_search and column:
        matches = []
        for idx, row in table.rows.items():
            val = row.get(column, "")
            if value_search.lower() in val.lower():
                matches.append({"row_index": idx, column: val})
                if len(matches) >= 50:
                    break
        return json_content({
            "table": table_name, "column": column,
            "value_search": value_search, "matches": matches,
        })

    return json_content({
        "table": table_name,
        "columns": table.columns,
        "row_count": len(table.rows),
    })


async def handle_lookup_tlk(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    strref = int(arguments.get("strref", 0))

    # Primary: use pykotor talktable (lazy-cached, no full read-bytes)
    pk = inst.pykotor_inst
    if pk is not None:
        try:
            text = pk.talktable().string(strref)
            return json_content({"strref": strref, "text": text})
        except Exception:
            pass  # fall through to gmodular TLKReader

    # Fallback: gmodular TLKReader
    tlk_path = inst.tlk_path()
    if tlk_path is None:
        raise ValueError("dialog.tlk not found in installation.")
    data = tlk_path.read_bytes()
    from gmodular.formats.tlk_reader import TLKReader
    tlk = TLKReader.from_bytes(data)
    if strref < 0 or strref >= len(tlk.entries):
        raise ValueError(f"strref {strref} out of range (tlk has {len(tlk.entries)} entries).")
    entry = tlk.entries[strref]
    return json_content({
        "strref": strref,
        "text": entry.text,
        "sound_resref": entry.sound_resref,
        "sound_length": entry.sound_length,
    })


# ── GFF field helpers ──────────────────────────────────────────────────────

def _gff_str(struct: Any, label: str) -> str:
    val = struct.fields.get(label, "")
    if hasattr(val, "text"):
        return val.text or ""
    return str(val) if val is not None else ""


def _gff_int(struct: Any, label: str) -> int:
    val = struct.fields.get(label, 0)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0
