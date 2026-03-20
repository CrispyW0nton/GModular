"""
GModular — Ghostworks Pipeline MCP Tools
=========================================
MCP tools for interacting with GhostRigger (port 7001) and
GhostScripter (port 7002) from an AI agent or the MCP server.

Tools:
  ghostrigger_ping            — health check GhostRigger
  ghostrigger_open_blueprint  — open a UTC/UTP/UTD blueprint in GhostRigger
  ghostrigger_get_blueprint   — retrieve an open blueprint's fields
  ghostrigger_set_field       — set a single field on an open blueprint
  ghostrigger_set_fields_bulk — set multiple fields at once
  ghostrigger_save_blueprint  — save/commit an open blueprint
  ghostrigger_list_blueprints — list all open blueprints
  ghostscripter_ping           — health check GhostScripter
  ghostscripter_open_script    — open / create a script in GhostScripter
  ghostscripter_get_script     — retrieve an open script's source
  ghostscripter_compile        — compile a script to NCS
  ghostscripter_list_scripts   — list all open scripts

Architecture notes:
  - All functions are async wrappers around the synchronous
    gmodular.ipc.ghostworks_bridge helpers.
  - Connection failures return {"error": "..."} instead of raising.
  - No Qt dependency — safe to call from headless MCP server.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from gmodular.mcp._formatting import json_content

log = logging.getLogger(__name__)


def get_tools() -> List[Dict[str, Any]]:
    return [
        # ── GhostRigger ───────────────────────────────────────────────────────
        {
            "name": "ghostrigger_ping",
            "description": (
                "Health-check GhostRigger (the KotOR asset/blueprint editor). "
                "Returns program name, version, and port. "
                "Use this to confirm GhostRigger is running before sending blueprint commands."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "number", "default": 3.0,
                                "description": "Seconds to wait for a response"}
                },
            },
        },
        {
            "name": "ghostrigger_open_blueprint",
            "description": (
                "Open a KotOR blueprint (UTC creature, UTP placeable, or UTD door) "
                "in GhostRigger's field editor. "
                "Pass optional initial field values to pre-populate the editor. "
                "GhostRigger must be running and reachable on port 7001."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref", "blueprint_type"],
                "properties": {
                    "resref": {"type": "string",
                               "description": "Blueprint resource reference (≤16 chars, no extension)"},
                    "blueprint_type": {"type": "string", "enum": ["utc", "utp", "utd"],
                                       "description": "Blueprint type: utc (creature), utp (placeable), utd (door)"},
                    "fields": {"type": "object",
                               "description": "Optional initial field values dict (e.g. {\"FirstName\":\"Revan\",\"Tag\":\"REVAN\"})"},
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostrigger_get_blueprint",
            "description": (
                "Retrieve all field values for an open blueprint from GhostRigger. "
                "Returns the full fields dict including any edits made in the UI."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref"],
                "properties": {
                    "resref": {"type": "string", "description": "Blueprint resref"},
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostrigger_set_field",
            "description": (
                "Set a single GFF field on an open blueprint in GhostRigger. "
                "Example fields: FirstName, Tag, MaxHitPoints, ScriptSpawn, Conversation."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref", "field", "value"],
                "properties": {
                    "resref": {"type": "string"},
                    "field": {"type": "string", "description": "GFF field name (e.g. 'FirstName', 'Tag', 'MaxHitPoints')"},
                    "value": {"description": "New field value (string, int, float, or bool)"},
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostrigger_set_fields_bulk",
            "description": (
                "Set multiple GFF fields at once on an open blueprint in GhostRigger. "
                "More efficient than calling ghostrigger_set_field repeatedly."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref", "fields"],
                "properties": {
                    "resref": {"type": "string"},
                    "fields": {"type": "object",
                               "description": "Dict of {field_name: value} pairs"},
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostrigger_save_blueprint",
            "description": (
                "Save/commit an open blueprint in GhostRigger (clears the dirty flag). "
                "Optionally provide the full fields dict to overwrite."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref"],
                "properties": {
                    "resref": {"type": "string"},
                    "blueprint_type": {"type": "string", "enum": ["utc", "utp", "utd"],
                                       "default": "utc"},
                    "fields": {"type": "object",
                               "description": "Optional field dict to commit"},
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostrigger_list_blueprints",
            "description": (
                "List all blueprints currently open in GhostRigger. "
                "Returns an array of {resref, type, fields} objects."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        # ── GhostScripter ─────────────────────────────────────────────────────
        {
            "name": "ghostscripter_ping",
            "description": (
                "Health-check GhostScripter (the NWScript IDE). "
                "Returns program name, version, and port. "
                "Use this to confirm GhostScripter is running before sending script commands."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostscripter_open_script",
            "description": (
                "Open a NWScript (.nss) file in GhostScripter's editor. "
                "Pass optional NSS source to pre-populate. "
                "GhostScripter must be running on port 7002."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref"],
                "properties": {
                    "resref": {"type": "string",
                               "description": "Script resref (≤16 chars, no .nss extension)"},
                    "source": {"type": "string",
                               "description": "Optional NWScript source code to pre-load"},
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostscripter_get_script",
            "description": (
                "Retrieve the source and compiled status of an open script from GhostScripter."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref"],
                "properties": {
                    "resref": {"type": "string"},
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
        {
            "name": "ghostscripter_compile",
            "description": (
                "Compile a NWScript source file in GhostScripter. "
                "Returns success status, errors, warnings, and compiled NCS hex bytes."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["resref"],
                "properties": {
                    "resref": {"type": "string"},
                    "source": {"type": "string",
                               "description": "Optional source override (compiles this instead of the stored source)"},
                    "timeout": {"type": "number", "default": 10.0},
                },
            },
        },
        {
            "name": "ghostscripter_list_scripts",
            "description": (
                "List all scripts currently open in GhostScripter. "
                "Returns an array of {resref, source, compiled, dirty} objects."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "number", "default": 3.0},
                },
            },
        },
    ]


# ── Handler helpers ────────────────────────────────────────────────────────────

def _bridge():
    """Lazy import of the bridge to avoid circular deps."""
    from gmodular.ipc import ghostworks_bridge
    return ghostworks_bridge


def _safe_call(fn, *args, **kwargs) -> Dict[str, Any]:
    """Call a bridge function and convert ConnectionError to error dict."""
    try:
        return fn(*args, **kwargs)
    except ConnectionError as exc:
        return {"error": str(exc), "connected": False}
    except Exception as exc:
        log.debug("Ghostworks bridge error: %s", exc)
        return {"error": str(exc)}


# ── Async handlers ─────────────────────────────────────────────────────────────

async def handle_ghostrigger_ping(arguments: Dict[str, Any]) -> Any:
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(_bridge().ghostrigger_ping, timeout=timeout)
    return json_content(result)


async def handle_ghostrigger_open_blueprint(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    bp_type = arguments["blueprint_type"]
    fields = arguments.get("fields")
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(
        _bridge().ghostrigger_open_blueprint,
        resref, bp_type, fields, timeout=timeout
    )
    return json_content(result)


async def handle_ghostrigger_get_blueprint(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(_bridge().ghostrigger_get_blueprint, resref, timeout=timeout)
    return json_content(result)


async def handle_ghostrigger_set_field(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    field = arguments["field"]
    value = arguments["value"]
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(
        _bridge().ghostrigger_set_field, resref, field, value, timeout=timeout
    )
    return json_content(result)


async def handle_ghostrigger_set_fields_bulk(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    fields = arguments["fields"]
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(
        _bridge().ghostrigger_set_fields_bulk, resref, fields, timeout=timeout
    )
    return json_content(result)


async def handle_ghostrigger_save_blueprint(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    bp_type = arguments.get("blueprint_type", "utc")
    fields = arguments.get("fields")
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(
        _bridge().ghostrigger_save_blueprint,
        resref, fields, bp_type, timeout=timeout
    )
    return json_content(result)


async def handle_ghostrigger_list_blueprints(arguments: Dict[str, Any]) -> Any:
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(_bridge().ghostrigger_list_blueprints, timeout=timeout)
    return json_content(result)


async def handle_ghostscripter_ping(arguments: Dict[str, Any]) -> Any:
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(_bridge().ghostscripter_ping, timeout=timeout)
    return json_content(result)


async def handle_ghostscripter_open_script(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    source = arguments.get("source", "")
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(
        _bridge().ghostscripter_open_script, resref, source, timeout=timeout
    )
    return json_content(result)


async def handle_ghostscripter_get_script(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(_bridge().ghostscripter_get_script, resref, timeout=timeout)
    return json_content(result)


async def handle_ghostscripter_compile(arguments: Dict[str, Any]) -> Any:
    resref = arguments["resref"]
    source = arguments.get("source", "")
    timeout = float(arguments.get("timeout", 10.0))
    result = _safe_call(
        _bridge().ghostscripter_compile, resref, source, timeout=timeout
    )
    return json_content(result)


async def handle_ghostscripter_list_scripts(arguments: Dict[str, Any]) -> Any:
    timeout = float(arguments.get("timeout", 3.0))
    result = _safe_call(_bridge().ghostscripter_list_scripts, timeout=timeout)
    return json_content(result)
