"""
GhostRigger — Main Entry Point
================================
Launches the GhostRigger main window and IPC server.

Usage (headless / CI):
    python main.py --headless        # IPC server only, no GUI
    python main.py                   # Full GUI + IPC server
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ghostrigger")


def _run_headless() -> int:
    """Start IPC server only (no Qt). For CI / testing."""
    from ghostrigger.ipc.server import start, stop, register
    from ghostrigger.core.blueprint_state import get_registry, Blueprint

    # Register IPC handlers
    registry = get_registry()

    def handle_open_utc(payload: dict) -> dict:
        resref = payload.get("resref", "new_creature")
        bp = Blueprint(resref=resref, blueprint_type="utc",
                       fields={"FirstName": "Unnamed", "Tag": resref.upper()})
        registry.add(bp)
        log.info("Opened UTC: %s", resref)
        return {"status": "ok", "resref": resref}

    def handle_open_utp(payload: dict) -> dict:
        resref = payload.get("resref", "new_placeable")
        bp = Blueprint(resref=resref, blueprint_type="utp",
                       fields={"Tag": resref.upper(), "TemplateResRef": resref})
        registry.add(bp)
        log.info("Opened UTP: %s", resref)
        return {"status": "ok", "resref": resref}

    def handle_open_utd(payload: dict) -> dict:
        resref = payload.get("resref", "new_door")
        bp = Blueprint(resref=resref, blueprint_type="utd",
                       fields={"Tag": resref.upper(), "Locked": 0})
        registry.add(bp)
        log.info("Opened UTD: %s", resref)
        return {"status": "ok", "resref": resref}

    def handle_get_blueprint(payload: dict) -> dict:
        resref = payload.get("resref", "")
        bp = registry.get(resref)
        if bp is None:
            return {"error": f"blueprint not found: {resref}"}
        return {"status": "ok", "blueprint": bp.to_dict()}

    def handle_list_blueprints(payload: dict) -> dict:
        bps = registry.list_all()
        return {"status": "ok", "blueprints": [b.to_dict() for b in bps]}

    def handle_save_blueprint(payload: dict) -> dict:
        data = payload.get("blueprint", {})
        bp = Blueprint.from_dict(data)
        bp.dirty = False
        registry.add(bp)
        log.info("Saved blueprint: %s", bp.resref)
        return {"status": "ok", "resref": bp.resref}

    def handle_set_field(payload: dict) -> dict:
        resref = payload.get("resref", "")
        field_name = payload.get("field", "")
        value = payload.get("value")
        if not resref or not field_name:
            return {"error": "resref and field are required"}
        bp = registry.get(resref)
        if bp is None:
            return {"error": f"blueprint not found: {resref}"}
        bp.set(field_name, value)
        log.info("set_field %s.%s = %r", resref, field_name, value)
        return {"status": "ok", "resref": resref, "field": field_name, "value": value}

    def handle_get_field(payload: dict) -> dict:
        resref = payload.get("resref", "")
        field_name = payload.get("field", "")
        if not resref or not field_name:
            return {"error": "resref and field are required"}
        bp = registry.get(resref)
        if bp is None:
            return {"error": f"blueprint not found: {resref}"}
        value = bp.get(field_name)
        return {"status": "ok", "resref": resref, "field": field_name, "value": value}

    def handle_set_fields_bulk(payload: dict) -> dict:
        """Set multiple fields at once: payload = {resref, fields: {k: v, …}}"""
        resref = payload.get("resref", "")
        updates = payload.get("fields", {})
        if not resref:
            return {"error": "resref is required"}
        bp = registry.get(resref)
        if bp is None:
            return {"error": f"blueprint not found: {resref}"}
        for k, v in updates.items():
            bp.set(k, v)
        log.info("set_fields_bulk %s: %d fields updated", resref, len(updates))
        return {"status": "ok", "resref": resref, "updated": len(updates)}

    register("open_utc",          handle_open_utc)
    register("open_utp",          handle_open_utp)
    register("open_utd",          handle_open_utd)
    register("get_blueprint",     handle_get_blueprint)
    register("list_blueprints",   handle_list_blueprints)
    register("save_blueprint",    handle_save_blueprint)
    register("set_field",         handle_set_field)
    register("get_field",         handle_get_field)
    register("set_fields_bulk",   handle_set_fields_bulk)

    srv = start()
    log.info("GhostRigger running headless on port 7001 — Ctrl+C to stop")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        stop()
    return 0


def _run_gui() -> int:
    """Start full Qt GUI + IPC server."""
    try:
        from qtpy.QtWidgets import QApplication
        from ghostrigger.gui.main_window import MainWindow
    except ImportError as exc:
        log.warning("Qt not available (%s) — falling back to headless mode", exc)
        return _run_headless()

    app = QApplication(sys.argv)
    app.setApplicationName("GhostRigger")
    app.setApplicationVersion("1.0.0")
    win = MainWindow()
    win.show()

    # Start IPC alongside GUI
    from ghostrigger.ipc.server import start as start_ipc
    start_ipc(daemon=True)

    return app.exec_() if hasattr(app, 'exec_') else app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description="GhostRigger — KotOR Asset Editor")
    parser.add_argument("--headless", action="store_true",
                        help="Start IPC server only (no GUI)")
    args = parser.parse_args()
    if args.headless:
        return _run_headless()
    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
