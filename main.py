#!/usr/bin/env python3
"""
GModular — KotOR Module Editor
Entry point.

Uses the qtpy compatibility layer; works with PyQt5, PyQt6, PySide2, or PySide6.
Set QT_API=pyqt6 (or pyside6) before launching to use a different Qt backend.

Usage:
    python main.py
    python main.py --open path/to/module.git
    QT_API=pyqt6 python main.py          # force Qt 6
"""
from __future__ import annotations
import sys
import os
import logging
import argparse
from pathlib import Path

# Ensure GModular package is importable
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))


def _configure_logging():
    """Set up console + file logging."""
    log_dir = Path.home() / ".gmodular"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "gmodular.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler (INFO+)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler (DEBUG+)
    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass


def main():
    _configure_logging()
    log = logging.getLogger("gmodular.main")

    parser = argparse.ArgumentParser(
        description="GModular — KotOR Module Editor"
    )
    parser.add_argument(
        "--open", metavar="FILE",
        help="Open a .git file on startup"
    )
    parser.add_argument(
        "--project", metavar="DIR",
        help="Open a GModular project directory on startup"
    )
    parser.add_argument(
        "--game-dir", metavar="DIR",
        help="Override KotOR game directory"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable verbose debug logging to console"
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().handlers[0].setLevel(logging.DEBUG)

    log.info("=" * 60)
    log.info("GModular — KotOR Module Editor  v2.0.10")
    log.info("=" * 60)

    # Import Qt via qtpy compatibility layer.
    # qtpy transparently wraps PyQt5, PyQt6, PySide2, or PySide6.
    # Select backend via QT_API environment variable (default: pyqt5).
    try:
        from qtpy.QtWidgets import QApplication, QMessageBox
        from qtpy.QtCore import Qt, QCoreApplication, QTimer
        from qtpy.QtGui import QFont
    except ImportError as e:
        print(f"FATAL: Qt bindings not available: {e}")
        print("Install a Qt backend and qtpy:")
        print("  pip install qtpy PyQt5        # Qt 5 (recommended)")
        print("  pip install qtpy PyQt6        # Qt 6")
        print("  pip install qtpy PySide6      # Qt 6 / PySide")
        sys.exit(1)

    # High-DPI support (Qt5 only — Qt6 enables this automatically)
    try:
        QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass  # Qt6: these attributes no longer exist

    app = QApplication(sys.argv)
    app.setApplicationName("GModular")
    app.setOrganizationName("GModular")
    app.setOrganizationDomain("github.com/CrispyW0nton")

    # Default font — falls back gracefully on non-Windows systems
    font = QFont("Segoe UI", 9)
    try:
        font.setHintingPreference(QFont.PreferFullHinting)
    except AttributeError:
        pass  # Qt6 renamed some enum members
    app.setFont(font)

    # Import and show main window
    try:
        from gmodular.gui.main_window import MainWindow
    except Exception as e:
        log.exception(f"Failed to import MainWindow: {e}")
        QMessageBox.critical(
            None, "Import Error",
            f"Failed to load GModular GUI:\n\n{e}\n\n"
            "Check that all dependencies are installed:\n"
            "  pip install qtpy PyQt5 moderngl numpy"
        )
        sys.exit(1)

    win = MainWindow()

    # Apply startup args
    if args.game_dir:
        gd = Path(args.game_dir)
        if gd.exists() and (gd / "chitin.key").exists():
            win._game_dir = gd
            win._save_settings()
            win.log(f"Game directory (CLI): {gd}")

    win.show()

    # Deferred open (after event loop starts)
    if args.open:
        def _open():
            p = Path(args.open)
            if p.exists():
                win._state.load_from_files(str(p))
                win._update_title()
                win._update_object_count()
                win.log(f"Opened: {p}")
        QTimer.singleShot(300, _open)

    if args.project:
        def _open_proj():
            from gmodular.core.module_state import ModuleProject
            proj = ModuleProject.load_meta(args.project)
            if proj.module_resref:
                win._state.load_from_project(proj)
                win._update_title()
                win._update_object_count()
                win.log(f"Opened project: {proj.name}")
        QTimer.singleShot(300, _open_proj)

    log.info("Starting Qt event loop…")
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
