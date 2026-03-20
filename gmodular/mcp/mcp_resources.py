"""GModular MCP resources — kotor:// URI scheme for passive context injection.

URIs:
  kotor://k1/resource/{resref}.{ext}         - Resolved via installation (resolution order)
  kotor://k2/resource/{resref}.{ext}
  kotor://k1/2da/{table_name}                - 2DA table as JSON
  kotor://k2/2da/{table_name}
  kotor://k1/tlk/{strref}                    - TLK string by reference
  kotor://k2/tlk/{strref}
  kotor://k1/walkmesh-diagram/{resref}.wok   - Text validation diagram (perimeter, transitions)
  kotor://k2/walkmesh-diagram/{resref}.wok
  kotor://docs/capabilities                  - Tool index + resolution order (agent onboarding)

Design (DESIGN_PHILOSOPHY.md §8, Constantine §3.4):
  URI scheme provides passive context injection — an MCP client can resolve any
  kotor:// URI without calling a tool.  Resources are read-only.

  This module mirrors Tools/KotorMCP/src/kotormcp/mcp_resources.py from the
  PyKotor monorepo (https://github.com/OldRepublicDevs/PyKotor).  GModular
  extends it with the walkmesh-diagram resource type.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from gmodular.mcp.state import load_installation, resolve_game


# ── URI parsing ────────────────────────────────────────────────────────────

def _game_from_authority(authority: str) -> Optional[str]:
    """Map URI authority to canonical game key (K1/K2), or None."""
    return resolve_game(authority.lower())


def parse_kotor_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse a kotor:// URI into components.

    Returns dict with keys: game, type, path, authority, uri — or None if invalid.
    """
    if not uri.startswith("kotor://"):
        return None
    remainder = uri[8:]                        # strip "kotor://"
    parts = remainder.split("/", 2)
    if len(parts) < 2:
        return None
    authority = parts[0].lower()
    resource_type = parts[1].lower()
    path = unquote(parts[2]) if len(parts) > 2 else ""
    game = _game_from_authority(authority)
    if game is None and authority != "docs":
        return None
    return {
        "game": game,
        "type": resource_type,
        "path": path,
        "uri": uri,
        "authority": authority,
    }


# ── Resource template listing ──────────────────────────────────────────────

async def list_resources() -> List[Dict[str, Any]]:
    """Return static kotor:// URI templates for MCP resource discovery."""
    return [
        {
            "uri": "kotor://k1/resource/{resref}.{ext}",
            "name": "K1 Resource",
            "description": "Resolve K1 resource by resref.ext (OVERRIDE → MODULES → CHITIN)",
            "mimeType": "application/octet-stream",
        },
        {
            "uri": "kotor://k2/resource/{resref}.{ext}",
            "name": "K2 Resource",
            "description": "Resolve K2 resource by resref.ext (OVERRIDE → MODULES → CHITIN)",
            "mimeType": "application/octet-stream",
        },
        {
            "uri": "kotor://k1/2da/{table_name}",
            "name": "K1 2DA Table",
            "description": "2DA table as JSON (K1)",
            "mimeType": "application/json",
        },
        {
            "uri": "kotor://k2/2da/{table_name}",
            "name": "K2 2DA Table",
            "description": "2DA table as JSON (K2)",
            "mimeType": "application/json",
        },
        {
            "uri": "kotor://k1/tlk/{strref}",
            "name": "K1 TLK String",
            "description": "TLK talk-table string by strref integer (K1)",
            "mimeType": "text/plain",
        },
        {
            "uri": "kotor://k2/tlk/{strref}",
            "name": "K2 TLK String",
            "description": "TLK talk-table string by strref integer (K2)",
            "mimeType": "text/plain",
        },
        {
            "uri": "kotor://k1/walkmesh-diagram/{resref}.wok",
            "name": "K1 Walkmesh Validation Diagram",
            "description": "Plain-text perimeter + transition diagram for an area walkmesh (K1)",
            "mimeType": "text/plain",
        },
        {
            "uri": "kotor://k2/walkmesh-diagram/{resref}.wok",
            "name": "K2 Walkmesh Validation Diagram",
            "description": "Plain-text perimeter + transition diagram for an area walkmesh (K2)",
            "mimeType": "text/plain",
        },
        {
            "uri": "kotor://docs/capabilities",
            "name": "GModular MCP capabilities",
            "description": "Resolution order, tool index, and when to use each tool (agent onboarding)",
            "mimeType": "text/markdown",
        },
    ]


