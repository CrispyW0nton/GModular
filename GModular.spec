# -*- mode: python ; coding: utf-8 -*-
"""
GModular — KotOR Module Editor
PyInstaller spec file  (Windows + Linux)

Produces: dist/GModular.exe  (Windows)  or  dist/GModular  (Linux)
Size: ~60-100 MB (single-file, no console window)

Usage:
    python -m PyInstaller GModular.spec --clean --noconfirm

NOTE on moderngl:
    moderngl requires Microsoft Visual C++ Build Tools to compile from source.
    build.bat installs it via --only-binary so no compiler is needed.
    If moderngl is absent the viewport falls back to PyOpenGL (pure Python).

NOTE on Python version:
    PyQt5 wheels exist for Python 3.8 – 3.12 only.
    Do NOT use Python 3.13 or 3.14.  Use Python 3.12.

KEY FIX (v2.1):
    Use collect_all('PyQt5') instead of bare hiddenimports for PyQt5.
    On Windows the frozen EXE needs the full PyQt5 hook to copy Qt DLLs,
    Qt plugins (platforms/, styles/, imageformats/), and the sip bindings.
    A bare hiddenimport of "PyQt5.QtWidgets" does NOT trigger those hooks,
    which causes NameError for QGroupBox and friends at startup.
"""

import sys
import importlib.util
from pathlib import Path

# PyInstaller built-ins (collect_all, collect_submodules, collect_data_files)
from PyInstaller.utils.hooks import collect_all, collect_submodules   # noqa: F821

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE = Path(SPECPATH)   # noqa: F821  (PyInstaller built-in)

# ── Detect available optional backends ───────────────────────────────────────
_has_moderngl = importlib.util.find_spec("moderngl") is not None
_has_pyopengl = importlib.util.find_spec("OpenGL")   is not None
_has_flask     = importlib.util.find_spec("flask")    is not None
_has_watchdog  = importlib.util.find_spec("watchdog") is not None

print(f"[spec] moderngl  : {'YES' if _has_moderngl else 'NO (will use PyOpenGL fallback)'}")
print(f"[spec] PyOpenGL  : {'YES' if _has_pyopengl else 'NO'}")
print(f"[spec] flask     : {'YES' if _has_flask    else 'NO (optional — skipped)'}")
print(f"[spec] watchdog  : {'YES' if _has_watchdog else 'NO (optional — skipped)'}")

# ── Collect PyQt5 via hooks (THE critical fix) ────────────────────────────────
# collect_all() runs PyInstaller's bundled hook for PyQt5, which:
#   1. Copies all Qt DLLs (Qt5Core.dll, Qt5Gui.dll, Qt5Widgets.dll, ...)
#   2. Copies Qt plugins (platforms/qwindows.dll, styles/, imageformats/)
#   3. Copies all PyQt5 .pyd/.so bindings (QtWidgets, QtCore, QtGui, sip, ...)
# Without this, QGroupBox / any widget class raises NameError in the frozen EXE.
_pyqt5_datas, _pyqt5_binaries, _pyqt5_hiddenimports = collect_all("PyQt5")

# ── Data files bundled into the EXE ─────────────────────────────────────────
datas = list(_pyqt5_datas)   # start with PyQt5 data (Qt plugins, translations)
if (HERE / "assets").exists():
    datas.append((str(HERE / "assets"), "assets"))
if (HERE / "resources").exists():
    datas.append((str(HERE / "resources"), "resources"))

