"""
GhostScripter — Main Entry Point
==================================
Usage:
    python main.py --headless       # IPC server only
    python main.py                  # Full GUI + IPC server
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
log = logging.getLogger("ghostscripter")


def _run_headless() -> int:
    from ghostscripter.ipc.server import start, stop, register
    from ghostscripter.core.script_state import get_registry, Script, NWScriptCompiler

    registry = get_registry()
    compiler = NWScriptCompiler()

    def handle_open_script(payload: dict) -> dict:
        resref = payload.get("resref", "new_script")
        source = payload.get("source", "void main() {\n    // TODO\n}\n")
        s = Script(resref=resref, source=source)
        registry.add(s)
        return {"status": "ok", "resref": resref}

    def handle_get_script(payload: dict) -> dict:
        resref = payload.get("resref", "")
        s = registry.get(resref)
        if s is None:
            return {"error": f"script not found: {resref}"}
        return {"status": "ok", "script": s.to_dict()}

    def handle_compile(payload: dict) -> dict:
        resref = payload.get("resref", "")
        s = registry.get(resref)
        if s is None:
            source = payload.get("source", "")
            if not source:
                return {"error": "resref not found and no source provided"}
        else:
            source = s.source
        result = compiler.compile(source, resref)
        if s and result["success"]:
            s.compiled = result["ncs"]
            s.diagnostics = result["warnings"]
        return {"status": "ok", **result}

    def handle_decompile(payload: dict) -> dict:
        # Stub — real decompiler wired in Phase 2
        return {"status": "ok", "source": "// Decompilation not yet implemented\nvoid main() {}\n"}

    def handle_set_resref(payload: dict) -> dict:
        # Called by GModular to request a script resref be filled in
        resref = payload.get("resref", "")
        return {"status": "ok", "resref": resref, "action": "set_resref"}

    def handle_list_scripts(payload: dict) -> dict:
        scripts = registry.list_all()
        return {"status": "ok", "scripts": [s.to_dict() for s in scripts]}

    register("open_script",   handle_open_script)
    register("get_script",    handle_get_script)
    register("compile",       handle_compile)
    register("decompile",     handle_decompile)
    register("set_resref",    handle_set_resref)
    register("list_scripts",  handle_list_scripts)

    srv = start()
    log.info("GhostScripter running headless on port 7002 — Ctrl+C to stop")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        stop()
    return 0


def _run_gui() -> int:
    try:
        from qtpy.QtWidgets import QApplication
        from ghostscripter.gui.main_window import MainWindow
    except ImportError as exc:
        log.warning("Qt not available (%s) — falling back to headless", exc)
        return _run_headless()

    app = QApplication(sys.argv)
    app.setApplicationName("GhostScripter")
    app.setApplicationVersion("1.0.0")
    win = MainWindow()
    win.show()

    from ghostscripter.ipc.server import start as start_ipc
    start_ipc(daemon=True)

    return app.exec_() if hasattr(app, 'exec_') else app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description="GhostScripter — KotOR Script + Logic IDE")
    parser.add_argument("--headless", action="store_true",
                        help="Start IPC server only (no GUI)")
    args = parser.parse_args()
    if args.headless:
        return _run_headless()
    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
