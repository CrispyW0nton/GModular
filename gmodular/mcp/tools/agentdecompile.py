"""MCP tools — AgentDecompile / Ghidra bridge.

These tools bridge GModular's KotOR MCP server with the AgentDecompile
proxy server (``agdec-proxy``), which connects to the live Ghidra Shared
Repository at 170.9.241.140 containing the fully-analysed ``swkotor.exe``
binary (24 591 functions).

Architecture
------------
GModular MCP client
        │
        ▼
  gmodular.mcp.tools.agentdecompile   ← this module
        │  (async HTTP / JSON-RPC)
        ▼
  agdec-proxy  (uvx agentdecompile-proxy)
        │  (MCP streamable-HTTP)
        ▼
  170.9.241.140:8080/mcp/   ← AgentDecompile HTTP backend
        │  (Ghidra Server protocol)
        ▼
  170.9.241.140:13100        ← Ghidra Shared Server "Odyssey"
        │
        ▼
  swkotor.exe / swkotor2.exe (24 591+ analysed functions)

The proxy is configured via the ``agdec-proxy`` MCP server entry in
``.vscode/mcp.json``.  When GModular's own MCP server is running, these
tools forward calls to the AgentDecompile backend and enrich the results
with KotOR-domain context from GModular's own parsers.

Usage from an MCP client
------------------------
    ghidra_search_symbols   query="CExoString"  programPath="/K1/k1_win_gog_swkotor.exe"
    ghidra_decompile        functionIdentifier="FUN_004041f0"
    ghidra_list_functions   prefix="CExo"  limit=20
    ghidra_get_program_info
    ghidra_cross_reference  address="0x004041f0"
    ghidra_search_strings   query="KOTOR" limit=50
    ghidra_find_function    name="WinMain"

Environment
-----------
AGDEC_SERVER_URL  — URL of the AgentDecompile HTTP backend
                    (default: http://170.9.241.140:8080/mcp/)
AGDEC_PROGRAM_PATH — default program path inside the Ghidra project
                    (default: /K1/k1_win_gog_swkotor.exe)
"""
from __future__ import annotations

import asyncio
import base64
import http.client
import json
import logging
import os
from typing import Any, Dict, List, Optional

from gmodular.mcp._formatting import json_content

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
# These can be overridden via environment variables.

_AGDEC_SERVER_URL: str = os.environ.get(
    "AGDEC_SERVER_URL", "http://170.9.241.140:8080/mcp/"
)
_DEFAULT_PROGRAM: str = os.environ.get(
    "AGDEC_PROGRAM_PATH", "/K1/k1_win_gog_swkotor.exe"
)
_GHIDRA_SERVER_HOST: str = os.environ.get(
    "AGENTDECOMPILE_HTTP_GHIDRA_SERVER_HOST", "170.9.241.140"
)
_GHIDRA_SERVER_PORT: int = int(
    os.environ.get("AGENTDECOMPILE_HTTP_GHIDRA_SERVER_PORT", "13100")
)
_GHIDRA_REPOSITORY: str = os.environ.get(
    "AGENTDECOMPILE_HTTP_GHIDRA_SERVER_REPOSITORY", "Odyssey"
)
_GHIDRA_USERNAME: str = os.environ.get("AGENTDECOMPILE_GHIDRA_USERNAME", "OpenKotOR")
_GHIDRA_PASSWORD: str = os.environ.get("AGENTDECOMPILE_GHIDRA_PASSWORD", "idekanymore")

# MCP session state (shared for this process lifetime)
_mcp_session_id: Optional[str] = None
_session_counter: int = 0


# ═══════════════════════════════════════════════════════════════════════════
#  Tool schemas
# ═══════════════════════════════════════════════════════════════════════════

