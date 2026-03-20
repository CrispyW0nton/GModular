"""
GModular — Ghostworks Pipeline IPC Bridge (urllib, no external deps)
====================================================================
Pure-Python HTTP client for communicating with GhostRigger (port 7001)
and GhostScripter (port 7002) using only stdlib urllib.

This module is the low-level transport layer. The MCP tool wrappers in
gmodular/mcp/tools/ghostworks.py call these functions.

Port contract (PIPELINE_SPEC.md §3):
  GhostRigger   port 7001 — POST /ping, /open_utc, /open_utp, /open_utd,
                             /get_blueprint, /list_blueprints,
                             /set_field, /get_field, /set_fields_bulk,
                             /save_blueprint
  GhostScripter port 7002 — POST /ping, /open_script, /get_script,
                             /compile, /decompile, /set_resref, /list_scripts
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

GHOSTRIGGER_PORT   = 7001
GHOSTSCRIPTER_PORT = 7002
DEFAULT_TIMEOUT    = 3.0


# ──────────────────────────────────────────────────────────────────────────────
# Low-level HTTP helper
# ──────────────────────────────────────────────────────────────────────────────

def _post(port: int, action: str, payload: Dict[str, Any],
          timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    POST JSON payload to http://127.0.0.1:<port>/<action>.
    Returns parsed response dict.
    Raises ConnectionError on network failure, ValueError on non-JSON response.
    """
    url = f"http://127.0.0.1:{port}/{action}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return json.loads(raw)
        except Exception:
            raise ConnectionError(f"HTTP {exc.code} from {url}: {raw[:200]}")
    except urllib.error.URLError as exc:
        raise ConnectionError(f"Cannot reach {url}: {exc.reason}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Non-JSON response from {url}: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# GhostRigger bridge (port 7001)
# ──────────────────────────────────────────────────────────────────────────────

def ghostrigger_ping(timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Ping GhostRigger. Returns {"status":"ok","program":"GhostRigger",...}."""
    return _post(GHOSTRIGGER_PORT, "ping", {}, timeout=timeout)


def ghostrigger_open_blueprint(resref: str, blueprint_type: str,
                                fields: Optional[Dict] = None,
                                timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Open a blueprint in GhostRigger's field editor.
    blueprint_type: "utc" | "utp" | "utd"
    fields: optional initial field values dict
    Returns {"status":"ok","resref":"..."} on success.
    """
    bp_type = blueprint_type.lower().strip(".")
    if bp_type not in ("utc", "utp", "utd"):
        raise ValueError(f"blueprint_type must be utc/utp/utd, got: {blueprint_type!r}")
    payload: Dict[str, Any] = {"resref": resref}
    if fields:
        payload["fields"] = fields
    return _post(GHOSTRIGGER_PORT, f"open_{bp_type}", payload, timeout=timeout)


def ghostrigger_get_blueprint(resref: str,
                               timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Retrieve a blueprint by resref. Returns {"status":"ok","blueprint":{...}}."""
    return _post(GHOSTRIGGER_PORT, "get_blueprint", {"resref": resref}, timeout=timeout)


def ghostrigger_set_field(resref: str, field: str, value: Any,
                           timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Set a single field on an open blueprint."""
    return _post(GHOSTRIGGER_PORT, "set_field",
                 {"resref": resref, "field": field, "value": value}, timeout=timeout)


def ghostrigger_get_field(resref: str, field: str,
                           timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Get a single field value from an open blueprint."""
    return _post(GHOSTRIGGER_PORT, "get_field",
                 {"resref": resref, "field": field}, timeout=timeout)


def ghostrigger_set_fields_bulk(resref: str, fields: Dict[str, Any],
                                 timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Set multiple fields at once on an open blueprint."""
    return _post(GHOSTRIGGER_PORT, "set_fields_bulk",
                 {"resref": resref, "fields": fields}, timeout=timeout)


def ghostrigger_save_blueprint(resref: str, fields: Optional[Dict] = None,
                                blueprint_type: str = "utc",
                                timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Save an open blueprint (marks dirty=False in GhostRigger's registry)."""
    payload: Dict[str, Any] = {
        "blueprint": {
            "resref": resref,
            "type": blueprint_type,
            "fields": fields or {},
        }
    }
    return _post(GHOSTRIGGER_PORT, "save_blueprint", payload, timeout=timeout)


def ghostrigger_list_blueprints(timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """List all open blueprints. Returns {"status":"ok","blueprints":[...]}."""
    return _post(GHOSTRIGGER_PORT, "list_blueprints", {}, timeout=timeout)


# ──────────────────────────────────────────────────────────────────────────────
# GhostScripter bridge (port 7002)
# ──────────────────────────────────────────────────────────────────────────────

def ghostscripter_ping(timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Ping GhostScripter. Returns {"status":"ok","program":"GhostScripter",...}."""
    return _post(GHOSTSCRIPTER_PORT, "ping", {}, timeout=timeout)


def ghostscripter_open_script(resref: str, source: str = "",
                               timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Open a script in GhostScripter's editor.
    resref: script resref (no extension)
    source: optional NSS source to pre-populate
    Returns {"status":"ok","resref":"..."}.
    """
    payload: Dict[str, Any] = {"resref": resref}
    if source:
        payload["source"] = source
    return _post(GHOSTSCRIPTER_PORT, "open_script", payload, timeout=timeout)


def ghostscripter_get_script(resref: str,
                              timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Retrieve a script from GhostScripter's registry."""
    return _post(GHOSTSCRIPTER_PORT, "get_script", {"resref": resref}, timeout=timeout)


def ghostscripter_compile(resref: str, source: str = "",
                           timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Compile a script in GhostScripter.
    resref: script resref; source: optional source override.
    Returns {"status":"ok","success":bool,"errors":[...],"ncs":"<hex>"}.
    """
    payload: Dict[str, Any] = {"resref": resref}
    if source:
        payload["source"] = source
    return _post(GHOSTSCRIPTER_PORT, "compile", payload, timeout=timeout)


def ghostscripter_decompile(resref: str,
                             timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Decompile a compiled script (.ncs) back to NSS."""
    return _post(GHOSTSCRIPTER_PORT, "decompile", {"resref": resref}, timeout=timeout)


def ghostscripter_list_scripts(timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """List all open scripts in GhostScripter."""
    return _post(GHOSTSCRIPTER_PORT, "list_scripts", {}, timeout=timeout)


def ghostscripter_set_resref(old_resref: str, new_resref: str,
                              timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Rename a script's resref in GhostScripter's registry."""
    return _post(GHOSTSCRIPTER_PORT, "set_resref",
                 {"resref": old_resref, "new_resref": new_resref}, timeout=timeout)
