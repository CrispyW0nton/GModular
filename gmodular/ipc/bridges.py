"""
GModular — IPC Bridges
HTTP REST communication with GhostScripter (port 7002) and GhostRigger (port 7001).
Architecture mirrors GhostScripter's own GhostRiggerBridge:
  - Background daemon thread handles all HTTP I/O
  - Qt signals deliver results back to main thread
  - Drain timer (50ms) empties result queue — never blocks UI

Port contract (PIPELINE_SPEC v1.0):
  GhostRigger    port 7001 — receives asset-edit requests
  GhostScripter  port 7002 — receives script/dlg requests
  GModular       port 7003 — receives refresh/update calls
"""
from __future__ import annotations
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

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

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# IPC configuration — PIPELINE_SPEC fixed ports (do not change)
GHOSTSCRIPTER_PORT = 7002
GHOSTRIGGER_PORT   = 7001
GMODULAR_PORT      = 7003   # GModular's own callback server

POLL_INTERVAL_S    = 8.0
CONNECT_TIMEOUT    = 0.5
REQUEST_TIMEOUT    = 3.0


# ─────────────────────────────────────────────────────────────────────────────
#  GhostScripter Bridge  (port 7002)
# ─────────────────────────────────────────────────────────────────────────────

class _GhostScripterPollWorker(threading.Thread):
    def __init__(self, result_queue: queue.Queue):
        super().__init__(daemon=True, name="gs-poll")
        self._q   = result_queue
        self._stop = threading.Event()
        self._base = f"http://localhost:{GHOSTSCRIPTER_PORT}/api"

    def stop(self):
        self._stop.set()

    def run(self):
        if not _HAS_REQUESTS:
            return
        session = _requests.Session()
        while not self._stop.is_set():
            try:
                r = session.get(f"{self._base}/status", timeout=CONNECT_TIMEOUT)
                ok = r.status_code == 200
                version = r.json().get("version", "?") if ok else ""
                self._q.put(("status", ok, version))

                if ok:
                    # Fetch script list while connected
                    try:
                        r2 = session.get(f"{self._base}/scripts", timeout=CONNECT_TIMEOUT)
                        if r2.status_code == 200:
                            scripts = r2.json()
                            if isinstance(scripts, list):
                                self._q.put(("scripts", scripts))
                    except Exception:
                        pass
            except Exception:
                self._q.put(("status", False, ""))

            for _ in range(int(POLL_INTERVAL_S / 0.2)):
                if self._stop.is_set():
                    break
                time.sleep(0.2)
        session.close()


