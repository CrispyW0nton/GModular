"""MCP tools — resource discovery: list, describe, find, search."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game


# ── Tool schema definitions ────────────────────────────────────────────────

def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "listResources",
            "description": (
                "List resources from override/modules/chitin with optional "
                "location, type, resref and pagination filters. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "location": {
                        "type": "string",
                        "description": "override | modules | chitin | all | module:<name>",
                        "default": "all",
                    },
                    "moduleFilter": {"type": "string", "description": "Substring filter on module names"},
                    "resourceTypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Extensions to include (mdl, are, gff, dlg, …)",
                    },
                    "resrefQuery": {"type": "string", "description": "Case-insensitive substring filter"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
            },
        },
        {
            "name": "describeResource",
            "description": (
                "Fetch and summarise a single resource (GFF, 2DA, TLK). "
                "Returns metadata + structured analysis. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string"},
                    "restype": {"type": "string", "description": "File extension (e.g. gff, are, dlg)"},
                    "location": {"type": "string", "description": "override | modules | chitin | all", "default": "all"},
                },
                "required": ["game", "resref", "restype"],
            },
        },
        {
            "name": "kotor_find_resource",
            "description": (
                "Find a resource by resref (supports glob: 203tel*). "
                "Returns all matching entries ordered by priority. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "query": {
                        "type": "string",
                        "description": "resref with optional .ext, or glob (e.g. 203tel*)",
                    },
                    "all_locations": {
                        "type": "boolean",
                        "description": "Return all matching locations (default: true)",
                        "default": True,
                    },
                },
                "required": ["game", "query"],
            },
        },
        {
            "name": "kotor_search_resources",
            "description": (
                "Search resource names by regex pattern. Paginated. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "pattern": {"type": "string", "description": "Regex pattern for resref"},
                    "location": {"type": "string", "default": "all"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["game", "pattern"],
            },
        },
    ]


# ── Internal helpers ───────────────────────────────────────────────────────

def _entry_snapshot(entry: Any) -> Dict[str, Any]:
    return {
        "resref": entry.resref,
        "type": entry.ext.upper(),
        "extension": entry.ext,
        "size": entry.size,
        "source": entry.source,
        "filepath": str(entry.filepath),
        "inside_capsule": entry.inside_capsule,
    }


def _iter_entries(inst: Any, location: str, module_filter: Optional[str]) -> Any:
    """Yield ResourceEntry objects from the installation index."""
    idx = inst.index
    loc = location.lower()
    for key, entries_list in idx["by_key"].items():
        for entry in entries_list:
            src = entry.source
            if loc == "all":
                pass
            elif loc == "override":
                if src != "override":
                    continue
            elif loc == "chitin":
                if src != "chitin":
                    continue
            elif loc.startswith("module:"):
                target = loc[7:]
                if not src.lower().startswith("module:") or target not in src.lower():
                    continue
            elif loc == "modules":
                if not src.lower().startswith("module:"):
                    continue
                if module_filter and module_filter.lower() not in src.lower():
                    continue
            yield entry


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_list_resources(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game parameter (k1/k2).")
    inst = load_installation(game_key)

    location: str = arguments.get("location", "all")
    module_filter: Optional[str] = arguments.get("moduleFilter")
    type_filters = {t.lower().lstrip(".") for t in (arguments.get("resourceTypes") or [])}
    resref_query = (arguments.get("resrefQuery") or "").lower()
    limit = min(int(arguments.get("limit", 50)), 500)
    offset = int(arguments.get("offset", 0))

    results: List[Dict[str, Any]] = []
    skipped = 0

    for entry in _iter_entries(inst, location, module_filter):
        if resref_query and resref_query not in entry.resref:
            continue
        if type_filters and entry.ext.lower() not in type_filters:
            continue
        if skipped < offset:
            skipped += 1
            continue
        results.append(_entry_snapshot(entry))
        if len(results) >= limit:
            break

    has_more = len(results) == limit
    return json_content({
        "count": len(results),
        "offset": offset,
        "items": results,
        "has_more": has_more,
        "next_offset": offset + len(results) if has_more else None,
    })


async def handle_describe_resource(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game parameter (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()
    restype = (arguments.get("restype") or "").lower().lstrip(".")
    location: str = arguments.get("location", "all")

    idx = inst.index
    key = (resref, restype)
    entries_list = idx["by_key"].get(key, [])

    if not entries_list:
        raise ValueError(
            f"{resref}.{restype} not found. "
            "Try kotor_find_resource with a glob or kotor_search_resources."
        )

    # Priority: override > module > chitin
    _priority = {"override": 0, "chitin": 2}
    def _pri(e: Any) -> int:
        if e.source == "override":
            return 0
        if e.source.startswith("module:"):
            return 1
        return 2

    entry = sorted(entries_list, key=_pri)[0]
    data = _read_entry_data(entry)
    analysis = _analyse(restype, data)

    return json_content({
        "resref": resref,
        "type": restype.upper(),
        "extension": restype,
        "bytes": len(data),
        "source": entry.source,
        "filepath": str(entry.filepath),
        "analysis": analysis,
    })


async def handle_find_resource(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game parameter (k1/k2).")
    inst = load_installation(game_key)

    query = (arguments.get("query") or "").strip()
    all_locations = arguments.get("all_locations", True)

    # Parse resref and optional extension from query
    if "." in query and not query.startswith("."):
        parts = query.rsplit(".", 1)
        resref_part, ext_part = parts[0].lower(), parts[1].lower()
    else:
        resref_part, ext_part = query.lower(), None

    is_glob = "*" in resref_part or "?" in resref_part

    idx = inst.index
    matches: List[Dict[str, Any]] = []

    if is_glob:
        pattern = re.compile(
            "^" + re.escape(resref_part).replace(r"\*", ".*").replace(r"\?", ".") + "$"
        )
        for (rr, ext), entries_list in idx["by_key"].items():
            if not pattern.match(rr):
                continue
            if ext_part and ext != ext_part:
                continue
            for entry in entries_list:
                matches.append(_entry_snapshot(entry))
                if not all_locations:
                    break
    else:
        for (rr, ext), entries_list in idx["by_key"].items():
            if rr != resref_part:
                continue
            if ext_part and ext != ext_part:
                continue
            for entry in entries_list:
                matches.append(_entry_snapshot(entry))
                if not all_locations:
                    break

    return json_content({"count": len(matches), "matches": matches})


async def handle_search_resources(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game parameter (k1/k2).")
    inst = load_installation(game_key)

    pattern_str = arguments.get("pattern") or ""
    try:
        pattern_re = re.compile(pattern_str, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}") from e

    location: str = arguments.get("location", "all")
    limit = min(int(arguments.get("limit", 50)), 500)
    offset = int(arguments.get("offset", 0))

    items: List[Dict[str, Any]] = []
    skipped = 0

    for entry in _iter_entries(inst, location, None):
        if not pattern_re.search(entry.resref):
            continue
        if skipped < offset:
            skipped += 1
            continue
        items.append(_entry_snapshot(entry))
        if len(items) >= limit:
            break

    has_more = len(items) == limit
    return json_content({
        "count": len(items),
        "offset": offset,
        "items": items,
        "has_more": has_more,
        "next_offset": offset + len(items) if has_more else None,
    })


# ── Data extraction helpers ────────────────────────────────────────────────

def _read_entry_data(entry: Any) -> bytes:
    """Read raw bytes for a ResourceEntry (loose file, capsule slice, or BIF)."""
    if not entry.inside_capsule:
        return entry.filepath.read_bytes()

    # BIF-backed (chitin) entry — use KEYReader._read_bif via stored raw entry
    if entry.source == "chitin" and getattr(entry, "_key_entry", None) is not None:
        from gmodular.formats.archives import KEYReader
        data = KEYReader._read_bif(str(entry.filepath), entry.data_offset)
        return data or b""

    # ERF / RIM capsule: seek to offset and read size bytes
    if entry.data_length > 0:
        with open(entry.filepath, "rb") as fh:
            fh.seek(entry.data_offset)
            return fh.read(entry.data_length)

    return b""


def find_resource_bytes(inst: Any, resref: str, ext: str) -> bytes:
    """Return raw resource bytes from *inst*.

    Resolution order (matches pykotor / KotorMCP canonical order):
      1. pykotor Installation.resource() — OVERRIDE → MODULES → CHITIN
         (only used when inst.resource() returns actual bytes)
      2. gmodular ResourceManager — lazy KEY/BIF/override cache
      3. Raw index scan — catches module capsules not in the RM

    Raises ``ValueError`` if the resource cannot be found.

    This is the single canonical resource-fetch helper shared by all MCP
    tools (gamedata, conversion, refs, walkmesh).  Khononov §4.3: eliminates
    duplicate ``_find_resource_bytes`` implementations scattered across tools.
    """
    # Primary path: delegate to pykotor Installation when available.
    # We call inst.resource() and only trust the result if it is bytes or
    # bytearray — this guards against MagicMock-based tests where .resource()
    # returns a truthy MagicMock instead of None.
    try:
        pk_result = inst.resource(resref.lower(), ext.lower())
        if isinstance(pk_result, (bytes, bytearray)) and pk_result:
            return bytes(pk_result)
    except Exception:
        pass

    # Secondary path: gmodular ResourceManager (lazy-loads KEY/BIF/override once)
    try:
        rm = inst.resource_manager()
        fallback = rm.get_file(resref, ext)
        if fallback is not None:
            return fallback
    except Exception:
        pass

    # Tertiary path: raw index scan (catches module capsules not in the RM)
    try:
        idx = inst.index
        by_key = idx.get("by_key", {})
        entries = by_key.get((resref.lower(), ext.lower()), [])
        if entries:
            return _read_entry_data(entries[0])
    except Exception:
        pass

    raise ValueError(f"{resref}.{ext} not found in {inst.game} installation.")


def _analyse(ext: str, data: bytes) -> Dict[str, Any]:
    """Produce a lightweight analysis dict based on resource extension.

    Delegates to pykotor format readers when available (canonical library);
    falls back to GModular's own parsers for MDL (no pykotor MDL analyser)
    and for GFF when pykotor raises.  Plain-text formats (2DA, LYT) are parsed
    without external deps.
    """
    ext = ext.lower()
    if ext in ("gff", "are", "git", "ifo", "utc", "utd", "ute", "uti",
               "utp", "uts", "utm", "utt", "utw", "bic", "dlg", "fac",
               "jrl", "gui", "gic"):
        return _analyse_gff(data)
    if ext == "2da":
        return _analyse_2da(data)
    if ext == "tlk":
        return _analyse_tlk(data)
    if ext == "mdl":
        return _analyse_mdl(data)
    if ext in ("wok", "pwk", "dwk"):
        return _analyse_wok(data)
    if ext == "lyt":
        return _analyse_lyt(data)
    return {"size": len(data), "head_hex": data[:64].hex()}


def _analyse_gff(data: bytes) -> Dict[str, Any]:
    """Analyse GFF binary. Prefers pykotor (returns label/type/preview per field); falls back to gmodular reader."""
    # ── pykotor path ──────────────────────────────────────────────────────
    try:
        from io import BytesIO
        from pykotor.resource.formats.gff import read_gff
        gff = read_gff(BytesIO(data))
        root = gff.root
        fields: List[Dict[str, Any]] = []
        for label, field_type, value in root:
            preview: Any = value
            if isinstance(value, bytes):
                preview = f"<bytes:{len(value)}>"
            elif hasattr(value, "__len__") and len(str(value)) > 120:
                preview = f"{str(value)[:117]}..."
            fields.append({"label": label, "type": field_type.name, "preview": preview})
        return {
            "struct_id": root.struct_id,
            "field_count": len(root),
            "fields": fields[:20],
            "parser": "pykotor",
        }
    except Exception:
        pass
    # ── fallback ──────────────────────────────────────────────────────────
    try:
        from gmodular.formats.gff_reader import GFFReader
        reader = GFFReader(data)
        root = reader.parse()
        fields_list = [{"label": k, "type": "UNKNOWN", "preview": str(v)[:120]}
                       for k, v in list(root.fields.items())[:20]]
        return {
            "file_type": root.file_type,
            "version": root.file_version,
            "field_count": len(root.fields),
            "fields": fields_list,
            "parser": "gmodular",
        }
    except Exception as e:
        return {"error": str(e), "size": len(data)}


def _analyse_2da(data: bytes) -> Dict[str, Any]:
    """Analyse 2DA table. Prefers pykotor; falls back to text parse."""
    try:
        from io import BytesIO
        from pykotor.resource.formats.twoda import read_2da
        tda = read_2da(BytesIO(data))
        return {
            "columns": list(tda.get_headers()),
            "row_count": tda.get_height(),
            "parser": "pykotor",
        }
    except Exception:
        pass
    try:
        text = data.decode("latin-1", errors="replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return {"error": "too short", "size": len(data)}
        headers_line = lines[2] if len(lines) > 2 else lines[-1]
        return {
            "version": lines[0].strip(),
            "columns": headers_line.split(),
            "row_count": max(0, len(lines) - 3),
            "parser": "text",
        }
    except Exception as e:
        return {"error": str(e)}


def _analyse_tlk(data: bytes) -> Dict[str, Any]:
    """Analyse TLK talk table. Prefers pykotor (returns sample entries); falls back to gmodular reader."""
    try:
        from io import BytesIO
        from pykotor.resource.formats.tlk import read_tlk
        tlk = read_tlk(BytesIO(data))
        sample = []
        for strref, entry in list(tlk.strings.items())[:20]:
            sample.append({
                "strref": strref,
                "text": entry.text[:200],
                "sound": entry.sound,
            })
        return {
            "language": tlk.language.name,
            "string_count": len(tlk.strings),
            "sample": sample,
            "parser": "pykotor",
        }
    except Exception:
        pass
    try:
        from gmodular.formats.tlk_reader import TLKReader
        tlk = TLKReader.from_bytes(data)
        return {
            "language_id": tlk.language_id,
            "string_count": len(tlk.entries),
            "parser": "gmodular",
        }
    except Exception as e:
        return {"error": str(e)}


def _analyse_mdl(data: bytes) -> Dict[str, Any]:
    """Analyse MDL model. Uses gmodular parser (pykotor MDL is read-only bytes)."""
    try:
        from gmodular.formats.mdl_parser import MDLParser
        mesh_data = MDLParser(data, b"").parse()
        if mesh_data is None:
            return {"error": "parse failed"}
        nodes = mesh_data.all_nodes()
        return {
            "model_name": mesh_data.name,
            "node_count": len(nodes),
            "mesh_node_count": len(mesh_data.mesh_nodes()),
            "animation_count": len(mesh_data.animations),
            "parser": "gmodular",
        }
    except Exception as e:
        return {"error": str(e)}


def _analyse_wok(data: bytes) -> Dict[str, Any]:
    """Analyse WOK walkmesh. Prefers pykotor BWM reader; falls back to gmodular."""
    try:
        from io import BytesIO
        from pykotor.resource.formats.bwm import read_bwm
        bwm = read_bwm(BytesIO(data))
        return {
            "face_count": len(bwm.faces),
            "vertex_count": len(bwm.vertices),
            "walkable_faces": sum(1 for f in bwm.faces if f.walkable),
            "parser": "pykotor",
        }
    except Exception:
        pass
    try:
        from gmodular.formats.wok_parser import WOKParser
        wok = WOKParser.from_bytes(data)
        if wok is None:
            return {"error": "parse failed"}
        faces = wok.faces
        verts: set = set()
        for face in faces:
            verts.update([face.v0, face.v1, face.v2])
        return {
            "face_count": len(faces),
            "vertex_count": len(verts),
            "parser": "gmodular",
        }
    except Exception as e:
        return {"error": str(e)}


def _analyse_lyt(data: bytes) -> Dict[str, Any]:
    """Analyse LYT room layout (plain text; no external dep needed)."""
    try:
        text = data.decode("latin-1", errors="replace")
        rooms = [ln for ln in text.splitlines() if ln.strip().startswith("room")]
        return {"room_count": len(rooms)}
    except Exception as e:
        return {"error": str(e)}