def get_tools() -> List[Dict[str, Any]]:
    """Return AgentDecompile/Ghidra bridge tool schemas."""
    return [
        {
            "name": "ghidra_get_program_info",
            "description": (
                "Return metadata for the loaded KotOR binary in the Ghidra "
                "shared repository: language, compiler, function count, "
                "import/export counts. No arguments required."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "programPath": {
                        "type": "string",
                        "description": (
                            "Ghidra project path (default: /K1/k1_win_gog_swkotor.exe)"
                        ),
                    }
                },
            },
        },
        {
            "name": "ghidra_search_symbols",
            "description": (
                "Search for symbols (functions, labels, data) in swkotor.exe "
                "by name substring. Ideal for finding KotOR engine classes such "
                "as CExoString, CExoLocString, CNWSCreature, etc. "
                "Returns address, name, type for each match."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to match against symbol names",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 200,
                        "description": "Maximum results to return",
                    },
                    "programPath": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "ghidra_list_functions",
            "description": (
                "List functions in swkotor.exe, optionally filtered by a name "
                "prefix. Returns address, name, size for each function. Useful "
                "for exploring KotOR engine subsystems (e.g. all functions whose "
                "name starts with 'CNWSObject', 'CExo', 'GFF', etc.)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "Optional name prefix filter (e.g. 'CExo')",
                    },
                    "offset": {
                        "type": "integer",
                        "default": 0,
                        "description": "Pagination offset",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 30,
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "programPath": {"type": "string"},
                },
            },
        },
        {
            "name": "ghidra_find_function",
            "description": (
                "Locate a specific function in swkotor.exe by exact name or "
                "address. Returns address, decompiled signature, and size."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact function name (e.g. 'WinMain', 'FUN_004041f0')",
                    },
                    "address": {
                        "type": "string",
                        "description": "Hex address (e.g. '0x004041f0')",
                    },
                    "programPath": {"type": "string"},
                },
            },
        },
        {
            "name": "ghidra_decompile",
            "description": (
                "Decompile a function from swkotor.exe to C-like pseudocode using "
                "Ghidra's decompiler. Accepts function name or address. Returns "
                "decompiled C code enriched with KotOR context annotations. "
                "Limit and offset support pagination for long functions."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "functionIdentifier": {
                        "type": "string",
                        "description": "Function name or hex address",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "description": "Max lines of decompiled output",
                    },
                    "offset": {
                        "type": "integer",
                        "default": 0,
                        "description": "Line offset for pagination",
                    },
                    "includeDisassembly": {
                        "type": "boolean",
                        "default": False,
                        "description": "Also include raw disassembly",
                    },
                    "programPath": {"type": "string"},
                },
                "required": ["functionIdentifier"],
            },
        },
        {
            "name": "ghidra_cross_reference",
            "description": (
                "Find cross-references to or from an address in swkotor.exe. "
                "Mode 'to' shows callers of a function. Mode 'from' shows "
                "callees. Returns address, function context, and reference type."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Hex address or symbol name",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["to", "from"],
                        "default": "to",
                        "description": "'to' = callers, 'from' = callees",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 30,
                        "maximum": 200,
                    },
                    "programPath": {"type": "string"},
                },
                "required": ["address"],
            },
        },
        {
            "name": "ghidra_search_strings",
            "description": (
                "Search string literals in swkotor.exe. Useful for finding file "
                "format signatures, error messages, resource references, and "
                "scripting keywords embedded in the KotOR engine binary."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to search for in string literals",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 30,
                        "maximum": 200,
                    },
                    "programPath": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "ghidra_list_imports",
            "description": (
                "List imported DLL functions used by swkotor.exe. Useful for "
                "understanding the OS/runtime surface: DirectX calls, Win32 "
                "file I/O, memory allocators, etc."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "maximum": 500,
                    },
                    "filter": {
                        "type": "string",
                        "description": "Optional substring filter on import names",
                    },
                    "programPath": {"type": "string"},
                },
            },
        },
        {
            "name": "ghidra_analyze_vtables",
            "description": (
                "Analyse C++ vtables in swkotor.exe at a given address. KotOR "
                "uses an extensive C++ class hierarchy — this tool reveals virtual "
                "method tables for classes like CNWSCreature, CExoString, etc. "
                "Returns vtable entries with function pointers and resolved names."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "vtableAddress": {
                        "type": "string",
                        "description": "Hex address of the vtable",
                    },
                    "maxEntries": {
                        "type": "integer",
                        "default": 50,
                        "maximum": 200,
                    },
                    "programPath": {"type": "string"},
                },
                "required": ["vtableAddress"],
            },
        },
        {
            "name": "ghidra_data_flow",
            "description": (
                "Perform backward or forward data-flow / taint analysis on a "
                "function in swkotor.exe using Ghidra P-code. Useful for tracing "
                "where a GFF field value comes from, how a resource resref "
                "propagates, or what function ultimately writes a memory address."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "functionAddress": {
                        "type": "string",
                        "description": "Hex address of the function to analyse",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["backward", "forward", "variable_accesses"],
                        "default": "backward",
                    },
                    "startAddress": {
                        "type": "string",
                        "description": "Starting address for the trace (optional)",
                    },
                    "variableName": {
                        "type": "string",
                        "description": "Variable name for 'variable_accesses' mode",
                    },
                    "programPath": {"type": "string"},
                },
                "required": ["functionAddress"],
            },
        },
        {
            "name": "ghidra_export_c",
            "description": (
                "Export the decompiled C/C++ source for a function or the entire "
                "program from swkotor.exe via Ghidra's CppExporter. Useful for "
                "offline analysis, diff-ing engine versions (KotOR 1 vs 2), or "
                "feeding decompiled code to downstream analysis tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "outputPath": {
                        "type": "string",
                        "description": "Server-side output path for the .cpp file",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["cpp", "c", "gzf", "sarif"],
                        "default": "cpp",
                    },
                    "includeTypes": {"type": "boolean", "default": True},
                    "includeGlobals": {"type": "boolean", "default": True},
                    "programPath": {"type": "string"},
                },
                "required": ["outputPath"],
            },
        },
        {
            "name": "ghidra_kotor_function_map",
            "description": (
                "GModular enrichment tool — queries the Ghidra backend for known "
                "KotOR engine functions and maps them to GModular's own format "
                "constants and class names. Returns a cross-reference table "
                "linking Ghidra symbols to GModular concepts (GFF field types, "
                "resource types, GIT object classes, etc.). "
                "This is GModular-specific and not part of the standard "
                "AgentDecompile tool set."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "gff",
                            "lyt",
                            "mdl",
                            "git",
                            "erf",
                            "twoda",
                            "all",
                        ],
                        "default": "all",
                        "description": "GModular format category to map",
                    },
                    "limit": {"type": "integer", "default": 40},
                },
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  AgentDecompile HTTP client helpers
# ═══════════════════════════════════════════════════════════════════════════