class GhostScripterBridge(QObject):
    """
    IPC bridge to GhostScripter-K1-K2.
    Fetches script list, triggers compiles, and watches for .ncs changes.
    """

    connected     = Signal(str)           # version
    disconnected  = Signal()
    scripts_updated = Signal(list)        # List[str] of script names
    compile_done  = Signal(bool, str)     # success, message
    status_update = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_connected = False
        self._scripts: List[str] = []
        self._q: queue.Queue = queue.Queue()
        self._worker: Optional[_GhostScripterPollWorker] = None
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(100)
        self._drain_timer.timeout.connect(self._drain)
        self._fg: Optional["_requests.Session"] = None

    def start(self):
        if not _HAS_REQUESTS:
            self.status_update.emit("⚠ requests not installed — IPC disabled")
            return
        self._fg = _requests.Session()
        self._worker = _GhostScripterPollWorker(self._q)
        self._worker.start()
        self._drain_timer.start()
        log.info("GhostScripter IPC bridge started")

    def stop(self):
        self._drain_timer.stop()
        if self._worker:
            self._worker.stop()
            self._worker.join(timeout=1.0)
            self._worker = None
        if self._fg:
            self._fg.close()
            self._fg = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def scripts(self) -> List[str]:
        return self._scripts

    def _drain(self):
        try:
            while True:
                event = self._q.get_nowait()
                self._handle(event)
        except queue.Empty:
            pass

    def _handle(self, event):
        kind = event[0]
        if kind == "status":
            _, ok, version = event
            was = self._is_connected
            self._is_connected = ok
            if ok and not was:
                self.connected.emit(version)
                self.status_update.emit(f"GhostScripter connected (v{version})")
            elif not ok and was:
                self.disconnected.emit()
                self.status_update.emit("GhostScripter disconnected")
        elif kind == "scripts":
            self._scripts = event[1]
            self.scripts_updated.emit(self._scripts)

    # ── Foreground API ────────────────────────────────────────────────────────

    def open_script(self, script_resref: str) -> bool:
        """Ask GhostScripter to open a script in its editor."""
        if not self._is_connected or not self._fg:
            return False
        try:
            r = self._fg.post(
                f"http://localhost:{GHOSTSCRIPTER_PORT}/api/open_script",
                json={"script": script_resref + ".nss"},
                timeout=REQUEST_TIMEOUT,
            )
            return r.status_code == 200
        except Exception:
            return False

    def compile_script(self, script_resref: str, game: str = "K1") -> bool:
        """Trigger a compile in GhostScripter."""
        if not self._is_connected or not self._fg:
            self.compile_done.emit(False, "GhostScripter not connected")
            return False
        try:
            r = self._fg.post(
                f"http://localhost:{GHOSTSCRIPTER_PORT}/api/compile",
                json={"script": script_resref + ".nss", "game": game},
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                success = data.get("success", False)
                self.compile_done.emit(success, data.get("message", ""))
                return success
            self.compile_done.emit(False, f"HTTP {r.status_code}")
            return False
        except Exception as e:
            self.compile_done.emit(False, str(e))
            return False

    def get_scripts(self) -> List[str]:
        """Synchronous script list fetch."""
        if not self._is_connected or not self._fg:
            return []
        try:
            r = self._fg.get(
                f"http://localhost:{GHOSTSCRIPTER_PORT}/api/scripts",
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception:
            pass
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  GhostRigger Bridge  (port 7001)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelPayload:
    model_name:       str
    mdl_path:         str = ""
    mdx_path:         str = ""
    texture_paths:    List[str] = field(default_factory=list)
    appearance_row:   int = -1
    appearance_label: str = ""


class _GhostRiggerPollWorker(threading.Thread):
    def __init__(self, result_queue: queue.Queue):
        super().__init__(daemon=True, name="gr-poll")
        self._q   = result_queue
        self._stop = threading.Event()
        self._base = f"http://localhost:{GHOSTRIGGER_PORT}/api"

    def stop(self):
        self._stop.set()

    def run(self):
        if not _HAS_REQUESTS:
            return
        session = _requests.Session()
        while not self._stop.is_set():
            try:
                r = session.get(f"{self._base}/status", timeout=CONNECT_TIMEOUT)
                ok = r.status_code == 200
                version = r.json().get("version", "?") if ok else ""
                self._q.put(("status", ok, version))

                if ok:
                    try:
                        r2 = session.get(f"{self._base}/completed", timeout=CONNECT_TIMEOUT)
                        if r2.status_code == 200:
                            for item in r2.json().get("models", []):
                                self._q.put(("model", item))
                    except Exception:
                        pass
            except Exception:
                self._q.put(("status", False, ""))

            for _ in range(int(POLL_INTERVAL_S / 0.2)):
                if self._stop.is_set():
                    break
                time.sleep(0.2)
        session.close()


class GhostRiggerBridge(QObject):
    """
    IPC bridge to GhostRigger-K1-K2.
    Fetches model list, pushes rig requests, receives completed models.
    """

    connected     = Signal(str)
    disconnected  = Signal()
    model_ready   = Signal(object)   # ModelPayload
    rig_error     = Signal(str)
    status_update = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_connected = False
        self._q: queue.Queue = queue.Queue()
        self._worker: Optional[_GhostRiggerPollWorker] = None
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(100)
        self._drain_timer.timeout.connect(self._drain)
        self._fg: Optional["_requests.Session"] = None

    def start(self):
        if not _HAS_REQUESTS:
            return
        self._fg = _requests.Session()
        self._worker = _GhostRiggerPollWorker(self._q)
        self._worker.start()
        self._drain_timer.start()
        log.info("GhostRigger IPC bridge started")

    def stop(self):
        self._drain_timer.stop()
        if self._worker:
            self._worker.stop()
            self._worker.join(timeout=1.0)
            self._worker = None
        if self._fg:
            self._fg.close()
            self._fg = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def _drain(self):
        try:
            while True:
                self._handle(self._q.get_nowait())
        except queue.Empty:
            pass

    def _handle(self, event):
        kind = event[0]
        if kind == "status":
            _, ok, version = event
            was = self._is_connected
            self._is_connected = ok
            if ok and not was:
                self.connected.emit(version)
            elif not ok and was:
                self.disconnected.emit()
        elif kind == "model":
            item = event[1]
            payload = ModelPayload(
                model_name=item.get("model_name", ""),
                mdl_path=item.get("mdl_path", ""),
                mdx_path=item.get("mdx_path", ""),
                texture_paths=item.get("texture_paths", []),
                appearance_row=item.get("appearance_row", -1),
                appearance_label=item.get("appearance_label", ""),
            )
            self.model_ready.emit(payload)
            self.status_update.emit(f"Model ready: {payload.model_name}")

    def request_rig(self, model_name: str, mdl_path: str = "") -> bool:
        if not self._is_connected or not self._fg:
            self.rig_error.emit("GhostRigger not connected")
            return False
        try:
            r = self._fg.post(
                f"http://localhost:{GHOSTRIGGER_PORT}/api/rig",
                json={"model": model_name, "path": mdl_path},
                timeout=5,
            )
            return r.status_code == 200
        except Exception as e:
            self.rig_error.emit(str(e))
            return False

    # ── P9: Blueprint IPC (open_utc / open_utp / open_utd) ──────────────────

    def open_blueprint(self, resref: str, blueprint_type: str,
                       module_dir: str = "") -> bool:
        """
        P9 — Ask GhostRigger to open a blueprint for editing.

        blueprint_type: "utc", "utp", or "utd"
        Payload matches PIPELINE_SPEC IPC contract:
          POST /api/open_utc  {"resref": "...", "module_dir": "..."}
          POST /api/open_utp  {...}
          POST /api/open_utd  {...}
        """
        if not self._fg:
            log.debug("GhostRigger not connected — blueprint open skipped")
            return False
        ext = blueprint_type.lower().strip(".")
        endpoint = f"http://localhost:{GHOSTRIGGER_PORT}/api/open_{ext}"
        try:
            r = self._fg.post(
                endpoint,
                json={"resref": resref, "module_dir": module_dir},
                timeout=REQUEST_TIMEOUT,
            )
            ok = r.status_code == 200
            if ok:
                log.info(f"GhostRigger: opened {resref}.{ext}")
            else:
                log.debug(f"GhostRigger: {endpoint} returned {r.status_code}")
            return ok
        except Exception as e:
            log.debug(f"GhostRigger open_blueprint error: {e}")
            return False

    def open_utc(self, resref: str, module_dir: str = "") -> bool:
        """Open a creature blueprint (.utc) in GhostRigger."""
        return self.open_blueprint(resref, "utc", module_dir)

    def open_utp(self, resref: str, module_dir: str = "") -> bool:
        """Open a placeable blueprint (.utp) in GhostRigger."""
        return self.open_blueprint(resref, "utp", module_dir)

    def open_utd(self, resref: str, module_dir: str = "") -> bool:
        """Open a door blueprint (.utd) in GhostRigger."""
        return self.open_blueprint(resref, "utd", module_dir)

    def get_models(self) -> List[Dict]:
        if not self._is_connected or not self._fg:
            return []
        try:
            r = self._fg.get(
                f"http://localhost:{GHOSTRIGGER_PORT}/api/models",
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception:
            pass
        return []

    # ── Animation control ────────────────────────────────────────────────────

    def play_animation(self, model_name: str, anim_name: str,
                       loop: bool = True, speed: float = 1.0) -> bool:
        """
        Ask GhostRigger to play an animation on a specific model.

        PIPELINE_SPEC endpoint:
          POST /api/animation/play
          Body: {"model": str, "anim": str, "loop": bool, "speed": float}

        Returns True if GhostRigger acknowledged the request.
        Falls back gracefully (returns False) when GhostRigger is offline.
        """
        if not self._fg:
            log.debug("GhostRigger not connected — play_animation skipped")
            return False
        try:
            r = self._fg.post(
                f"http://localhost:{GHOSTRIGGER_PORT}/api/animation/play",
                json={
                    "model": model_name,
                    "anim":  anim_name,
                    "loop":  loop,
                    "speed": speed,
                },
                timeout=REQUEST_TIMEOUT,
            )
            ok = r.status_code == 200
            if ok:
                log.info(f"GhostRigger: play '{anim_name}' on '{model_name}'")
            else:
                log.debug(f"GhostRigger: play_animation returned {r.status_code}")
            return ok
        except Exception as e:
            log.debug(f"GhostRigger play_animation error: {e}")
            return False

    def stop_animation(self, model_name: str) -> bool:
        """
        Ask GhostRigger to stop all animations on a specific model.

        PIPELINE_SPEC endpoint:
          POST /api/animation/stop
          Body: {"model": str}
        """
        if not self._fg:
            return False
        try:
            r = self._fg.post(
                f"http://localhost:{GHOSTRIGGER_PORT}/api/animation/stop",
                json={"model": model_name},
                timeout=REQUEST_TIMEOUT,
            )
            ok = r.status_code == 200
            if ok:
                log.info(f"GhostRigger: stop animation on '{model_name}'")
            return ok
        except Exception as e:
            log.debug(f"GhostRigger stop_animation error: {e}")
            return False

    def set_animation_speed(self, model_name: str, speed: float) -> bool:
        """
        Adjust the playback speed multiplier for a model's current animation.

        PIPELINE_SPEC endpoint:
          POST /api/animation/speed
          Body: {"model": str, "speed": float}
        """
        if not self._fg:
            return False
        try:
            r = self._fg.post(
                f"http://localhost:{GHOSTRIGGER_PORT}/api/animation/speed",
                json={"model": model_name, "speed": max(0.0, speed)},
                timeout=REQUEST_TIMEOUT,
            )
            return r.status_code == 200
        except Exception as e:
            log.debug(f"GhostRigger set_animation_speed error: {e}")
            return False

    def list_animations(self, model_name: str) -> List[str]:
        """
        Query GhostRigger for the list of animation names available on a model.

        PIPELINE_SPEC endpoint:
          GET /api/animation/list?model=<name>

        Returns list of animation name strings (empty on failure / offline).
        """
        if not self._fg:
            return []
        try:
            r = self._fg.get(
                f"http://localhost:{GHOSTRIGGER_PORT}/api/animation/list",
                params={"model": model_name},
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            log.debug(f"GhostRigger list_animations error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  File Watcher (auto-reload scripts/models on external change)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False


class ProjectFileWatcher(QObject):
    """
    Watches the project folder for external file changes.
    Emits signals when scripts or models are modified.
    """

    script_changed = Signal(str)   # path to .ncs
    model_changed  = Signal(str)   # path to .mdl

    def __init__(self, parent=None):
        super().__init__(parent)
        self._observer: Optional["Observer"] = None
        self._watch_path: Optional[str] = None

    def watch(self, project_dir: str):
        self.stop()
        self._watch_path = project_dir
        if not _HAS_WATCHDOG:
            log.warning("watchdog not installed — file watcher disabled")
            return

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            bridge = self

            class _Handler(FileSystemEventHandler):
                def on_modified(self, event):
                    if event.is_directory:
                        return
                    path = event.src_path
                    if path.endswith(".ncs"):
                        bridge.script_changed.emit(path)
                    elif path.endswith((".mdl", ".mdx")):
                        bridge.model_changed.emit(path)

            self._observer = Observer()
            self._observer.schedule(_Handler(), path=project_dir, recursive=True)
            self._observer.start()
            log.info(f"File watcher started on: {project_dir}")
        except Exception as e:
            log.warning(f"File watcher error: {e}")

    def stop(self):
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                pass
            self._observer = None
