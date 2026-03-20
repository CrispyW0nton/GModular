"""MCP tools — archive operations: list_archive, extract_resource."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game


def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_list_archive",
            "description": (
                "List contents of a KEY/BIF/RIM/ERF/MOD archive with pagination. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to archive file"},
                    "key_file": {"type": "string", "description": "Path to KEY file (for BIF)"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "kotor_extract_resource",
            "description": (
                "Write a resolved resource to disk. "
                "Optional 'source' restricts to one location. "
                "[destructiveHint: writes to disk]"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "Resource reference name"},
                    "restype": {"type": "string", "description": "Resource type extension"},
                    "output_path": {"type": "string", "description": "Output file or directory"},
                    "source": {
                        "type": "string",
                        "description": "override | modules | chitin (omit for first match)",
                    },
                },
                "required": ["game", "resref", "restype", "output_path"],
            },
        },
    ]


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_list_archive(arguments: Dict[str, Any]) -> Any:
    file_path = Path(arguments.get("file_path", ""))
    if not file_path.exists():
        raise ValueError(f"Archive not found: {file_path}")

    limit = min(int(arguments.get("limit", 50)), 500)
    offset = int(arguments.get("offset", 0))
    suffix = file_path.suffix.lower()
    items: List[Dict[str, Any]] = []

    from gmodular.formats.archives import RES_TYPE_MAP

    if suffix == ".key":
        # KEYReader takes a path string and iterates resources dict values
        from gmodular.formats.archives import KEYReader
        reader = KEYReader(str(file_path))
        reader.load()
        for _key, res in reader.resources.items():
            ext = RES_TYPE_MAP.get(res.res_type, "bin")
            items.append({
                "resref": res.resref,
                "type": ext.upper(),
                "extension": ext,
                "bif_path": res.bif_path or None,
                "size": res.size,
            })
    elif suffix == ".bif":
        # BIF resources are listed via the chitin.key that references them
        key_path_str = arguments.get("key_file")
        key_path = Path(key_path_str) if key_path_str else file_path.parent / "chitin.key"
        if not key_path.exists():
            raise ValueError(
                f"BIF listing requires a KEY file. Provide key_file or place chitin.key at {key_path}."
            )
        from gmodular.formats.archives import KEYReader
        reader = KEYReader(str(key_path))
        reader.load()
        bif_name_lower = file_path.name.lower()
        for _key, res in reader.resources.items():
            if bif_name_lower not in res.bif_path.lower():
                continue
            ext = RES_TYPE_MAP.get(res.res_type, "bin")
            items.append({
                "resref": res.resref,
                "type": ext.upper(),
                "extension": ext,
                "size": res.size,
            })
    elif suffix in (".rim", ".erf", ".mod", ".sav", ".hak"):
        # ERFReader handles both RIM and ERF/MOD formats
        from gmodular.formats.archives import ERFReader
        reader = ERFReader(str(file_path))
        reader.load()
        for _key, res in reader.resources.items():
            ext = RES_TYPE_MAP.get(res.res_type, "bin")
            items.append({
                "resref": res.resref,
                "type": ext.upper(),
                "extension": ext,
                "size": res.size,
            })
    else:
        raise ValueError(
            f"Unsupported archive type: {suffix}. "
            "Supported: .key, .bif, .rim, .erf, .mod, .sav, .hak"
        )

    total = len(items)
    page = items[offset: offset + limit]
    has_more = total > offset + limit
    return json_content({
        "total": total,
        "count": len(page),
        "offset": offset,
        "items": page,
        "has_more": has_more,
        "next_offset": offset + len(page) if has_more else None,
    })


async def handle_extract_resource(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()
    restype = (arguments.get("restype") or "").lower().lstrip(".")
    out_path = Path(arguments.get("output_path", "."))
    source_filter = (arguments.get("source") or "").lower()

    idx = inst.index
    key = (resref, restype)
    entries = idx["by_key"].get(key, [])
    if not entries:
        raise ValueError(
            f"{resref}.{restype} not found. "
            "Try kotor_find_resource with a glob pattern."
        )

    # Filter by source if requested
    if source_filter:
        filtered = [e for e in entries if source_filter in e.source.lower()]
        if filtered:
            entries = filtered

    # Pick highest priority
    def _pri(e: Any) -> int:
        if e.source == "override":
            return 0
        if e.source.startswith("module:"):
            return 1
        return 2

    entry = sorted(entries, key=_pri)[0]

    from gmodular.mcp.tools.discovery import _read_entry_data
    data = _read_entry_data(entry)

    if out_path.is_dir():
        out_path = out_path / f"{resref}.{restype}"
    elif out_path.suffix.lower() != f".{restype}":
        out_path = out_path.with_suffix(f".{restype}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)

    return json_content({
        "status": "ok",
        "path": str(out_path),
        "bytes": len(data),
        "source": entry.source,
    })
