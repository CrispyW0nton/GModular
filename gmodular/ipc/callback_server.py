"""
GModular — Self-Hosted IPC Callback Server (port 7003)
Provides a minimal HTTP REST server so GhostScripter and GhostRigger
can push events *back* to GModular (compile results, model updates, etc.)

Architecture:
  - Runs in background thread (non-blocking for Qt)
  - Uses Python's built-in http.server — zero extra deps
  - Qt signals deliver events to main thread via queue + drain timer
  - GhostScripter can POST to /api/compile_result  → triggers log + refresh
  - GhostRigger  can POST to /api/model_ready       → triggers model import prompt
  - General status at GET /api/status

Endpoints:
  GET  /api/status                → {"app":"GModular","version":"1.0.0","ready":true}
  POST /api/compile_result        → {"success":true,"script":"name","message":"..."}
  POST /api/model_ready           → {"model":"name","mdl_path":"...","mdx_path":"..."}
  POST /api/script_opened         → {"script":"name"}
  POST /api/git_updated           → {"path":"...","objects":42}
"""
from __future__ import annotations
import json
import logging
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Dict, Any

try:
    from qtpy.QtCore import QObject, Signal, QTimer
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QObject = object   # type: ignore[misc,assignment]
    class Signal:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def __set_name__(self, owner, name): pass

log = logging.getLogger(__name__)

GMODULAR_CALLBACK_PORT = 7003
GMODULAR_VERSION = "2.0.0"


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP Request Handler
# ─────────────────────────────────────────────────────────────────────────────