# ── Capabilities doc ───────────────────────────────────────────────────────

def _capabilities_doc() -> str:
    """One-page onboarding markdown: resolution order + tool index."""
    return """\
# GModular MCP — capabilities

## Resource resolution order

Resources are resolved in this priority order (first match wins):

1. **OVERRIDE** — `<game>/override/` directory (user / mod content)
2. **MODULES** — Module ERF/RIM capsules in `<game>/modules/`
3. **CHITIN** — Base game `chitin.key` / BIF data files

## Tool index

| Tool | Use when |
|------|----------|
| `get_resource` | Fetch any resource as text/JSON by resref+type |
| `get_quest` | Full quest profile: JRL, TLK, scripts, DLG refs, 2DA |
| `get_creature` | UTC template → name, class, HP, scripts, equipment |
| `get_conversation` | Full DLG tree with TLK-resolved text and script refs |
| `get_area` | ARE/GIT/LYT profile with object counts and script refs |
| `get_script` | NSS source / NCS decompile + referrer list |
| `search` | Full-text search across TLK, 2DA, resrefs, GFF tags, NSS |
| `kotor_installation_info` | Check game path, validity, module/override counts |
| `kotor_find_resource` | Find resources by resref or glob (e.g. `*.dlg`) |
| `kotor_search_resources` | Search by regex pattern, paginated |
| `kotor_walkmesh_validation_diagram` | Text diagram of walkable layout, door links, boundary |
| `kotor_walkmesh_info` | Vertex/face counts and surface material breakdown |
| `kotor_mdl_info` | Model node tree, texture refs, animation list |
| `kotor_read_gff` | Read GFF (DLG, UTC, etc.) as JSON |
| `kotor_read_2da` | Read 2DA table as JSON |
| `kotor_read_tlk` | Resolve TLK string by strref |
| `kotor_lookup_2da` | Look up 2DA row by column value |
| `kotor_lookup_tlk` | Look up TLK by substring (returns strref + text) |
| `kotor_describe_dlg` | DLG structure and entry/reply links |
| `kotor_describe_jrl` | Journal structure |
| `kotor_list_modules` | List available modules/areas |
| `kotor_describe_module` | Module metadata and dependencies |
| `kotor_module_resources` | Resources inside a module capsule |
| `kotor_list_references` | Script/DLG/plot refs in a resource |
| `kotor_find_referrers` | Resources that reference a given resref |
| `kotor_find_strref_referrers` | Resources that use a TLK strref |
| `kotor_describe_resource_refs` | Reference summary for any resource |
| `kotor_list_archive` | List contents of an ERF/RIM/BIF |
| `kotor_extract_resource` | Extract binary resource to disk |
| `ghidra_*` | AgentDecompile/Ghidra bridge — binary analysis of swkotor.exe |

## IPC bridges

| Service | Port | Purpose |
|---------|------|---------|
| GhostScripter | 7002 | NWScript compile / DLG editor |
| GhostRigger | 7001 | MDL rig / K1↔K2 model port |
| GModular callback | 7003 | Receive compile results & model updates |
| AgentDecompile | 8080 | Ghidra binary analysis proxy |

## KotorMCP upstream

This tool server is compatible with the KotorMCP protocol defined in the
[PyKotor monorepo](https://github.com/OldRepublicDevs/PyKotor/tree/master/Tools/KotorMCP).
GModular extends that base with composite tools, an AgentDecompile/Ghidra
bridge, walkmesh editing, MDL analysis, and full 3D rendering.
"""


# ── Resource reader ────────────────────────────────────────────────────────

