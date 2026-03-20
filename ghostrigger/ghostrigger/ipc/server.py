"""
GhostRigger — IPC Server
========================
Lightweight JSON-over-HTTP IPC server on port 7001.
Matches the Ghostworks Pipeline IPC contract (PIPELINE_SPEC.md §3).

Endpoints:
  POST /ping                — health check
  POST /open_utc            — open a UTC blueprint editor
  POST /open_utp            — open a UTP (placeable) blueprint editor
  POST /open_utd            — open a UTD (door) blueprint editor
  POST /get_blueprint       — return serialised blueprint JSON
  POST /save_blueprint      — save blueprint and notify GModular
  POST /list_blueprints     — list all loaded blueprints
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict, Any, Optional

log = logging.getLogger(__name__)

PORT = 7001
_server_instance: Optional[HTTPServer] = None
_running = False

# Registered action handlers: action_name → callable(payload) → dict
_handlers: Dict[str, Callable[[dict], dict]] = {}


def register(action: str, fn: Callable[[dict], dict]) -> None:
    """Register a handler for an IPC action."""
    _handlers[action] = fn


def _default_ping(payload: dict) -> dict:
    return {"status": "ok", "program": "GhostRigger", "version": "1.0.0", "port": PORT}


register("ping", _default_ping)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug("IPC %s", fmt % args)

    def do_POST(self):
        action = self.path.lstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        handler = _handlers.get(action)
        if handler is None:
            self._respond(404, {"error": f"unknown action: {action}"})
            return

        try:
            result = handler(payload)
            self._respond(200, result)
        except Exception as exc:
            log.exception("IPC handler %s raised", action)
            self._respond(500, {"error": str(exc)})

    def _respond(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(host: str = "127.0.0.1", port: int = PORT,
          daemon: bool = True) -> HTTPServer:
    """Start the IPC server in a background daemon thread."""
    global _server_instance, _running
    srv = HTTPServer((host, port), _Handler)
    _server_instance = srv
    _running = True
    t = threading.Thread(target=srv.serve_forever, daemon=daemon)
    t.start()
    log.info("GhostRigger IPC server listening on %s:%d", host, port)
    return srv


def stop() -> None:
    """Stop the IPC server."""
    global _server_instance, _running
    if _server_instance:
        _server_instance.shutdown()
        _server_instance = None
    _running = False
    log.info("GhostRigger IPC server stopped")


def is_running() -> bool:
    return _running