# ── Hidden imports ────────────────────────────────────────────────────────────
# NOTE: The regex in tests/test_module_state.py::TestSpecHiddenImports
# scans this literal list for required entries — keep all GModular imports here.
hidden_imports = [
    # ── GModular package ──────────────────────────────────────────────────
    "gmodular",
    "gmodular.core",
    "gmodular.core.module_state",
    "gmodular.formats",
    "gmodular.formats.gff_types",
    "gmodular.formats.gff_reader",
    "gmodular.formats.gff_writer",
    "gmodular.formats.archives",
    "gmodular.formats.mdl_parser",
    "gmodular.engine",
    "gmodular.engine.player_controller",
    "gmodular.engine.npc_instance",
    "gmodular.gui",
    "gmodular.gui.main_window",
    "gmodular.gui.viewport",
    "gmodular.gui.inspector",
    "gmodular.gui.asset_palette",
    "gmodular.gui.scene_outline",
    "gmodular.gui.walkmesh_editor",
    "gmodular.gui.script_library",
    "gmodular.gui.mod_packager_dialog",
    "gmodular.gui.patrol_editor",
    "gmodular.gui.room_assembly",
    "gmodular.formats.mod_packager",
    "gmodular.formats.twoda_loader",
    "gmodular.ipc",
    "gmodular.ipc.bridges",
    "gmodular.ipc.callback_server",
    "gmodular.utils",
    "gmodular.utils.resource_manager",
    # ── PyQt5 explicit (belt-and-suspenders on top of collect_all) ────────
    "PyQt5",
    "PyQt5.QtWidgets",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtOpenGL",
    "PyQt5.QtPrintSupport",
    "PyQt5.sip",
    # ── numpy ────────────────────────────────────────────────────────────
    "numpy",
    "numpy.core",
    "numpy.core._multiarray_umath",
    # ── requests (IPC) ───────────────────────────────────────────────────
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
]
# Merge in the full PyQt5 hook hidden imports (collected above via collect_all)
hidden_imports += [h for h in _pyqt5_hiddenimports if h not in hidden_imports]

# Optional: moderngl (preferred GL backend)
if _has_moderngl:
    hidden_imports.append("moderngl")

# Optional: PyOpenGL (pure-Python fallback)
if _has_pyopengl:
    hidden_imports += [
        "OpenGL",
        "OpenGL.GL",
        "OpenGL.GLU",
        "OpenGL.arrays",
        "OpenGL.arrays.numpymodule",
        "OpenGL.platform",
        "OpenGL.platform.win32",
    ]

# Optional: watchdog (file watcher)
if _has_watchdog:
    hidden_imports += [
        "watchdog",
        "watchdog.observers",
        "watchdog.events",
    ]

# Optional: flask / werkzeug (soft IPC dependency)
if _has_flask:
    hidden_imports += ["flask", "werkzeug", "jinja2"]

# ── Excludes (keep EXE small) ─────────────────────────────────────────────────
excludes = [
    "tkinter", "_tkinter",
    "matplotlib", "scipy", "pandas",
    "notebook", "IPython",
    "pytest", "_pytest",
    "PyQt5.QtWebEngine", "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngineCore",
    "PyQt5.QtMultimedia", "PyQt5.QtSql",
    "PyQt5.QtBluetooth", "PyQt5.QtNfc",
    "PyQt5.QtLocation", "PyQt5.QtPositioning", "PyQt5.QtSensors",
    "PyQt5.QtXml", "PyQt5.QtXmlPatterns",
    "PyQt5.QtHelp", "PyQt5.QtDesigner",
    "unittest", "doctest", "pdb",
    "email", "html", "http.server", "xmlrpc",
    # NOTE: multiprocessing intentionally NOT excluded — PyInstaller Windows
    #       bootloader requires it for the freeze_support() call on Windows.
    "sip",   # bare 'sip' is obsolete; PyQt5.sip is used instead (suppresses warning)
]

if not _has_moderngl:
    excludes.append("moderngl")
if not _has_pyopengl:
    excludes += ["OpenGL", "OpenGL.GL"]
if not _has_flask:
    excludes += ["flask", "werkzeug", "jinja2"]
if not _has_watchdog:
    excludes += ["watchdog"]

# ── Icon path ─────────────────────────────────────────────────────────────────
_icon = str(HERE / "assets" / "icons" / "gmodular.ico")
_icon_arg = _icon if (HERE / "assets" / "icons" / "gmodular.ico").exists() else None

# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [str(HERE / "main.py")],
    pathex=[str(HERE)],
    binaries=list(_pyqt5_binaries),   # Qt DLLs from collect_all hook
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GModular",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # disabled — UPX often absent on fresh Windows, causes errors
    runtime_tmpdir=None,
    console=False,      # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_arg,
)
