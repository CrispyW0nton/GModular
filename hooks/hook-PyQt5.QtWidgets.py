"""
GModular — PyInstaller hook for PyQt5.QtWidgets
================================================
Forces PyInstaller to collect ALL PyQt5 binaries, data files (Qt plugins),
and hidden imports using the official PyInstaller hook system.

This file lives in the project's local `hooks/` directory and is picked up
by Analysis(hookspath=['hooks']) in GModular.spec.

WHY THIS IS NEEDED:
    PyInstaller's spec-level hiddenimports=['PyQt5.QtWidgets'] alone does NOT
    copy Qt DLLs or Qt plugin directories (platforms/, styles/, imageformats/).
    Without platforms/qwindows.dll the app silently fails on Windows.
    Without the full .pyd collection, classes like QGroupBox raise NameError.

    The correct approach is to let PyInstaller run its own PyQt5 hook, which
    this file triggers by importing from the standard hook infrastructure.
"""

from PyInstaller.utils.hooks import collect_all

# collect_all('PyQt5') returns (datas, binaries, hiddenimports)
# These are merged by PyInstaller into the final EXE automatically.
datas, binaries, hiddenimports = collect_all('PyQt5')
