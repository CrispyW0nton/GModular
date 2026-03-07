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
    PyQt5 wheels exist for Python 3.8 - 3.12 only.
    Do NOT use Python 3.13 or 3.14.  Use Python 3.12.

HOW PyQt5 COLLECTION WORKS (v2.2):
    Three-layer defence so the EXE always contains the full Qt runtime:

    Layer 1 - hookspath=['hooks']:
        hooks/hook-PyQt5.py and hooks/hook-PyQt5.QtWidgets.py call
        collect_all('PyQt5'), which copies Qt5*.dll, platforms/, styles/,
        imageformats/, and all .pyd extension modules.

    Layer 2 - spec-level collect_all() call (wrapped in try/except):
        Explicit call here adds the results to binaries= and datas= so
        they end up in the EXE even if the hook path mechanism is skipped.

    Layer 3 - runtime_hooks=['runtime_hooks/pyi_rth_pyqt5.py']:
        Runs inside the frozen EXE at boot time and pre-imports every
        PyQt5 module, giving a clear error at startup rather than a
        cryptic NameError inside GUI code.

    Without all three layers: QGroupBox (and every other Qt widget class)
    raises NameError because the .pyd binding file was never included.
"""

import sys
import importlib.util
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE = Path(SPECPATH)   # noqa: F821  (PyInstaller built-in)

# ── Detect available optional backends ───────────────────────────────────────
_has_moderngl = importlib.util.find_spec("moderngl") is not None
_has_pyopengl = importlib.util.find_spec("OpenGL")   is not None
_has_flask     = importlib.util.find_spec("flask")    is not None
_has_watchdog  = importlib.util.find_spec("watchdog") is not None

print(f"[spec] moderngl  : {'YES' if _has_moderngl else 'NO (will use PyOpenGL fallback)'}")
print(f"[spec] PyOpenGL  : {'YES' if _has_pyopengl else 'NO'}")
print(f"[spec] flask     : {'YES' if _has_flask    else 'NO (optional - skipped)'}")
print(f"[spec] watchdog  : {'YES' if _has_watchdog else 'NO (optional - skipped)'}")

# ── Layer 2: collect_all for PyQt5 (try/except so spec never hard-fails) ─────
# Even if this fails, Layer 1 (hookspath) and Layer 3 (runtime hook) still run.
try:
    from PyInstaller.utils.hooks import collect_all as _collect_all   # noqa
    _pyqt5_datas, _pyqt5_binaries, _pyqt5_hiddenimports = _collect_all("PyQt5")
    print(f"[spec] PyQt5 collect_all: {len(_pyqt5_binaries)} binaries, "
          f"{len(_pyqt5_datas)} datas, {len(_pyqt5_hiddenimports)} hidden")
except Exception as _e:
    print(f"[spec] WARNING: collect_all('PyQt5') failed ({_e}); "
          f"relying on hookspath for PyQt5 collection")
    _pyqt5_datas, _pyqt5_binaries, _pyqt5_hiddenimports = [], [], []

# ── Data files bundled into the EXE ─────────────────────────────────────────
datas = list(_pyqt5_datas)
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
    # ── PyQt5 explicit (belt-and-suspenders) ─────────────────────────────
    "PyQt5",
    "PyQt5.QtWidgets",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtOpenGL",
    "PyQt5.QtPrintSupport",
    "PyQt5.sip",
    # ── numpy ─────────────────────────────────────────────────────────────
    "numpy",
    "numpy.core",
    "numpy.core._multiarray_umath",
    # ── requests (IPC) ────────────────────────────────────────────────────
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
]
# Merge collect_all hidden imports (deduped)
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
    "sip",   # bare 'sip' is obsolete; PyQt5.sip is used instead
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
    binaries=list(_pyqt5_binaries),
    datas=datas,
    hiddenimports=hidden_imports,
    # Layer 1: local hooks trigger collect_all for PyQt5
    hookspath=["hooks"],
    hooksconfig={},
    # Layer 3: runtime hook pre-imports all PyQt5 modules inside the EXE
    runtime_hooks=["runtime_hooks/pyi_rth_pyqt5.py"],
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
    upx=False,          # disabled - UPX often absent on fresh Windows
    runtime_tmpdir=None,
    console=False,      # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_arg,
)