async def read_resource(uri: str) -> Dict[str, Any]:
    """Read a kotor:// URI and return its content (text or base64 blob).

    Called by the MCP server's ``read_resource`` endpoint.
    Returns a dict with keys: uri, mimeType, and either text or blob.
    """
    parsed = parse_kotor_uri(uri)
    if not parsed:
        raise ValueError(f"Invalid kotor:// URI: {uri}")

    authority = parsed["authority"]
    resource_type = parsed["type"]
    path = parsed["path"]

    # ── docs/capabilities ─────────────────────────────────────────────────
    if authority == "docs" and resource_type == "capabilities":
        return {"uri": uri, "mimeType": "text/markdown", "text": _capabilities_doc()}

    game = parsed["game"]
    if game is None:
        raise ValueError(f"Invalid kotor:// URI (unknown game): {uri}")

    inst = load_installation(game)

    # ── resource/{resref}.{ext} ───────────────────────────────────────────
    if resource_type == "resource":
        # path = "resref.ext"
        if "." not in path:
            raise ValueError(f"URI path must be resref.ext, got: {path}")
        parts = path.rsplit(".", 1)
        resref, ext = parts[0].lower(), parts[1].lower()
        data = inst.resource(resref, ext)
        if data is None:
            raise ValueError(f"Resource not found: {path}")
        return {
            "uri": uri,
            "mimeType": "application/octet-stream",
            "blob": base64.b64encode(data).decode("ascii"),
        }

    # ── 2da/{table_name} ──────────────────────────────────────────────────
    if resource_type == "2da":
        table_name = path.strip().lower() or "appearance"
        from gmodular.mcp.tools.discovery import find_resource_bytes
        raw = find_resource_bytes(inst, table_name, "2da")
        try:
            from io import BytesIO
            from pykotor.resource.formats.twoda import read_2da
            tda = read_2da(BytesIO(raw))
            headers = list(tda.get_headers())
            rows = []
            for i in range(min(tda.get_height(), 500)):
                rows.append({h: tda.get_cell_safe(i, h, "") for h in headers})
            payload = {"columns": headers, "row_count": tda.get_height(), "rows": rows}
        except Exception:
            # Text fallback
            text = raw.decode("latin-1", errors="replace")
            payload = {"raw": text}
        return {"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, indent=2)}

    # ── tlk/{strref} ──────────────────────────────────────────────────────
    if resource_type == "tlk":
        strref_str = path.strip()
        if not strref_str.isdigit():
            raise ValueError(f"TLK strref must be an integer, got: {strref_str}")
        strref = int(strref_str)
        tlk_path = inst.tlk_path()
        if tlk_path is None:
            raise ValueError(f"dialog.tlk not found in {inst.game} installation.")
        try:
            from io import BytesIO
            from pykotor.resource.formats.tlk import read_tlk
            raw = tlk_path.read_bytes()
            tlk = read_tlk(BytesIO(raw))
            entry = tlk.get(strref)
            text = entry.text if entry else ""
        except Exception as exc:
            from gmodular.formats.tlk_reader import TLKReader
            tlk_obj = TLKReader.from_path(str(tlk_path))
            entry = tlk_obj.entries[strref] if 0 <= strref < len(tlk_obj.entries) else None
            text = entry.text if entry else f"[strref {strref} not found: {exc}]"
        return {"uri": uri, "mimeType": "text/plain", "text": text}

    # ── walkmesh-diagram/{resref}.wok ────────────────────────────────────
    if resource_type == "walkmesh-diagram":
        resref = path.strip().lower()
        for sfx in (".wok", ".pwk", ".dwk"):
            if resref.endswith(sfx):
                resref = resref[: -len(sfx)]
                break
        from gmodular.mcp.tools.discovery import find_resource_bytes

        data: Optional[bytes] = None
        for ext in ("wok", "pwk", "dwk"):
            try:
                data = find_resource_bytes(inst, resref, ext)
                break
            except ValueError:
                continue
        if data is None:
            raise ValueError(f"Walkmesh {resref}.wok/.pwk/.dwk not found.")

        from io import BytesIO
        from pykotor.resource.formats.bwm import read_bwm
        from pykotor.tools.walkmesh_render_diagram import render_bwm_validation_diagram_lines
        bwm = read_bwm(BytesIO(data))
        lines = render_bwm_validation_diagram_lines(bwm, use_color=False)
        return {"uri": uri, "mimeType": "text/plain", "text": "\n".join(lines)}

    raise ValueError(f"Unsupported kotor:// resource type: {resource_type}")