class _GModularRequestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — routes to the server's event queue."""

    def log_message(self, format, *args):
        log.debug(f"IPC: {format % args}")

    def _json_response(self, data: dict, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Optional[dict]:
        """Read and parse the request body as JSON.
        Returns the parsed dict, an empty dict when Content-Length is 0,
        or None when the body is present but unparseable.
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                raw = self.rfile.read(length)
                return json.loads(raw)
            return {}   # No body — treat as empty dict (not an error)
        except Exception as e:
            log.debug(f"JSON parse error: {e}")
            return None   # Explicitly None so callers can detect parse failure

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            self._json_response({
                "app":     "GModular",
                "version": GMODULAR_VERSION,
                "ready":   True,
                "port":    GMODULAR_CALLBACK_PORT,
            })
        elif self.path == "/api/module":
            # Return current module info if any
            state_info = self.server.get_module_info()
            self._json_response(state_info)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        data = self._read_json()
        if data is None:
            # Unparseable body — return 400 Bad Request
            self._json_response({"error": "Invalid JSON body"}, 400)
            return
        path = self.path

        if path == "/api/compile_result":
            self.server.push_event(("compile_result", data))
            self._json_response({"ok": True})

        elif path == "/api/model_ready":
            self.server.push_event(("model_ready", data))
            self._json_response({"ok": True})

        elif path == "/api/script_opened":
            self.server.push_event(("script_opened", data))
            self._json_response({"ok": True})

        elif path == "/api/git_updated":
            self.server.push_event(("git_updated", data))
            self._json_response({"ok": True})

        elif path == "/api/ping":
            self.server.push_event(("ping", data))
            self._json_response({"pong": True, "version": GMODULAR_VERSION})

        else:
            self._json_response({"error": "Unknown endpoint"}, 404)


class _GModularHTTPServer(HTTPServer):
    def __init__(self, event_queue: queue.Queue, *args, **kwargs):
        self._event_queue = event_queue
        self._module_info: Dict[str, Any] = {"module": None, "objects": 0}
        super().__init__(*args, **kwargs)

    def push_event(self, event):
        self._event_queue.put(event)

    def get_module_info(self) -> dict:
        return self._module_info

    def set_module_info(self, info: dict):
        self._module_info = info


# ─────────────────────────────────────────────────────────────────────────────
#  Server Thread
# ─────────────────────────────────────────────────────────────────────────────

class _ServerThread(threading.Thread):
    def __init__(self, server: _GModularHTTPServer):
        super().__init__(daemon=True, name="gmodular-ipc-server")
        self._server = server
        self._stop_event = threading.Event()

    def run(self):
        log.info(f"GModular IPC server started on port {GMODULAR_CALLBACK_PORT}")
        while not self._stop_event.is_set():
            try:
                self._server.handle_request()
            except Exception as e:
                if not self._stop_event.is_set():
                    log.debug(f"IPC server error: {e}")
                time.sleep(0.05)

    def stop(self):
        self._stop_event.set()


# ─────────────────────────────────────────────────────────────────────────────
#  GModular IPC Server  (Qt-aware)
# ─────────────────────────────────────────────────────────────────────────────

class GModularIPCServer(QObject):
    """
    Hosts the GModular callback HTTP server on port 7003.
    Delivers events to the main thread via Qt signals.

    Usage:
        server = GModularIPCServer(parent=main_window)
        server.compile_result.connect(on_compile)
        server.model_ready.connect(on_model)
        server.start()
        ...
        server.stop()
    """

    compile_result = Signal(bool, str, str)    # success, script, message
    model_ready    = Signal(str, str, str)      # model_name, mdl_path, mdx_path
    script_opened  = Signal(str)               # script name
    git_updated    = Signal(str, int)           # git path, object count
    ping_received  = Signal()
    error          = Signal(str)
    server_started = Signal(int)               # port
    server_stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q: queue.Queue = queue.Queue()
        self._server: Optional[_GModularHTTPServer] = None
        self._thread: Optional[_ServerThread] = None
        self._running = False

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(100)
        self._drain_timer.timeout.connect(self._drain)

    def start(self) -> bool:
        """Start the IPC server. Returns True if started successfully."""
        if self._running:
            return True
        try:
            self._server = _GModularHTTPServer(
                self._q,
                ("localhost", GMODULAR_CALLBACK_PORT),
                _GModularRequestHandler,
            )
            self._server.timeout = 0.1
            self._thread = _ServerThread(self._server)
            self._thread.start()
            self._drain_timer.start()
            self._running = True
            self.server_started.emit(GMODULAR_CALLBACK_PORT)
            log.info(f"GModular IPC server running on port {GMODULAR_CALLBACK_PORT}")
            return True
        except OSError as e:
            log.warning(f"Could not start IPC server on port {GMODULAR_CALLBACK_PORT}: {e}")
            self.error.emit(f"IPC server error: {e}")
            return False

    def stop(self):
        """Stop the IPC server."""
        self._drain_timer.stop()
        if self._thread:
            self._thread.stop()
            self._thread = None
        if self._server:
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self._running = False
        self.server_stopped.emit()
        log.info("GModular IPC server stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def port(self) -> int:
        return GMODULAR_CALLBACK_PORT

    def update_module_info(self, module_name: Optional[str], object_count: int):
        """Push current module info so tools can query it."""
        if self._server:
            self._server.set_module_info({
                "module": module_name,
                "objects": object_count,
                "port": GMODULAR_CALLBACK_PORT,
            })

    # ── Event Drain ───────────────────────────────────────────────────────────

    def _drain(self):
        try:
            while True:
                event = self._q.get_nowait()
                self._handle(event)
        except queue.Empty:
            pass

    def _handle(self, event):
        kind = event[0]
        data = event[1] if len(event) > 1 else {}

        if kind == "compile_result":
            success = bool(data.get("success", False))
            script  = str(data.get("script", ""))
            message = str(data.get("message", ""))
            self.compile_result.emit(success, script, message)
            log.info(f"IPC compile_result: {script} success={success}")

        elif kind == "model_ready":
            name     = str(data.get("model", ""))
            mdl_path = str(data.get("mdl_path", ""))
            mdx_path = str(data.get("mdx_path", ""))
            self.model_ready.emit(name, mdl_path, mdx_path)
            log.info(f"IPC model_ready: {name}")

        elif kind == "script_opened":
            name = str(data.get("script", ""))
            self.script_opened.emit(name)

        elif kind == "git_updated":
            path    = str(data.get("path", ""))
            objects = int(data.get("objects", 0))
            self.git_updated.emit(path, objects)

        elif kind == "ping":
            self.ping_received.emit()