async def _agdec_call(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Forward a single tool call to the AgentDecompile MCP backend.

    Uses stdlib asyncio + http.client so no extra deps are required.
    Returns the parsed JSON result dict, or an error dict on failure.
    """
    global _mcp_session_id, _session_counter

    url = _AGDEC_SERVER_URL.rstrip("/")
    if url.endswith("/mcp"):
        host_port = url[len("http://"):url.rfind("/mcp")]
    else:
        # Normalise: add /mcp if not present
        host_port = url[len("http://"):]
        url = url + "/mcp"

    # Build JSON-RPC request
    _session_counter += 1
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": _session_counter,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": args,
            },
        }
    ).encode()

    # Build auth headers
    creds = base64.b64encode(
        f"{_GHIDRA_USERNAME}:{_GHIDRA_PASSWORD}".encode()
    ).decode()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Basic {creds}",
        "X-Ghidra-Server-Host": _GHIDRA_SERVER_HOST,
        "X-Ghidra-Server-Port": str(_GHIDRA_SERVER_PORT),
        "X-Ghidra-Repository": _GHIDRA_REPOSITORY,
    }
    if _mcp_session_id:
        headers["Mcp-Session-Id"] = _mcp_session_id

    try:
        # Run blocking HTTP in a thread pool to keep async loop happy
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: _sync_http_post(host_port, url, payload, headers)
        )
        # Persist session ID if server returned one
        if isinstance(result, dict) and "sessionId" in result:
            _mcp_session_id = result["sessionId"]
        return result
    except Exception as exc:
        log.warning("AgentDecompile call %s failed: %s", tool_name, exc)
        return {"error": str(exc), "tool": tool_name}


def _sync_http_post(
    host_port: str, url: str, body: bytes, headers: Dict[str, str]
) -> Dict[str, Any]:
    """Synchronous HTTP POST; runs in thread pool from _agdec_call."""
    path = "/" + url.split("/", 3)[-1] if url.count("/") >= 3 else "/mcp"

    try:
        conn = http.client.HTTPConnection(host_port, timeout=30)
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()

        raw = resp.read().decode("utf-8", errors="replace")
        conn.close()

        # SSE or chunked: grab first JSON object
        if "data:" in raw:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break

        data = json.loads(raw)
        if "result" in data:
            return data["result"]
        if "error" in data:
            return {"error": data["error"]}
        return data

    except Exception as exc:
        raise RuntimeError(f"HTTP POST to {host_port}{path} failed: {exc}") from exc


def _extract_text(result: Dict[str, Any]) -> str:
    """Pull text content out of an MCP tool result."""
    if "content" in result:
        parts = result["content"]
        if isinstance(parts, list):
            texts = []
            for part in parts:
                if isinstance(part, dict):
                    texts.append(part.get("text", str(part)))
                else:
                    texts.append(str(part))
            return "\n".join(texts)
        return str(parts)
    if "error" in result:
        return f"[AgentDecompile error] {result['error']}"
    return json.dumps(result, indent=2)


def _resolve_program(arguments: Dict[str, Any]) -> str:
    """Return the program path from arguments or the default."""
    return arguments.get("programPath") or _DEFAULT_PROGRAM


# ═══════════════════════════════════════════════════════════════════════════
#  Tool handlers
# ═══════════════════════════════════════════════════════════════════════════

async def handle_get_program_info(arguments: Dict[str, Any]) -> Any:
    """Return metadata for the loaded KotOR binary."""
    prog = _resolve_program(arguments)
    result = await _agdec_call("get-current-program", {"programPath": prog})
    return json_content(result)


async def handle_search_symbols(arguments: Dict[str, Any]) -> Any:
    """Search symbols in swkotor.exe by name substring."""
    prog = _resolve_program(arguments)
    result = await _agdec_call(
        "search-symbols",
        {
            "programPath": prog,
            "query": arguments["query"],
            "limit": arguments.get("limit", 20),
        },
    )
    return json_content(result)


async def handle_list_functions(arguments: Dict[str, Any]) -> Any:
    """List functions, with optional name prefix filter."""
    prog = _resolve_program(arguments)
    params: Dict[str, Any] = {
        "programPath": prog,
        "offset": arguments.get("offset", 0),
        "limit": arguments.get("limit", 30),
    }
    prefix = arguments.get("prefix", "")
    if prefix:
        # Use search-symbols for prefix filtering (faster than list-functions)
        result = await _agdec_call(
            "search-symbols",
            {"programPath": prog, "query": prefix, "limit": params["limit"]},
        )
    else:
        result = await _agdec_call("list-functions", params)
    return json_content(result)


async def handle_find_function(arguments: Dict[str, Any]) -> Any:
    """Locate a specific function by name or address."""
    prog = _resolve_program(arguments)
    identifier = arguments.get("name") or arguments.get("address", "")
    result = await _agdec_call(
        "get-functions",
        {"programPath": prog, "identifier": identifier},
    )
    return json_content(result)


async def handle_decompile(arguments: Dict[str, Any]) -> Any:
    """Decompile a function to C pseudocode."""
    prog = _resolve_program(arguments)
    result = await _agdec_call(
        "decompile-function",
        {
            "programPath": prog,
            "functionIdentifier": arguments["functionIdentifier"],
            "limit": arguments.get("limit", 100),
            "offset": arguments.get("offset", 0),
            "includeDisassembly": arguments.get("includeDisassembly", False),
        },
    )
    return json_content(result)


async def handle_cross_reference(arguments: Dict[str, Any]) -> Any:
    """Find cross-references to/from an address."""
    prog = _resolve_program(arguments)
    result = await _agdec_call(
        "get-references",
        {
            "programPath": prog,
            "address": arguments["address"],
            "mode": arguments.get("mode", "to"),
            "limit": arguments.get("limit", 30),
        },
    )
    return json_content(result)


async def handle_search_strings(arguments: Dict[str, Any]) -> Any:
    """Search string literals in the binary."""
    prog = _resolve_program(arguments)
    result = await _agdec_call(
        "search-strings",
        {
            "programPath": prog,
            "query": arguments["query"],
            "limit": arguments.get("limit", 30),
        },
    )
    return json_content(result)


async def handle_list_imports(arguments: Dict[str, Any]) -> Any:
    """List DLL imports."""
    prog = _resolve_program(arguments)
    params: Dict[str, Any] = {
        "programPath": prog,
        "limit": arguments.get("limit", 50),
    }
    result = await _agdec_call("list-imports", params)

    # Apply optional client-side filter
    flt = arguments.get("filter", "").lower()
    if flt and isinstance(result, dict) and "content" in result:
        text = _extract_text(result)
        filtered = [ln for ln in text.splitlines() if flt in ln.lower()]
        result = {"content": [{"type": "text", "text": "\n".join(filtered)}]}

    return json_content(result)


async def handle_analyze_vtables(arguments: Dict[str, Any]) -> Any:
    """Analyse a C++ vtable."""
    prog = _resolve_program(arguments)
    result = await _agdec_call(
        "analyze-vtables",
        {
            "programPath": prog,
            "mode": "analyze",
            "vtableAddress": arguments["vtableAddress"],
            "maxEntries": arguments.get("maxEntries", 50),
        },
    )
    return json_content(result)


async def handle_data_flow(arguments: Dict[str, Any]) -> Any:
    """Taint / data-flow analysis."""
    prog = _resolve_program(arguments)
    params: Dict[str, Any] = {
        "programPath": prog,
        "functionAddress": arguments["functionAddress"],
        "direction": arguments.get("direction", "backward"),
    }
    if arguments.get("startAddress"):
        params["startAddress"] = arguments["startAddress"]
    if arguments.get("variableName"):
        params["variableName"] = arguments["variableName"]

    result = await _agdec_call("analyze-data-flow", params)
    return json_content(result)


async def handle_export_c(arguments: Dict[str, Any]) -> Any:
    """Export decompiled C/C++ source."""
    prog = _resolve_program(arguments)
    result = await _agdec_call(
        "export",
        {
            "programPath": prog,
            "outputPath": arguments["outputPath"],
            "format": arguments.get("format", "cpp"),
            "includeTypes": arguments.get("includeTypes", True),
            "includeGlobals": arguments.get("includeGlobals", True),
        },
    )
    return json_content(result)


# ── GModular-specific enrichment tool ──────────────────────────────────────

# Known KotOR engine → GModular mapping hints.
# These were identified by cross-referencing Ghidra symbol names in the
# Odyssey repository with GModular's own format constants.
_KOTOR_GMODULAR_MAP: Dict[str, List[Dict[str, str]]] = {
    "gff": [
        {"ghidra": "CExoLocString", "gmodular": "GFFFieldType.CEXOLOCSTRING", "desc": "Localised string field"},
        {"ghidra": "CExoString", "gmodular": "GFFFieldType.CEXOSTRING", "desc": "Non-localised string"},
        {"ghidra": "GFF_ReadFile", "gmodular": "GFFReader.parse()", "desc": "GFF binary reader"},
        {"ghidra": "GFF_WriteFile", "gmodular": "GFFWriter.__init__(root)", "desc": "GFF binary writer"},
        {"ghidra": "CResGFF", "gmodular": "gff_types.GFFRoot", "desc": "GFF root struct container"},
    ],
    "lyt": [
        {"ghidra": "CLYTFile", "gmodular": "LYTParser", "desc": "Room layout file parser"},
        {"ghidra": "CLYTRoom", "gmodular": "RoomPlacement", "desc": "Single room placement"},
        {"ghidra": "CVISFile", "gmodular": "VISParser", "desc": "Visibility list parser"},
    ],
    "mdl": [
        {"ghidra": "CModelMesh", "gmodular": "MeshData", "desc": "Mesh vertex/index data"},
        {"ghidra": "CTrimeshNode", "gmodular": "MDLParser", "desc": "MDL trimesh node"},
        {"ghidra": "CModelAnimation", "gmodular": "MDLParser (animations list)", "desc": "MDL animation"},
    ],
    "git": [
        {"ghidra": "CAreaGIT", "gmodular": "GITData", "desc": "GIT object container"},
        {"ghidra": "CGITPlaceable", "gmodular": "GITPlaceable", "desc": "Placeable instance"},
        {"ghidra": "CGITCreature", "gmodular": "GITCreature", "desc": "Creature instance"},
        {"ghidra": "CGITDoor", "gmodular": "GITDoor", "desc": "Door instance"},
        {"ghidra": "CGITTrigger", "gmodular": "GITTrigger", "desc": "Trigger area"},
        {"ghidra": "CGITWaypoint", "gmodular": "GITWaypoint", "desc": "Waypoint"},
    ],
    "erf": [
        {"ghidra": "CExoResMan", "gmodular": "ERFReader / BIFReader", "desc": "Resource manager"},
        {"ghidra": "CResStruct", "gmodular": "PackageResource", "desc": "ERF resource entry"},
    ],
    "twoda": [
        {"ghidra": "C2DA", "gmodular": "TwoDALoader / TwoDATable", "desc": "2DA table"},
        {"ghidra": "C2DAFile", "gmodular": "TwoDALoader.load()", "desc": "2DA file load"},
    ],
}


async def handle_kotor_function_map(arguments: Dict[str, Any]) -> Any:
    """Return a cross-reference table: Ghidra symbols ↔ GModular classes."""
    category = arguments.get("category", "all").lower()
    limit = int(arguments.get("limit", 40))

    if category == "all":
        rows: List[Dict[str, str]] = []
        for entries in _KOTOR_GMODULAR_MAP.values():
            rows.extend(entries)
    else:
        rows = list(_KOTOR_GMODULAR_MAP.get(category, []))

    rows = rows[:limit]

    # Enrich with live Ghidra data for the first few rows
    enriched = []
    for row in rows[:min(5, len(rows))]:
        symbol_name = row["ghidra"]
        live = await _agdec_call(
            "search-symbols",
            {"programPath": _DEFAULT_PROGRAM, "query": symbol_name, "limit": 3},
        )
        live_text = _extract_text(live)
        enriched.append({**row, "ghidra_live": live_text[:200]})
    for row in rows[min(5, len(rows)):]:
        enriched.append({**row, "ghidra_live": "(not fetched)"})

    return json_content(
        {
            "category": category,
            "count": len(enriched),
            "map": enriched,
            "note": (
                "ghidra_live field contains live Ghidra symbol search results "
                "for the first 5 entries. Set AGDEC_SERVER_URL to override backend."
            ),
        }
    )
