"""
GModular — PyInstaller runtime hook: pre-import all PyQt5 modules
=================================================================
This script runs INSIDE the frozen EXE immediately at startup, before
any application code runs.  It explicitly imports every PyQt5 module
that GModular uses, so they are resolved from the bundled .pyd files
rather than being lazily discovered at first use.

This guarantees that QGroupBox, QPushButton, QPolygon, QCursor, etc.
are all available when gmodular.gui.main_window imports them.

WHY THIS IS NEEDED:
    In a PyInstaller one-file EXE on Windows, the frozen .pyd files are
    extracted to a temp directory at runtime.  If a module is listed in
    hiddenimports but the corresponding .pyd was not collected (because
    the hook didn't run), the import silently fails or raises NameError.
    Pre-importing here forces Python to resolve all Qt bindings early,
    giving a clear ImportError at startup rather than a cryptic NameError
    deep inside the GUI code.
"""

import sys

# -- Force-load every PyQt5 binding module used by GModular ------------------
_PYQT5_MODULES = [
    "PyQt5",
    "PyQt5.sip",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt5.QtOpenGL",
    "PyQt5.QtPrintSupport",
]

for _mod in _PYQT5_MODULES:
    try:
        __import__(_mod)
    except ImportError as _e:
        # Print to stderr so it shows in the PyInstaller error dialog
        print(f"[GModular runtime hook] WARNING: could not import {_mod}: {_e}",
              file=sys.stderr)
