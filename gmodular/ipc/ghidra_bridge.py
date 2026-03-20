"""GModular — Ghidra IPC Bridge (AgentDecompile integration).

This module provides a lightweight, synchronous HTTP client that GModular's
GUI and non-MCP code can use to query the AgentDecompile backend directly
(e.g. from the Inspector panel or a background analysis thread).

Architecture
------------

  GModular GUI / tool code
       │
       ▼
  GhidraIPCBridge.query(tool, args)   ← this class
       │  (background thread, non-blocking)
       ▼
  AgentDecompile HTTP endpoint
  http://170.9.241.140:8080/mcp/
       │
       ▼
  swkotor.exe (24 591 analysed functions)

Port contract
-------------
  GhostRigger    port 7001
  GhostScripter  port 7002
  GModular       port 7003
  AgentDecompile port 8080  (remote, 170.9.241.140)

Usage
-----
    from gmodular.ipc.ghidra_bridge import GhidraIPCBridge

    bridge = GhidraIPCBridge()
    # Synchronous (blocks caller thread — use from a worker thread):
    result = bridge.query("search-symbols", {"query": "CExoString", "limit": 5})
    print(result)

    # Fire-and-forget (returns immediately):
    bridge.query_async("search-symbols", {"query": "CExoString"}, callback=print)

Qt integration
--------------
If qtpy/Qt is available, ``GhidraIPCBridge`` emits a ``result_ready(dict)``
signal after each async query so GUI code can connect without polling. Works with any Qt backend via qtpy.
"""
from __future__ import annotations

import base64
import http.client
import json
import logging
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# Default AgentDecompile backend — matches the agdec-proxy config
_DEFAULT_BACKEND = "http://170.9.241.140:8080/mcp/"
_DEFAULT_PROGRAM = "/K1/k1_win_gog_swkotor.exe"
_GHIDRA_HOST = "170.9.241.140"
_GHIDRA_PORT = 13100
_GHIDRA_REPO = "Odyssey"
_GHIDRA_USER = "OpenKotOR"
_GHIDRA_PASS = "idekanymore"

try:
    from qtpy.QtCore import QObject, Signal
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QObject = object  # type: ignore[misc,assignment]

    class Signal:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None: pass
        def __set_name__(self, owner: Any, name: str) -> None: pass
        def emit(self, *args: Any) -> None: pass
        def connect(self, *args: Any) -> None: pass


# ── Qt-compatible bridge class ─────────────────────────────────────────────

