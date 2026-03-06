# -*- mode: python ; coding: utf-8 -*-
"""
GModular — KotOR Module Editor
PyInstaller spec file.

Produces: dist/GModular.exe  (single-file, windowed, ~50–80 MB)

Usage:
    python -m PyInstaller GModular.spec --clean --noconfirm

NOTE on moderngl:
    moderngl requires Microsoft Visual C++ Build Tools to compile from source.
    If moderngl is not installed, the build still succeeds using the PyOpenGL
    fallback. The viewport remains fully functional via PyOpenGL (pure Python).
    To install moderngl later:
        pip install moderngl --only-binary :all:
    Or install VC++ Build Tools from:
        https://visualstudio.microsoft.com/visual-cpp-build-tools/
"""

import sys
import importlib.util
from pathlib import Path

# ── Source directory ─────────────────────────────────────────────────────────
HERE = Path(SPECPATH)  # noqa: F821  (PyInstaller global)

# ── Detect which OpenGL backend is available ──────────────────────────────────
_has_moderngl  = importlib.util.find_spec("moderngl")  is not None
_has_pyopengl  = importlib.util.find_spec("OpenGL")    is not None

# ── Data files bundled into the EXE ─────────────────────────────────────────
datas = [
    # Assets and resources
    (str(HERE / "assets"),    "assets"),
    (str(HERE / "resources"), "resources"),
]

# ── Hidden imports ────────────────────────────────────────────────────────────
hidden_imports = [
    # GModular package — all sub-packages
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
    "gmodular.ipc",
    "gmodular.ipc.bridges",
    "gmodular.ipc.callback_server",
    "gmodular.utils",
    "gmodular.utils.resource_manager",
    # PyQt5 essentials
    "PyQt5",
    "PyQt5.QtWidgets",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtOpenGL",
    "PyQt5.sip",
    # numpy
    "numpy",
    "numpy.core",
    "numpy.core._multiarray_umath",
    # Requests (for IPC bridges)
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
    # Watchdog (optional — file watcher)
    "watchdog",
    "watchdog.observers",
    "watchdog.events",
    # Optional extras (soft imports in bridges/IPC)
    "flask",
    "werkzeug",
    "jinja2",
]

# Add moderngl only if it is actually installed (requires VC++ Build Tools)
if _has_moderngl:
    hidden_imports.append("moderngl")
    print("[spec] moderngl detected — bundling full GL backend")
else:
    print("[spec] moderngl NOT found — using PyOpenGL fallback")

# Add PyOpenGL if available (pure-Python fallback, no compiler needed)
if _has_pyopengl:
    hidden_imports += [
        "OpenGL",
        "OpenGL.GL",
        "OpenGL.GLU",
        "OpenGL.GLUT",
        "OpenGL.arrays",
        "OpenGL.arrays.numpymodule",
        "OpenGL.platform",
        "OpenGL.platform.win32",
    ]
    print("[spec] PyOpenGL detected — bundling as GL fallback")

# ── Excludes (reduce EXE size) ────────────────────────────────────────────────
excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "notebook",
    "IPython",
    "pytest",
    "PyQt5.QtWebEngine",
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtWebEngineCore",
    "PyQt5.QtMultimedia",
    "PyQt5.QtSql",
    "PyQt5.QtBluetooth",
    "PyQt5.QtNfc",
    "PyQt5.QtLocation",
    "PyQt5.QtPositioning",
    "PyQt5.QtSensors",
    "PyQt5.QtXml",
    "PyQt5.QtXmlPatterns",
    "PyQt5.QtHelp",
    "PyQt5.QtDesigner",
]

# Exclude whichever GL backend is NOT present
if not _has_moderngl:
    excludes.append("moderngl")
if not _has_pyopengl:
    excludes += ["OpenGL", "OpenGL.GL"]

# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [str(HERE / "main.py")],
    pathex=[str(HERE)],
    binaries=[],
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
    upx=True,
    upx_exclude=["vcruntime*.dll", "msvcp*.dll", "python*.dll"],
    runtime_tmpdir=None,
    console=False,   # windowed — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(HERE / "assets" / "icons" / "gmodular.ico")
    if (HERE / "assets" / "icons" / "gmodular.ico").exists()
    else None,
)
