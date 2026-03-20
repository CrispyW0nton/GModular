"""MCP tools — GFF references: list outbound refs, find referrers, describe DLG/JRL."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game


def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "kotor_list_references",
            "description": (
                "List outbound references (scripts, tags, conversations, template "
                "resrefs) from a GFF resource. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string"},
                    "restype": {"type": "string", "description": "GFF type (dlg, utc, are, …)"},
                },
                "required": ["game", "resref", "restype"],
            },
        },
        {
            "name": "kotor_find_referrers",
            "description": (
                "Find which resources reference a given script/tag/conversation/resref. "
                "Optionally scoped to a module. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "value": {"type": "string", "description": "Value to search for"},
                    "reference_kind": {
                        "type": "string",
                        "enum": ["script", "tag", "conversation", "resref"],
                        "default": "resref",
                    },
                    "module_root": {"type": "string", "description": "Limit to module"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["game", "value"],
            },
        },
        {
            "name": "kotor_describe_dlg",
            "description": (
                "DLG (conversation) summary: entry/reply counts, script/condition refs. "
                "Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string", "description": "DLG resource reference"},
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "kotor_describe_jrl",
            "description": (
                "JRL (journal) summary: categories and entry counts. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string", "description": "JRL resref (e.g. global)"},
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "kotor_find_strref_referrers",
            "description": (
                "Find all GFF resources that reference a TLK strref (integer). "
                "Optionally scoped to a module. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "strref": {"type": "integer", "description": "TLK string reference ID"},
                    "module_root": {"type": "string", "description": "Limit to module (optional)"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["game", "strref"],
            },
        },
        {
            "name": "kotor_describe_resource_refs",
            "description": (
                "Generic GFF reference summary: all script/tag/conversation/resref "
                "fields found in a resource, grouped by kind. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string"},
                    "resref": {"type": "string"},
                    "restype": {"type": "string", "description": "GFF extension (utc, dlg, are, …)"},
                },
                "required": ["game", "resref", "restype"],
            },
        },
    ]


# ── GFF field extraction helpers ───────────────────────────────────────────

# Fields that typically contain script resrefs
_SCRIPT_FIELDS = {
    "OnEnter", "OnExit", "OnHeartbeat", "OnOpen", "OnClose",
    "OnFailToOpen", "OnClick", "OnDamaged", "OnDeath", "OnDisarm",
    "OnEndDialogue", "OnSpellCastAt", "OnLock", "OnUnlock",
    "OnUsed", "OnUserDefined", "Script",
    "ScriptOnEnter", "ScriptOnExit",
    # DLG
    "Script1", "Script2", "Active", "Active2", "ActivePair",
    "Conditional", "ActionParam1", "ActionParam2",
}

_TAG_FIELDS = {"Tag", "LocalizedName"}
_CONV_FIELDS = {"Conversation", "DialogResRef", "DlgFile"}
_RESREF_FIELDS = {"TemplateResRef", "Blueprint", "ResRef"}


def _extract_refs(struct: Any, depth: int = 0) -> List[Dict[str, str]]:
    """Walk GFF struct and collect reference-typed fields."""
    if depth > 12:
        return []
    refs: List[Dict[str, str]] = []
    if struct is None or not hasattr(struct, "fields"):
        return refs
    for label, gff_field in struct.fields.items():
        # GFFStruct.fields maps label -> GFFField; get the .value
        value = gff_field.value if hasattr(gff_field, "value") else gff_field
        if label in _SCRIPT_FIELDS and isinstance(value, str) and value.strip():
            refs.append({"ref_kind": "script", "value": value.strip(), "field": label})
        elif label in _CONV_FIELDS and isinstance(value, str) and value.strip():
            refs.append({"ref_kind": "conversation", "value": value.strip(), "field": label})
        elif label in _TAG_FIELDS and isinstance(value, str) and value.strip():
            refs.append({"ref_kind": "tag", "value": value.strip(), "field": label})
        elif label in _RESREF_FIELDS and isinstance(value, str) and value.strip():
            refs.append({"ref_kind": "resref", "value": value.strip(), "field": label})
        # Recurse into nested structs and lists
        elif hasattr(value, "fields"):
            refs.extend(_extract_refs(value, depth + 1))
        elif isinstance(value, list):
            for item in value:
                if hasattr(item, "fields"):
                    refs.extend(_extract_refs(item, depth + 1))
    return refs


def _find_resource_bytes(inst: Any, resref: str, ext: str) -> bytes:
    """Thin shim → delegates to the canonical ``find_resource_bytes`` in discovery."""
    from gmodular.mcp.tools.discovery import find_resource_bytes
    return find_resource_bytes(inst, resref, ext)


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_list_references(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()
    restype = (arguments.get("restype") or "gff").lower().lstrip(".")

    data = _find_resource_bytes(inst, resref, restype)
    from gmodular.formats.gff_reader import GFFReader
    reader = GFFReader(data)
    root = reader.parse()
    if root is None:
        raise ValueError(f"Failed to parse {resref}.{restype}.")

    refs = _extract_refs(root)
    return json_content({
        "resref": resref,
        "restype": restype,
        "count": len(refs),
        "references": refs,
    })


async def handle_find_referrers(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    value = (arguments.get("value") or "")
    kind = (arguments.get("reference_kind") or "resref").lower()
    module_root: Optional[str] = arguments.get("module_root")
    partial_match: bool = bool(arguments.get("partial_match", False))
    limit = min(int(arguments.get("limit", 100)), 500)
    offset = int(arguments.get("offset", 0))

    # Primary: use pykotor.tools.references.find_referrers when available
    pk = inst.pykotor_inst
    if pk is not None:
        try:
            from pykotor.tools.references import find_referrers
            results = find_referrers(
                pk,
                value,
                reference_kind=kind,
                module_root=module_root or None,
                partial_match=partial_match,
                file_types=None,
            )
            total = len(results)
            page = results[offset: offset + limit]
            items = [
                {
                    "resref": r.file_resource.resname(),
                    "restype": r.file_resource.restype().name,
                    "field_path": r.field_path,
                    "matched_value": r.matched_value,
                    "filepath": str(r.file_resource.filepath()),
                }
                for r in page
            ]
            return json_content({
                "count": len(items),
                "total": total,
                "offset": offset,
                "has_more": total > offset + limit,
                "items": items,
            })
        except Exception:
            pass  # fall through to manual scan below

    # Fallback: manual GFF index scan (no pykotor)
    value_lower = value.lower()
    GFF_EXTS = {"are", "git", "ifo", "utc", "utd", "ute", "uti", "utp",
                "uts", "utm", "utt", "utw", "bic", "dlg", "fac", "jrl",
                "gff", "gui", "gic"}

    idx = inst.index
    from gmodular.formats.gff_reader import GFFReader
    from gmodular.mcp.tools.discovery import _read_entry_data

    matches: List[Dict[str, Any]] = []

    for (resref, ext), entries in idx["by_key"].items():
        if ext.lower() not in GFF_EXTS:
            continue
        entry = entries[0]
        src_lower = entry.source.lower()
        if module_root and not (src_lower.startswith("module:") and module_root.lower() in src_lower):
            continue
        try:
            data = _read_entry_data(entry)
            reader = GFFReader(data)
            root = reader.parse()
            if root is None:
                continue
            refs = _extract_refs(root)
            for ref in refs:
                if ref["ref_kind"] != kind:
                    continue
                ref_val = ref["value"].lower()
                hit = (ref_val == value_lower) or (partial_match and value_lower in ref_val)
                if hit:
                    matches.append({
                        "resref": resref,
                        "restype": ext.upper(),
                        "field_path": ref["field"],
                        "matched_value": ref["value"],
                        "filepath": str(entry.filepath),
                    })
        except Exception:
            continue

    total = len(matches)
    page = matches[offset: offset + limit]
    return json_content({
        "count": len(page),
        "total": total,
        "offset": offset,
        "has_more": total > offset + limit,
        "items": page,
    })


async def handle_describe_dlg(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()
    data = _find_resource_bytes(inst, resref, "dlg")
    from gmodular.formats.gff_reader import GFFReader
    reader = GFFReader(data)
    root = reader.parse()
    if root is None:
        raise ValueError(f"Failed to parse {resref}.dlg.")

    entry_list = root.get("EntryList") or []
    reply_list = root.get("ReplyList") or []
    entry_count = len(entry_list) if isinstance(entry_list, list) else 0
    reply_count = len(reply_list) if isinstance(reply_list, list) else 0

    refs = _extract_refs(root)
    scripts = list({r["value"] for r in refs if r["ref_kind"] == "script"})
    convs = list({r["value"] for r in refs if r["ref_kind"] == "conversation"})

    return json_content({
        "resref": resref,
        "entry_count": entry_count,
        "reply_count": reply_count,
        "script_refs": scripts[:50],
        "conversation_refs": convs[:20],
        "reference_count": len(refs),
    })


async def handle_describe_jrl(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "global").lower()
    data = _find_resource_bytes(inst, resref, "jrl")
    from gmodular.formats.gff_reader import GFFReader
    reader = GFFReader(data)
    root = reader.parse()
    if root is None:
        raise ValueError(f"Failed to parse {resref}.jrl.")

    categories = root.get("Categories") or []
    cat_count = len(categories) if isinstance(categories, list) else 0
    entries = root.get("EntryList") or []
    entry_count = len(entries) if isinstance(entries, list) else 0

    return json_content({
        "resref": resref,
        "category_count": cat_count,
        "entry_count": entry_count,
    })


# ── strref referrers ────────────────────────────────────────────────────────

def _extract_strrefs(struct: Any, depth: int = 0) -> List[int]:
    """Walk GFF struct recursively and collect all integer strref-like values."""
    if depth > 12 or struct is None or not hasattr(struct, "fields"):
        return []
    strrefs: List[int] = []
    for label, gff_field in struct.fields.items():
        value = gff_field.value if hasattr(gff_field, "value") else gff_field
        # CExoLocString fields often carry strref as an integer or dict
        if isinstance(value, int) and 0 <= value < 0xFFFFFF:
            strrefs.append(value)
        elif hasattr(value, "fields"):
            strrefs.extend(_extract_strrefs(value, depth + 1))
        elif isinstance(value, list):
            for item in value:
                if hasattr(item, "fields"):
                    strrefs.extend(_extract_strrefs(item, depth + 1))
    return strrefs


async def handle_find_strref_referrers(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    target_strref = int(arguments.get("strref", 0))
    module_root: Optional[str] = arguments.get("module_root")
    limit = min(int(arguments.get("limit", 100)), 500)
    offset = int(arguments.get("offset", 0))

    GFF_EXTS = {"are", "git", "ifo", "utc", "utd", "ute", "uti", "utp",
                "uts", "utm", "utt", "utw", "bic", "dlg", "fac", "jrl",
                "gff", "gui", "gic"}

    idx = inst.index
    from gmodular.formats.gff_reader import GFFReader
    from gmodular.mcp.tools.discovery import _read_entry_data

    matches: List[Dict[str, Any]] = []

    for (resref, ext), entries in idx["by_key"].items():
        if ext.lower() not in GFF_EXTS:
            continue
        entry = entries[0]
        src_lower = entry.source.lower()
        if module_root and not (src_lower.startswith("module:") and module_root.lower() in src_lower):
            continue
        try:
            data = _read_entry_data(entry)
            reader = GFFReader(data)
            root = reader.parse()
            if root is None:
                continue
            strrefs = _extract_strrefs(root)
            if target_strref in strrefs:
                matches.append({
                    "resref": resref,
                    "restype": ext.upper(),
                    "source": entry.source,
                    "filepath": str(entry.filepath),
                })
        except Exception:
            continue

    total = len(matches)
    page = matches[offset: offset + limit]
    return json_content({
        "strref": target_strref,
        "count": len(page),
        "total": total,
        "offset": offset,
        "has_more": total > offset + limit,
        "items": page,
    })


async def handle_describe_resource_refs(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower()
    restype = (arguments.get("restype") or "gff").lower().lstrip(".")

    data = _find_resource_bytes(inst, resref, restype)
    from gmodular.formats.gff_reader import GFFReader
    reader = GFFReader(data)
    root = reader.parse()
    if root is None:
        raise ValueError(f"Failed to parse {resref}.{restype}.")

    refs = _extract_refs(root)

    # Group by kind
    grouped: Dict[str, List[str]] = {}
    for ref in refs:
        kind = ref["ref_kind"]
        grouped.setdefault(kind, [])
        val = ref["value"]
        if val not in grouped[kind]:
            grouped[kind].append(val)

    return json_content({
        "resref": resref,
        "restype": restype.upper(),
        "file_type": root.file_type,
        "reference_count": len(refs),
        "by_kind": grouped,
        "all_references": refs,
    })