class GhidraIPCBridge(QObject if _HAS_QT else object):  # type: ignore[misc]
    """HTTP bridge to the AgentDecompile / Ghidra backend.

    Parameters
    ----------
    backend_url:
        Base URL of the AgentDecompile MCP server.
        Default: ``http://170.9.241.140:8080/mcp/``
    program_path:
        Default Ghidra project path for tool calls.
        Default: ``/K1/k1_win_gog_swkotor.exe``
    timeout:
        HTTP request timeout in seconds (default: 15).
    """

    if _HAS_QT:
        result_ready = Signal(dict)   # emitted on each async result
        error_occurred = Signal(str)  # emitted on network/parse errors

    def __init__(
        self,
        backend_url: str = _DEFAULT_BACKEND,
        program_path: str = _DEFAULT_PROGRAM,
        timeout: int = 15,
    ) -> None:
        if _HAS_QT:
            super().__init__()
        self._backend_url = backend_url.rstrip("/") + "/"
        self._program_path = program_path
        self._timeout = timeout
        self._session_counter = 0
        self._session_id: Optional[str] = None
        self._work_queue: queue.Queue[Optional[Dict[str, Any]]] = queue.Queue()
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="ghidra-ipc"
        )
        self._worker.start()
        log.info("GhidraIPCBridge started → %s", self._backend_url)

    # ── Public API ────────────────────────────────────────────────────────

    def query(self, tool: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Synchronous query — blocks the calling thread.

        Returns the parsed result dict, or an error dict with key ``"error"``.
        Use this only from non-UI threads (e.g. background workers).
        """
        args = args or {}
        if "programPath" not in args:
            args["programPath"] = self._program_path
        try:
            return self._http_call(tool, args)
        except Exception as exc:
            log.warning("GhidraIPCBridge.query(%s) failed: %s", tool, exc)
            return {"error": str(exc), "tool": tool}

    def query_async(
        self,
        tool: str,
        args: Optional[Dict[str, Any]] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """Non-blocking query — executes on the background worker thread.

        Results are delivered via:
        1. The ``callback`` function (if provided).
        2. The ``result_ready`` Qt signal (if qtpy/Qt is available).
        """
        args = args or {}
        if "programPath" not in args:
            args["programPath"] = self._program_path
        self._work_queue.put({"tool": tool, "args": args, "callback": callback})

    def search_symbols(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search KotOR symbols by name substring.

        Returns a list of ``{name, address, type}`` dicts.
        """
        result = self.query("search-symbols", {"query": query, "limit": limit})
        return self._parse_list(result)

    def decompile(self, function_identifier: str, limit: int = 100) -> str:
        """Decompile a function and return the C pseudocode as a string."""
        result = self.query(
            "decompile-function",
            {"functionIdentifier": function_identifier, "limit": limit},
        )
        return self._extract_text(result)

    def get_function(self, name_or_address: str) -> Dict[str, Any]:
        """Look up a single function by name or address."""
        return self.query(
            "get-functions", {"identifier": name_or_address}
        )

    def list_functions(
        self, prefix: str = "", limit: int = 30, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List functions with an optional name prefix filter."""
        if prefix:
            result = self.query(
                "search-symbols", {"query": prefix, "limit": limit}
            )
        else:
            result = self.query(
                "list-functions",
                {"limit": limit, "offset": offset},
            )
        return self._parse_list(result)

    def cross_reference(
        self, address: str, mode: str = "to", limit: int = 30
    ) -> List[Dict[str, Any]]:
        """Return callers (mode='to') or callees (mode='from') of an address."""
        result = self.query(
            "get-references",
            {"address": address, "mode": mode, "limit": limit},
        )
        return self._parse_list(result)

    def program_info(self) -> Dict[str, Any]:
        """Return metadata for the default KotOR binary."""
        return self.query("get-current-program")

    def is_available(self) -> bool:
        """Return True if the backend is reachable (fast connectivity check)."""
        try:
            info = self.program_info()
            return "error" not in info
        except Exception:
            return False

    def shutdown(self) -> None:
        """Stop the background worker thread."""
        self._work_queue.put(None)  # sentinel

    # ── Private helpers ───────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """Background thread: drain work queue and fire callbacks."""
        while True:
            item = self._work_queue.get()
            if item is None:
                break  # shutdown sentinel
            tool = item["tool"]
            args = item["args"]
            callback = item.get("callback")
            try:
                result = self._http_call(tool, args)
            except Exception as exc:
                result = {"error": str(exc), "tool": tool}

            if callback:
                try:
                    callback(result)
                except Exception as exc:
                    log.warning("GhidraIPCBridge callback error: %s", exc)

            if _HAS_QT and hasattr(self, "result_ready"):
                try:
                    self.result_ready.emit(result)
                except Exception:
                    pass

    def _http_call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Perform a synchronous MCP tool call over HTTP."""
        self._session_counter += 1
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self._session_counter,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            }
        ).encode()

        creds = base64.b64encode(
            f"{_GHIDRA_USER}:{_GHIDRA_PASS}".encode()
        ).decode()
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Basic {creds}",
            "X-Ghidra-Server-Host": _GHIDRA_HOST,
            "X-Ghidra-Server-Port": str(_GHIDRA_PORT),
            "X-Ghidra-Repository": _GHIDRA_REPO,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        # Parse host:port from URL
        url = self._backend_url
        # e.g. http://170.9.241.140:8080/mcp/
        if url.startswith("http://"):
            rest = url[len("http://"):]
        else:
            rest = url
        slash_pos = rest.find("/")
        host_port = rest[:slash_pos] if slash_pos != -1 else rest
        path = rest[slash_pos:] if slash_pos != -1 else "/"

        conn = http.client.HTTPConnection(host_port, timeout=self._timeout)
        try:
            conn.request("POST", path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", errors="replace")

            # Capture session ID
            sess = resp.getheader("Mcp-Session-Id")
            if sess:
                self._session_id = sess
        finally:
            conn.close()

        # SSE streams: grab first data: line
        if "data:" in raw:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"error": f"Non-JSON response: {raw[:200]}"}

        if "result" in data:
            return data["result"]
        if "error" in data:
            return {"error": data["error"]}
        return data

    @staticmethod
    def _extract_text(result: Dict[str, Any]) -> str:
        """Pull plain text out of an MCP result payload."""
        if "content" in result:
            parts = result["content"]
            if isinstance(parts, list):
                return "\n".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p)
                    for p in parts
                )
            return str(parts)
        if "error" in result:
            return f"[Ghidra error] {result['error']}"
        return json.dumps(result, indent=2)

    @staticmethod
    def _parse_list(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Try to extract a list from a result; fall back to wrapping text."""
        if isinstance(result, list):
            return result
        if "content" in result:
            parts = result["content"]
            if isinstance(parts, list):
                combined = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in parts
                )
                try:
                    parsed = json.loads(combined)
                    if isinstance(parsed, list):
                        return parsed
                    if isinstance(parsed, dict) and "items" in parsed:
                        return parsed["items"]
                except json.JSONDecodeError:
                    pass
                return [{"text": combined}]
        if "error" in result:
            return [{"error": result["error"]}]
        return [result]


# ── Module-level singleton (lazy, created on first use) ────────────────────

_bridge_instance: Optional[GhidraIPCBridge] = None
_bridge_lock = threading.Lock()


def get_bridge(
    backend_url: str = _DEFAULT_BACKEND,
    program_path: str = _DEFAULT_PROGRAM,
) -> GhidraIPCBridge:
    """Return the module-level GhidraIPCBridge singleton.

    Creates it on first call with the supplied defaults.
    """
    global _bridge_instance
    if _bridge_instance is None:
        with _bridge_lock:
            if _bridge_instance is None:
                _bridge_instance = GhidraIPCBridge(
                    backend_url=backend_url,
                    program_path=program_path,
                )
    return _bridge_instance
