"""MCP tools — composite "get X" layer.

Design philosophy (DESIGN_PHILOSOPHY.md, §8–9):
  - Tools answer questions, not expose formats.
  - Every tool has functional cohesion: "Returns <X>."
  - Every tool delegates to existing primitives (find_resource_bytes,
    _extract_refs, …) — no duplicated format-reading logic.
  - Tools are context-agnostic: the same tool works in a Discord bot,
    a VS Code assistant, Claude Desktop, or a headless CI script.
  - A ``format`` parameter controls presentation (json | markdown | brief)
    without changing the underlying data.

Structured Design rationale (Yourdon & Constantine §3):
  - Fan-in: all handlers call find_resource_bytes, _extract_refs, and other
    primitives that already exist in the codebase.
  - Information hiding: callers never need to know that "quest" = JRL + TLK +
    GFF refs. That mapping is hidden here.
  - Transform center: get_quest, get_creature, get_area act as top-level
    coordinators; find_resource_bytes / parse_gff are the atomic primitives.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from gmodular.mcp._formatting import json_content
from gmodular.mcp.state import load_installation, resolve_game

log = logging.getLogger(__name__)


# ── Tool schemas ───────────────────────────────────────────────────────────

def get_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "get_resource",
            "description": (
                "Return a KotOR resource in its most useful human-readable form. "
                "Pass a resref and type (utc, dlg, jrl, 2da, nss, ncs, are, git, "
                "mdl, wok, lyt, tpc, tlk). Returns structured data for GFF types, "
                "text for scripts, table rows for 2DA, and decoded info for binary "
                "formats. Use format='markdown' for plain-text output."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "Resource reference (no extension)"},
                    "type": {"type": "string", "description": "Resource type / extension (utc, dlg, jrl, 2da, nss, ncs, are, git, mdl, wok, lyt, tpc)"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "brief"],
                        "default": "json",
                        "description": "Output format: json (default), markdown (human readable), brief (one-line summary)",
                    },
                },
                "required": ["game", "resref", "type"],
            },
        },
        {
            "name": "get_quest",
            "description": (
                "Return a complete quest profile by tag. Aggregates: JRL states with "
                "TLK-resolved text, all scripts responsible for quest state changes, "
                "all DLG files that reference this quest tag, global variables "
                "(K_SWG_ naming convention), and globalcat.2da entry. "
                "Use format='markdown' for plain-text output."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "tag": {"type": "string", "description": "Quest tag (e.g. k_swg_bastila, or partial match)"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "brief"],
                        "default": "json",
                    },
                },
                "required": ["game", "tag"],
            },
        },
        {
            "name": "get_creature",
            "description": (
                "Return a complete creature (NPC) profile from a UTC template. "
                "Includes: name/tag, race/gender/class/level/HP, appearance.2da row, "
                "all script slots, faction, equipment item resrefs, and portrait. "
                "Use format='markdown' for readable output."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "UTC resref (e.g. n_bastila, c_darth_malak)"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "brief"],
                        "default": "json",
                    },
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "get_conversation",
            "description": (
                "Return a full DLG (dialogue) tree with TLK-resolved text. "
                "Includes: all entry/reply nodes with display text, branch connections, "
                "all script references (action/condition scripts), VO sound ResRefs, "
                "and speaker assignments. Use format='markdown' for readable tree output."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "DLG resref (e.g. c_bastila, tar_vult_01)"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "brief"],
                        "default": "json",
                    },
                    "max_nodes": {
                        "type": "integer",
                        "default": 200,
                        "description": "Maximum nodes to return (default 200)",
                    },
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "get_area",
            "description": (
                "Return a complete area profile. Includes: ARE properties (name, "
                "ambient music, fog, sky box), GIT object counts (creatures, doors, "
                "placeables, triggers, waypoints, sounds, stores), LYT room list, "
                "and all script references from the ARE. "
                "Use format='markdown' for module-design summaries."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "Area resref (e.g. 203tel, danm13)"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "brief"],
                        "default": "json",
                    },
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "get_script",
            "description": (
                "Return a script profile. Attempts to retrieve NSS source (from override "
                "or module), then falls back to decompiling the NCS binary via DeNCS, "
                "xoreos-tools, or pykotor. Also returns all GFF resources that reference "
                "this script by name. Use format='markdown' for readable output."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "resref": {"type": "string", "description": "Script resref (no extension, e.g. k_hench_bastila)"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "brief"],
                        "default": "json",
                    },
                },
                "required": ["game", "resref"],
            },
        },
        {
            "name": "search",
            "description": (
                "Return ranked search results from a full-text search across the KotOR "
                "installation. Searches: TLK strings, 2DA table values (all columns), "
                "resource names (resref), GFF Tag fields, and NWScript function names. "
                "Each result includes type, resref, field, and matched text."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "description": "k1 or k2"},
                    "query": {"type": "string", "description": "Search term (case-insensitive)"},
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Resource types to search (tlk, 2da, resref, tag, nss). Default: all.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                },
                "required": ["game", "query"],
            },
        },
    ]


# ── Shared primitives ──────────────────────────────────────────────────────

def _find(inst: Any, resref: str, ext: str) -> Optional[bytes]:
    """Return resource bytes or None (never raises)."""
    try:
        from gmodular.mcp.tools.discovery import find_resource_bytes
        return find_resource_bytes(inst, resref, ext)
    except Exception:
        return None


def _parse_gff(data: bytes) -> Optional[Any]:
    """Parse GFF bytes → root struct, or None on failure."""
    try:
        from gmodular.formats.gff_reader import GFFReader
        return GFFReader(data).parse()
    except Exception:
        return None


def _gv(struct: Any, label: str, default: Any = "") -> Any:
    """Get a GFF field value by label, returning default on miss."""
    if struct is None or not hasattr(struct, "fields"):
        return default
    field = struct.fields.get(label)
    if field is None:
        return default
    value = field.value if hasattr(field, "value") else field
    # CExoLocString → pick first language text
    if hasattr(value, "strings") and isinstance(value.strings, dict):
        for lang_val in value.strings.values():
            if lang_val:
                return lang_val
        strref = getattr(value, "strref", -1)
        return f"<strref:{strref}>" if strref >= 0 else ""
    if value is None:
        return default
    return value


def _resolve_tlk(inst: Any, strref: int) -> str:
    """Resolve a TLK strref to text, or empty string on failure."""
    if strref < 0:
        return ""
    try:
        tlk_path = inst.tlk_path()
        if tlk_path is None:
            return ""
        from gmodular.formats.tlk_reader import TLKReader
        tlk = TLKReader.from_bytes(tlk_path.read_bytes())
        if 0 <= strref < len(tlk.entries):
            return tlk.entries[strref].text or ""
    except Exception:
        pass
    return ""


def _resolve_exolocstr(inst: Any, field_value: Any) -> str:
    """Resolve a CExoLocString value (dict or string) to display text."""
    if isinstance(field_value, str):
        return field_value
    if hasattr(field_value, "strings"):
        for lang_val in field_value.strings.values():
            if lang_val:
                return lang_val
        strref = getattr(field_value, "strref", -1)
        if strref >= 0:
            return _resolve_tlk(inst, strref)
    if isinstance(field_value, int) and field_value >= 0:
        return _resolve_tlk(inst, field_value)
    return str(field_value) if field_value else ""


def _extract_scripts(struct: Any) -> List[str]:
    """Extract all non-empty script resrefs from a GFF struct."""
    from gmodular.mcp.tools.refs import _SCRIPT_FIELDS, _extract_refs
    refs = _extract_refs(struct)
    return sorted({r["value"] for r in refs if r["ref_kind"] == "script" and r["value"]})


def _find_referrers(inst: Any, script_name: str) -> List[str]:
    """Return list of 'resref.TYPE' strings that reference script_name."""
    try:
        from gmodular.mcp.tools.refs import _extract_refs, _SCRIPT_FIELDS
        from gmodular.mcp.tools.discovery import _read_entry_data
        from gmodular.formats.gff_reader import GFFReader
        GFF_EXTS = {"are", "git", "utc", "utd", "ute", "uti", "utp",
                    "uts", "utm", "utt", "utw", "dlg", "jrl"}
        idx = inst.index
        hits: List[str] = []
        for (resref, ext), entries in idx["by_key"].items():
            if ext.lower() not in GFF_EXTS:
                continue
            try:
                data = _read_entry_data(entries[0])
                root = GFFReader(data).parse()
                if root is None:
                    continue
                refs = _extract_refs(root)
                for r in refs:
                    if r["ref_kind"] == "script" and r["value"].lower() == script_name.lower():
                        hits.append(f"{resref}.{ext.upper()}")
                        break
            except Exception:
                continue
        return sorted(set(hits))
    except Exception:
        return []


# ── Decompiler chain ───────────────────────────────────────────────────────

def _decompile_ncs(ncs_bytes: bytes) -> Optional[str]:
    """
    Attempt to decompile NCS bytes → NSS source.
    Tries (in order): DeNCS CLI, xoreos-tools ncsdecomp, pykotor.
    Returns NSS source string or None if no decompiler is available.
    """
    # Write NCS to a temp file
    with tempfile.NamedTemporaryFile(suffix=".ncs", delete=False) as tf:
        tf.write(ncs_bytes)
        ncs_path = tf.name

    # 1. DeNCS CLI (NCSDecompCLI.jar)
    dencs_candidates = [
        Path("tools") / "NCSDecompCLI.jar",
        Path(__file__).parent.parent.parent.parent / "tools" / "NCSDecompCLI.jar",
    ]
    for jar_path in dencs_candidates:
        if jar_path.exists():
            try:
                out_path = ncs_path.replace(".ncs", ".nss")
                result = subprocess.run(
                    ["java", "-jar", str(jar_path), ncs_path, out_path],
                    capture_output=True, timeout=10
                )
                if Path(out_path).exists():
                    with open(out_path) as f:
                        source = f.read()
                    Path(out_path).unlink(missing_ok=True)
                    Path(ncs_path).unlink(missing_ok=True)
                    return source
            except Exception:
                pass

    # 2. xoreos-tools ncsdecomp
    try:
        result = subprocess.run(
            ["ncsdecomp", "-o", "/dev/stdout", ncs_path],
            capture_output=True, timeout=10
        )
        if result.returncode == 0 and result.stdout:
            Path(ncs_path).unlink(missing_ok=True)
            return result.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 3. pykotor
    try:
        import importlib
        spec = importlib.util.find_spec("pykotor")
        if spec is not None:
            from pykotor.resource.formats.ncs import read_ncs  # type: ignore
            nss = read_ncs(ncs_bytes)
            if nss:
                Path(ncs_path).unlink(missing_ok=True)
                return str(nss)
    except Exception:
        pass

    Path(ncs_path).unlink(missing_ok=True)
    return None


# ── Formatting helpers ─────────────────────────────────────────────────────

def _fmt(data: Dict[str, Any], fmt: str, md_fn) -> Any:
    """Route to json_content or a markdown formatter."""
    if fmt == "markdown":
        return json_content({"markdown": md_fn(data), "data": data})
    if fmt == "brief":
        brief = md_fn(data).split("\n")[0]
        return json_content({"brief": brief})
    return json_content(data)


# ── Handler: get_resource ──────────────────────────────────────────────────

async def handle_get_resource(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower().strip()
    res_type = (arguments.get("type") or "").lower().lstrip(".")
    fmt = (arguments.get("format") or "json").lower()

    data = _find(inst, resref, res_type)
    if data is None:
        raise ValueError(f"{resref}.{res_type} not found in {game_key} installation.")

    result: Dict[str, Any] = {
        "resref": resref,
        "type": res_type,
        "game": game_key,
        "size_bytes": len(data),
    }

    # GFF types → full field tree
    GFF_TYPES = {"are", "git", "ifo", "utc", "utd", "ute", "uti", "utp",
                 "uts", "utm", "utt", "utw", "bic", "dlg", "fac", "jrl",
                 "gff", "gui", "gic"}
    if res_type in GFF_TYPES:
        root = _parse_gff(data)
        if root is None:
            raise ValueError(f"Failed to parse {resref}.{res_type} as GFF.")
        result["file_type"] = root.file_type
        # Build a readable field dict
        fields: Dict[str, Any] = {}
        if hasattr(root, "fields"):
            for label, gff_field in root.fields.items():
                value = gff_field.value if hasattr(gff_field, "value") else gff_field
                # Resolve CExoLocString
                if hasattr(value, "strings") or isinstance(value, int):
                    text = _resolve_exolocstr(inst, value)
                    if text:
                        fields[label] = {"value": value if not hasattr(value, "strings") else str(value), "text": text}
                    else:
                        fields[label] = value if not hasattr(value, "strings") else str(value)
                elif isinstance(value, list):
                    fields[label] = f"<list of {len(value)} items>"
                elif hasattr(value, "fields"):
                    fields[label] = f"<struct with {len(value.fields)} fields>"
                else:
                    fields[label] = value
        result["fields"] = fields

    elif res_type == "2da":
        text = data.decode("latin-1", errors="replace")
        from gmodular.formats.twoda_loader import _parse_2da
        table = _parse_2da(text, resref)
        if table:
            result["columns"] = table.columns
            result["row_count"] = len(table.rows)
            result["rows"] = {str(k): v for k, v in list(table.rows.items())[:100]}
        else:
            result["raw_text"] = text[:2000]

    elif res_type == "tlk":
        from gmodular.formats.tlk_reader import TLKReader
        tlk = TLKReader.from_bytes(data)
        result["language_id"] = tlk.language_id
        result["entry_count"] = len(tlk.entries)
        result["note"] = "Use get_resource with resref=dialog and type=tlk, then query by strref number."

    elif res_type in ("nss",):
        result["source"] = data.decode("utf-8", errors="replace")

    elif res_type == "ncs":
        source = _decompile_ncs(data)
        result["decompiled_source"] = source
        result["decompiler_available"] = source is not None
        if source is None:
            result["raw_base64"] = __import__("base64").b64encode(data[:256]).decode()
            result["note"] = "No decompiler found. Install DeNCS (tools/NCSDecompCLI.jar), xoreos-tools, or pykotor."

    elif res_type == "mdl":
        try:
            from gmodular.formats.mdl_parser import MDLParser
            mesh = MDLParser(data, b"").parse()
            if mesh:
                nodes = mesh.all_nodes()
                result["model_name"] = mesh.name
                result["node_count"] = len(nodes)
                result["mesh_node_count"] = len(mesh.mesh_nodes())
                result["animation_count"] = len(mesh.animations)
        except Exception as e:
            result["error"] = str(e)

    elif res_type in ("wok", "pwk", "dwk"):
        try:
            from gmodular.formats.wok_parser import WOKParser
            wok = WOKParser.from_bytes(data)
            if wok:
                result["face_count"] = len(wok.faces)
                walkable = sum(1 for f in wok.faces if getattr(f, "walkable", False))
                result["walkable_faces"] = walkable
        except Exception as e:
            result["error"] = str(e)

    elif res_type == "lyt":
        text = data.decode("latin-1", errors="replace")
        rooms = [l.strip() for l in text.splitlines() if l.strip().lower().startswith("room")]
        result["room_count"] = len(rooms)
        result["rooms"] = rooms

    elif res_type == "tpc":
        try:
            from gmodular.formats.tpc_reader import TPCReader
            tpc = TPCReader.from_bytes(data)
            if tpc:
                result["width"] = tpc.width
                result["height"] = tpc.height
                result["format"] = tpc.format_name
                result["mip_levels"] = tpc.mip_count
        except Exception as e:
            result["error"] = str(e)

    else:
        result["raw_base64"] = __import__("base64").b64encode(data[:256]).decode()
        result["note"] = f"Binary format '{res_type}' — first 256 bytes shown as base64."

    def _md(d: Dict[str, Any]) -> str:
        lines = [f"## {d['resref']}.{d['type']} ({d['game'].upper()})"]
        lines.append(f"**Size**: {d['size_bytes']} bytes")
        if "file_type" in d:
            lines.append(f"**GFF type**: {d['file_type']}")
        if "fields" in d:
            lines.append("**Fields:**")
            for k, v in list(d["fields"].items())[:20]:
                if isinstance(v, dict) and "text" in v:
                    lines.append(f"  - `{k}`: {v['text']!r}")
                elif isinstance(v, str) and v:
                    lines.append(f"  - `{k}`: {v!r}")
                elif v is not None and v != "" and not isinstance(v, dict):
                    lines.append(f"  - `{k}`: {v}")
        if "source" in d:
            lines.append("**NSS Source:**")
            lines.append(f"```nwscript\n{d['source'][:1000]}\n```")
        if "decompiled_source" in d and d["decompiled_source"]:
            lines.append("**Decompiled NCS:**")
            lines.append(f"```nwscript\n{d['decompiled_source'][:1000]}\n```")
        if "rows" in d:
            cols = d.get("columns", [])
            lines.append(f"**2DA columns**: {', '.join(cols[:10])}")
            lines.append(f"**Row count**: {d.get('row_count', 0)}")
        if "rooms" in d:
            lines.append(f"**Rooms ({d['room_count']})**: {', '.join(d['rooms'][:10])}")
        return "\n".join(lines)

    return _fmt(result, fmt, _md)


# ── Handler: get_quest ─────────────────────────────────────────────────────

async def handle_get_quest(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    tag = (arguments.get("tag") or "").strip()
    fmt = (arguments.get("format") or "json").lower()

    if not tag:
        raise ValueError("tag is required.")

    # Load global.jrl (or search for a matching JRL)
    jrl_data = _find(inst, "global", "jrl")
    if jrl_data is None:
        raise ValueError("global.jrl not found in installation.")

    root = _parse_gff(jrl_data)
    if root is None:
        raise ValueError("Failed to parse global.jrl.")

    # Find matching category by tag (case-insensitive partial match)
    cat_list = root.get("Categories") or []
    if not isinstance(cat_list, list):
        cat_list = []

    matching_cats: List[Dict[str, Any]] = []
    for cat_struct in cat_list:
        cat_tag = _gv(cat_struct, "Tag", "")
        cat_name_raw = _gv(cat_struct, "Name", "")
        cat_name = _resolve_exolocstr(inst, cat_name_raw)

        tag_lower = tag.lower()
        if tag_lower not in cat_tag.lower() and tag_lower not in cat_name.lower():
            continue

        # Build state list
        states: List[Dict[str, Any]] = []
        entry_list = _gv(cat_struct, "EntryList", [])
        if not isinstance(entry_list, list):
            entry_list = []
        for entry_struct in entry_list:
            state_id = _gv(entry_struct, "ID", 0)
            text_raw = _gv(entry_struct, "Text", "")
            text = _resolve_exolocstr(inst, text_raw)
            comment = _gv(entry_struct, "Comment", "")
            end = bool(_gv(entry_struct, "End", 0))
            states.append({
                "id": state_id,
                "text": text[:500] if text else "",
                "comment": comment,
                "completes_quest": end,
            })

        # Collect scripts from the category struct itself
        scripts = _extract_scripts(cat_struct)

        matching_cats.append({
            "tag": cat_tag,
            "name": cat_name,
            "priority": _gv(cat_struct, "Priority", 0),
            "states": states,
            "scripts": scripts,
        })

    if not matching_cats:
        raise ValueError(f"No quest found with tag matching '{tag}' in global.jrl.")

    # For each matching quest, find DLG files that reference the tag
    all_tags = [c["tag"] for c in matching_cats if c["tag"]]
    dlg_referrers: List[str] = []
    for q_tag in all_tags:
        try:
            from gmodular.mcp.tools.refs import _extract_refs
            from gmodular.mcp.tools.discovery import _read_entry_data
            from gmodular.formats.gff_reader import GFFReader
            idx = inst.index
            for (resref, ext), entries in idx["by_key"].items():
                if ext.lower() != "dlg":
                    continue
                try:
                    data = _read_entry_data(entries[0])
                    ref_root = GFFReader(data).parse()
                    if ref_root is None:
                        continue
                    refs = _extract_refs(ref_root)
                    for r in refs:
                        if r["ref_kind"] == "tag" and q_tag.lower() in r["value"].lower():
                            dlg_referrers.append(resref)
                            break
                except Exception:
                    continue
        except Exception:
            pass

    # Global variable inference (K_SWG_ naming convention)
    global_vars: List[Dict[str, str]] = []
    for qt in all_tags:
        base = qt.upper()
        global_vars.append({"name": base, "type": "boolean", "description": "Quest active flag"})
        global_vars.append({"name": f"{base}_STATE", "type": "number", "description": "Quest state index"})

    result: Dict[str, Any] = {
        "game": game_key,
        "query": tag,
        "quests": matching_cats,
        "dlg_referrers": sorted(set(dlg_referrers))[:50],
        "inferred_global_vars": global_vars,
    }

    def _md(d: Dict[str, Any]) -> str:
        lines = [f"## Quest: {tag} ({game_key.upper()})"]
        for q in d["quests"]:
            lines.append(f"\n### {q['name'] or q['tag']}  `{q['tag']}`")
            if q["states"]:
                lines.append("**States:**")
                for s in q["states"]:
                    marker = "✅" if s["completes_quest"] else "•"
                    lines.append(f"  {marker} **{s['id']}**: {s['text'] or s['comment'] or '(no text)'}")
            if q["scripts"]:
                lines.append(f"**Scripts**: `{'`, `'.join(q['scripts'])}`")
        if d["dlg_referrers"]:
            lines.append(f"\n**Referenced in dialogues**: `{'`, `'.join(d['dlg_referrers'][:10])}`")
        if d["inferred_global_vars"]:
            lines.append("\n**Global variables (inferred from tag):**")
            for gv in d["inferred_global_vars"]:
                lines.append(f"  - `{gv['name']}` ({gv['type']}): {gv['description']}")
        return "\n".join(lines)

    return _fmt(result, fmt, _md)


# ── Handler: get_creature ──────────────────────────────────────────────────

async def handle_get_creature(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower().strip()
    fmt = (arguments.get("format") or "json").lower()

    utc_data = _find(inst, resref, "utc")
    if utc_data is None:
        raise ValueError(f"{resref}.utc not found in {game_key} installation.")

    root = _parse_gff(utc_data)
    if root is None:
        raise ValueError(f"Failed to parse {resref}.utc.")

    # Core identity
    name_raw = _gv(root, "FirstName", "")
    name = _resolve_exolocstr(inst, name_raw)
    last_name_raw = _gv(root, "LastName", "")
    last_name = _resolve_exolocstr(inst, last_name_raw)
    full_name = f"{name} {last_name}".strip() or resref

    tag = _gv(root, "Tag", "")
    race = _gv(root, "Race", 0)
    gender = _gv(root, "Gender", 0)
    appearance_id = _gv(root, "Appearance_Type", 0)
    portrait_id = _gv(root, "PortraitId", 0)
    faction_id = _gv(root, "FactionID", 0)
    max_hp = _gv(root, "MaxHitPoints", 0)
    current_hp = _gv(root, "CurrentHitPoints", 0)
    conversation = _gv(root, "Conversation", "")

    # Class/level (ClassList GFF list)
    classes: List[Dict[str, Any]] = []
    class_list = _gv(root, "ClassList", [])
    if isinstance(class_list, list):
        for cls_struct in class_list:
            cid = _gv(cls_struct, "Class", 0)
            lvl = _gv(cls_struct, "ClassLevel", 0)
            classes.append({"class_id": cid, "level": lvl})

    # Script slots
    script_fields = [
        "ScriptAttacked", "ScriptDamaged", "ScriptDeath",
        "ScriptDialogue", "ScriptEndDialogue", "ScriptEndRound",
        "ScriptHeartbeat", "ScriptOnBlocked", "ScriptOnNotice",
        "ScriptRested", "ScriptSpawn", "ScriptSpellAt",
        "ScriptUserDefine",
    ]
    scripts: Dict[str, str] = {}
    for sf in script_fields:
        val = _gv(root, sf, "")
        if val:
            scripts[sf] = str(val)

    # Appearance.2da lookup
    appearance_row: Dict[str, Any] = {}
    try:
        app_data = _find(inst, "appearance", "2da")
        if app_data:
            text = app_data.decode("latin-1", errors="replace")
            from gmodular.formats.twoda_loader import _parse_2da
            table = _parse_2da(text, "appearance")
            if table:
                row = table.rows.get(int(appearance_id), {})
                appearance_row = {k: v for k, v in row.items()
                                  if k in ("label", "modeltype", "normalhead", "backuphead",
                                           "race", "gender", "texvariation", "bloodcolr",
                                           "modelscale", "walkdist", "rundist")}
    except Exception:
        pass

    # Portrait lookup
    portrait_resref = ""
    try:
        port_data = _find(inst, "portraits", "2da")
        if port_data:
            text = port_data.decode("latin-1", errors="replace")
            from gmodular.formats.twoda_loader import _parse_2da
            table = _parse_2da(text, "portraits")
            if table:
                row = table.rows.get(int(portrait_id), {})
                portrait_resref = row.get("baseresref", "")
    except Exception:
        pass

    # Equipment (ItemList)
    equipment: List[str] = []
    item_list = _gv(root, "ItemList", [])
    if isinstance(item_list, list):
        for item_struct in item_list:
            item_ref = _gv(item_struct, "TemplateResRef", "")
            if item_ref:
                equipment.append(str(item_ref))

    result: Dict[str, Any] = {
        "game": game_key,
        "resref": resref,
        "name": full_name,
        "tag": str(tag),
        "race": race,
        "gender": gender,
        "appearance_id": appearance_id,
        "appearance": appearance_row,
        "portrait_id": portrait_id,
        "portrait_resref": portrait_resref,
        "faction_id": faction_id,
        "max_hp": max_hp,
        "current_hp": current_hp,
        "conversation": str(conversation),
        "classes": classes,
        "scripts": scripts,
        "equipment": equipment[:20],
    }

    def _md(d: Dict[str, Any]) -> str:
        lines = [f"## {d['name']}  `{d['resref']}.utc` ({d['game'].upper()})"]
        lines.append(f"**Tag**: `{d['tag']}`  |  **Race**: {d['race']}  |  **Gender**: {d['gender']}")
        if d["classes"]:
            cls_str = ", ".join(f"Class {c['class_id']} Lv{c['level']}" for c in d["classes"])
            lines.append(f"**Class/Level**: {cls_str}")
        lines.append(f"**HP**: {d['current_hp']}/{d['max_hp']}")
        if d["appearance"]:
            lines.append(f"**Appearance**: {d['appearance']}")
        if d["conversation"]:
            lines.append(f"**Conversation**: `{d['conversation']}`")
        if d["scripts"]:
            lines.append("**Scripts:**")
            for k, v in d["scripts"].items():
                lines.append(f"  - `{k}`: `{v}`")
        if d["equipment"]:
            lines.append(f"**Equipment**: `{'`, `'.join(d['equipment'])}`")
        return "\n".join(lines)

    return _fmt(result, fmt, _md)


# ── Handler: get_conversation ──────────────────────────────────────────────

async def handle_get_conversation(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower().strip()
    fmt = (arguments.get("format") or "json").lower()
    max_nodes = int(arguments.get("max_nodes", 200))

    dlg_data = _find(inst, resref, "dlg")
    if dlg_data is None:
        raise ValueError(f"{resref}.dlg not found in {game_key} installation.")

    root = _parse_gff(dlg_data)
    if root is None:
        raise ValueError(f"Failed to parse {resref}.dlg.")

    def _parse_node_list(node_list: Any, kind: str) -> List[Dict[str, Any]]:
        if not isinstance(node_list, list):
            return []
        nodes: List[Dict[str, Any]] = []
        for i, struct in enumerate(node_list):
            if i >= max_nodes:
                break
            text_raw = _gv(struct, "Text", "")
            text = _resolve_exolocstr(inst, text_raw)
            speaker = _gv(struct, "Speaker", "")
            sound = _gv(struct, "Sound", "")
            script1 = _gv(struct, "Script1", "")
            script2 = _gv(struct, "Script2", "")
            active = _gv(struct, "Active", "")
            quest = _gv(struct, "Quest", "")
            quest_entry = _gv(struct, "QuestEntry", 0)

            # Branch links
            reply_list = _gv(struct, "RepliesList", [])
            entry_list = _gv(struct, "EntriesList", [])
            branches: List[Dict[str, Any]] = []
            for link in (reply_list if isinstance(reply_list, list) else []):
                idx_val = _gv(link, "Index", -1)
                is_child = _gv(link, "IsChild", 0)
                if idx_val >= 0:
                    branches.append({"index": idx_val, "kind": "reply", "is_child": bool(is_child)})
            for link in (entry_list if isinstance(entry_list, list) else []):
                idx_val = _gv(link, "Index", -1)
                is_child = _gv(link, "IsChild", 0)
                if idx_val >= 0:
                    branches.append({"index": idx_val, "kind": "entry", "is_child": bool(is_child)})

            node: Dict[str, Any] = {
                "index": i,
                "kind": kind,
                "text": text[:400],
                "speaker": str(speaker),
            }
            if sound:
                node["sound"] = str(sound)
            if script1:
                node["script1"] = str(script1)
            if script2:
                node["script2"] = str(script2)
            if active:
                node["active_condition"] = str(active)
            if quest:
                node["quest"] = str(quest)
            if quest_entry:
                node["quest_entry"] = quest_entry
            if branches:
                node["branches"] = branches
            nodes.append(node)
        return nodes

    entry_list = root.get("EntryList") or []
    reply_list = root.get("ReplyList") or []
    starter_list = root.get("StartingList") or []

    entries = _parse_node_list(entry_list, "entry")
    replies = _parse_node_list(reply_list, "reply")

    starters: List[Dict[str, Any]] = []
    if isinstance(starter_list, list):
        for link in starter_list:
            idx_val = _gv(link, "Index", -1)
            if idx_val >= 0:
                starters.append({"index": idx_val, "kind": "entry"})

    # Collect all script refs
    from gmodular.mcp.tools.refs import _extract_refs
    all_scripts = sorted({r["value"] for r in _extract_refs(root) if r["ref_kind"] == "script"})

    result: Dict[str, Any] = {
        "game": game_key,
        "resref": resref,
        "entry_count": len(entries),
        "reply_count": len(replies),
        "starters": starters,
        "entries": entries,
        "replies": replies,
        "scripts": all_scripts[:50],
        "truncated": len(entry_list) > max_nodes or len(reply_list) > max_nodes,
    }

    def _md(d: Dict[str, Any]) -> str:
        lines = [f"## Dialogue: {d['resref']}.dlg ({d['game'].upper()})"]
        lines.append(f"**{d['entry_count']} NPC entries**, **{d['reply_count']} player replies**")
        if d["scripts"]:
            lines.append(f"**Scripts**: `{'`, `'.join(d['scripts'][:10])}`")
        lines.append("\n**Dialogue tree** (first 20 entries):")
        for entry in d["entries"][:20]:
            text = entry.get("text") or "(no text)"
            spk = entry.get("speaker") or "NPC"
            lines.append(f"  [{entry['index']}] **{spk}**: {text[:120]}")
            for b in entry.get("branches", [])[:4]:
                reply = d["replies"][b["index"]] if b["kind"] == "reply" and b["index"] < len(d["replies"]) else None
                if reply:
                    rtxt = reply.get("text") or "(no text)"
                    lines.append(f"      → [{b['index']}] PC: {rtxt[:100]}")
        return "\n".join(lines)

    return _fmt(result, fmt, _md)


# ── Handler: get_area ─────────────────────────────────────────────────────

async def handle_get_area(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower().strip()
    fmt = (arguments.get("format") or "json").lower()

    # ARE file
    are_data = _find(inst, resref, "are")
    if are_data is None:
        raise ValueError(f"{resref}.are not found in {game_key} installation.")

    are_root = _parse_gff(are_data)
    if are_root is None:
        raise ValueError(f"Failed to parse {resref}.are.")

    name_raw = _gv(are_root, "Name", "")
    area_name = _resolve_exolocstr(inst, name_raw)
    ambient_music = _gv(are_root, "MusicBackground", 0)
    ambient_battle = _gv(are_root, "MusicBattle", 0)
    ambient_day = _gv(are_root, "AmbientSndDay", "")
    fog_on = bool(_gv(are_root, "FogOn", 0))

    are_scripts = _extract_scripts(are_root)

    # GIT file (object placement)
    git_summary: Dict[str, Any] = {
        "creatures": 0, "doors": 0, "placeables": 0,
        "triggers": 0, "waypoints": 0, "sounds": 0, "stores": 0,
        "encounters": 0,
    }
    git_data = _find(inst, resref, "git")
    if git_data:
        git_root = _parse_gff(git_data)
        if git_root:
            type_map = {
                "Creature List": "creatures",
                "Door List": "doors",
                "Placeable List": "placeables",
                "TriggerList": "triggers",
                "WaypointList": "waypoints",
                "SoundList": "sounds",
                "StoreList": "stores",
                "Encounter List": "encounters",
            }
            for field_name, key in type_map.items():
                val = _gv(git_root, field_name, [])
                if isinstance(val, list):
                    git_summary[key] = len(val)

    # LYT file
    rooms: List[str] = []
    lyt_data = _find(inst, resref, "lyt")
    if lyt_data:
        text = lyt_data.decode("latin-1", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("room "):
                parts = stripped.split()
                if len(parts) >= 2:
                    rooms.append(parts[1])

    result: Dict[str, Any] = {
        "game": game_key,
        "resref": resref,
        "name": area_name,
        "ambient_music_id": ambient_music,
        "battle_music_id": ambient_battle,
        "ambient_sound": str(ambient_day),
        "fog_on": fog_on,
        "scripts": are_scripts,
        "git_summary": git_summary,
        "rooms": rooms,
        "room_count": len(rooms),
    }

    def _md(d: Dict[str, Any]) -> str:
        lines = [f"## Area: {d['name'] or d['resref']}  `{d['resref']}` ({d['game'].upper()})"]
        lines.append(f"**Music**: background={d['ambient_music_id']}, battle={d['battle_music_id']}")
        lines.append(f"**Ambient sound**: {d['ambient_sound'] or 'none'}  |  **Fog**: {d['fog_on']}")
        s = d["git_summary"]
        total = sum(s.values())
        if total:
            lines.append(f"**Objects** ({total} total):")
            for k, v in s.items():
                if v:
                    lines.append(f"  - {k}: {v}")
        if d["rooms"]:
            lines.append(f"**Rooms ({d['room_count']})**: {', '.join(d['rooms'][:10])}")
        if d["scripts"]:
            lines.append(f"**Scripts**: `{'`, `'.join(d['scripts'])}`")
        return "\n".join(lines)

    return _fmt(result, fmt, _md)


# ── Handler: get_script ───────────────────────────────────────────────────

async def handle_get_script(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    resref = (arguments.get("resref") or "").lower().strip()
    fmt = (arguments.get("format") or "json").lower()

    result: Dict[str, Any] = {
        "game": game_key,
        "resref": resref,
        "source": None,
        "decompiled_source": None,
        "source_from": None,
        "decompiler_used": None,
        "referrers": [],
    }

    # Try NSS source first (override / module)
    nss_data = _find(inst, resref, "nss")
    if nss_data:
        result["source"] = nss_data.decode("utf-8", errors="replace")
        result["source_from"] = "nss"

    # Try NCS binary (and decompile)
    ncs_data = _find(inst, resref, "ncs")
    if ncs_data:
        if result["source"] is None:
            # No NSS — try to decompile
            decompiled = _decompile_ncs(ncs_data)
            if decompiled:
                result["decompiled_source"] = decompiled
                result["source_from"] = "ncs_decompiled"
            else:
                result["source_from"] = "ncs_binary_only"
        result["ncs_size_bytes"] = len(ncs_data)

    if result["source"] is None and result["decompiled_source"] is None:
        if ncs_data is None:
            raise ValueError(f"{resref}.nss and {resref}.ncs not found in {game_key} installation.")

    # Find what resources call this script
    result["referrers"] = _find_referrers(inst, resref)

    def _md(d: Dict[str, Any]) -> str:
        lines = [f"## Script: `{d['resref']}` ({d['game'].upper()})"]
        if d.get("source"):
            lines.append(f"**Source** (NSS):")
            lines.append(f"```nwscript\n{d['source'][:1500]}\n```")
        elif d.get("decompiled_source"):
            lines.append(f"**Decompiled NCS** (auto-decompiled):")
            lines.append(f"```nwscript\n{d['decompiled_source'][:1500]}\n```")
        else:
            lines.append("*No source or decompiler available.*")
        if d["referrers"]:
            lines.append(f"\n**Referenced by ({len(d['referrers'])})**: " +
                         ", ".join(f"`{r}`" for r in d["referrers"][:20]))
        return "\n".join(lines)

    return _fmt(result, fmt, _md)


# ── Handler: search ───────────────────────────────────────────────────────

async def handle_search(arguments: Dict[str, Any]) -> Any:
    game_key = resolve_game(arguments.get("game"))
    if game_key is None:
        raise ValueError("Specify game (k1/k2).")
    inst = load_installation(game_key)

    query = (arguments.get("query") or "").lower().strip()
    if not query:
        raise ValueError("query is required.")

    types_filter = {t.lower() for t in (arguments.get("types") or [])}
    limit = min(int(arguments.get("limit", 50)), 200)

    results: List[Dict[str, Any]] = []
    query_lower = query.lower()

    def _add(res: Dict[str, Any]) -> None:
        if len(results) < limit:
            results.append(res)

    def _should_search(kind: str) -> bool:
        return not types_filter or kind in types_filter

    # 1. TLK string search
    if _should_search("tlk"):
        try:
            tlk_path = inst.tlk_path()
            if tlk_path:
                from gmodular.formats.tlk_reader import TLKReader
                tlk = TLKReader.from_bytes(tlk_path.read_bytes())
                for i, entry in enumerate(tlk.entries):
                    if len(results) >= limit:
                        break
                    if entry.text and query_lower in entry.text.lower():
                        _add({
                            "type": "tlk",
                            "resref": "dialog.tlk",
                            "field": f"strref:{i}",
                            "text": entry.text[:200],
                            "score": 3 if query_lower == entry.text.lower() else 1,
                        })
        except Exception:
            pass

    # 2. Resource name (resref) search
    if _should_search("resref") and len(results) < limit:
        idx = inst.index
        for (resref, ext), entries in idx["by_key"].items():
            if len(results) >= limit:
                break
            if query_lower in resref.lower():
                _add({
                    "type": "resref",
                    "resref": resref,
                    "ext": ext.upper(),
                    "field": "resref",
                    "text": resref,
                    "score": 3 if query_lower == resref.lower() else 2,
                })

    # 3. 2DA value search (key tables only — avoid scanning all 200+ tables)
    if _should_search("2da") and len(results) < limit:
        key_tables = ["appearance", "portraits", "placeables", "heads", "classes",
                      "racialtypes", "feat", "spells", "baseitems", "gender",
                      "phenotype", "movies", "globalcat", "modulelist"]
        for table_name in key_tables:
            if len(results) >= limit:
                break
            try:
                data = _find(inst, table_name, "2da")
                if not data:
                    continue
                text = data.decode("latin-1", errors="replace")
                from gmodular.formats.twoda_loader import _parse_2da
                table = _parse_2da(text, table_name)
                if not table:
                    continue
                for row_idx, row in table.rows.items():
                    if len(results) >= limit:
                        break
                    for col, val in row.items():
                        if val and query_lower in val.lower():
                            _add({
                                "type": "2da",
                                "resref": table_name,
                                "field": f"row_{row_idx}.{col}",
                                "text": f"{table_name}.2da row {row_idx} col {col}: {val}",
                                "score": 2,
                            })
                            break
            except Exception:
                continue

    # 4. GFF Tag field search
    if _should_search("tag") and len(results) < limit:
        try:
            from gmodular.mcp.tools.discovery import _read_entry_data
            from gmodular.formats.gff_reader import GFFReader
            idx = inst.index
            GFF_EXTS = {"utc", "utd", "utp", "ute", "uti", "utm", "utt", "utw"}
            for (resref, ext), entries in idx["by_key"].items():
                if len(results) >= limit:
                    break
                if ext.lower() not in GFF_EXTS:
                    continue
                try:
                    data = _read_entry_data(entries[0])
                    root = GFFReader(data).parse()
                    if root is None:
                        continue
                    tag_field = root.fields.get("Tag")
                    if tag_field:
                        tag_val = tag_field.value if hasattr(tag_field, "value") else tag_field
                        if tag_val and query_lower in str(tag_val).lower():
                            _add({
                                "type": "tag",
                                "resref": resref,
                                "ext": ext.upper(),
                                "field": "Tag",
                                "text": f"{resref}.{ext}: Tag={tag_val}",
                                "score": 3 if query_lower == str(tag_val).lower() else 2,
                            })
                except Exception:
                    continue
        except Exception:
            pass

    # Sort by score descending
    results.sort(key=lambda r: r.get("score", 0), reverse=True)

    return json_content({
        "game": game_key,
        "query": query,
        "count": len(results),
        "results": results[:limit],
    })
