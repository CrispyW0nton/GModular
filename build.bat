@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  GModular Build Script  v1.3
echo  KotOR Module Editor (K1 + K2)
echo  Produces: dist\GModular.exe
echo ============================================================
echo.

REM ── Navigate to the folder this .bat lives in ──────────────────────────────
cd /d "%~dp0"
echo Working directory: %CD%
echo.

REM ── Check Python is installed ───────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo.
    echo  Install Python 3.10+ from:  https://python.org/downloads/
    echo  Tick "Add Python to PATH" during the installer wizard.
    echo.
    pause
    exit /b 1
)
echo [OK] Python found:
python --version
echo.

REM ── Enforce Python 3.10+ ────────────────────────────────────────────────────
python -c "import sys; assert sys.version_info >= (3,10), 'Need 3.10+'" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10 or higher is required.
    python --version
    echo.
    echo  Download Python 3.12 from: https://python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python version is 3.10+.
echo.

REM ── Optional: use a virtual environment if 'venv' folder exists ─────────────
if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating existing virtual environment in .\venv ...
    call "venv\Scripts\activate.bat"
    echo [OK] Virtual environment activated.
    echo.
) else (
    echo [INFO] No .\venv folder found.
    echo        Tip: create one with  python -m venv venv  for an isolated build.
    echo        Building with the system Python instead.
    echo.
)

REM ── Upgrade pip silently ────────────────────────────────────────────────────
echo [....] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo [OK] pip up to date.
echo.

REM ── Install core dependencies (no-compile-required packages) ────────────────
echo [....] Installing core dependencies (PyQt5, numpy, watchdog, requests)...
python -m pip install ^
    "PyQt5>=5.15.0" ^
    "numpy>=1.21.0" ^
    "watchdog>=2.0.0" ^
    "requests>=2.28.0" ^
    --quiet

if errorlevel 1 (
    echo.
    echo [ERROR] Core dependency installation failed!
    echo  Possible causes:
    echo    - No internet connection
    echo    - Corporate proxy blocking pip  (try: pip install ... --proxy http://...)
    echo    - Antivirus quarantining pip packages
    echo.
    pause
    exit /b 1
)
echo [OK] Core dependencies installed.
echo.

REM ── Try to install moderngl from pre-built binary wheel ─────────────────────
REM    --only-binary :all:  prevents pip from trying to compile from source.
REM    If no pre-built wheel exists for this Python version, it will fail
REM    gracefully and we fall back to PyOpenGL (pure Python, no compiler needed).
echo [....] Trying moderngl (pre-built binary wheel only — no compiler needed)...
set MODERNGL_OK=0

python -m pip install "moderngl>=5.8.0" --only-binary :all: --quiet >nul 2>&1
if not errorlevel 1 (
    set MODERNGL_OK=1
    echo [OK] moderngl installed from pre-built wheel.
) else (
    echo [WARN] No pre-built moderngl wheel for your Python version.
    echo        Falling back to PyOpenGL (pure-Python OpenGL — no C++ compiler needed).
    echo        The 3D viewport will still work; only the low-level GL path differs.
    echo.
    python -m pip install "PyOpenGL>=3.1.0" "PyOpenGL_accelerate>=3.1.0" --quiet >nul 2>&1
    if not errorlevel 1 (
        echo [OK] PyOpenGL fallback installed.
    ) else (
        python -m pip install "PyOpenGL>=3.1.0" --quiet >nul 2>&1
        echo [OK] PyOpenGL installed (without accelerate — still functional).
    )
    echo.
    echo [INFO] ──────────────────────────────────────────────────────────────
    echo [INFO] OPTIONAL: To get full moderngl (faster 3D rendering), install
    echo [INFO] Microsoft Visual C++ Build Tools from:
    echo [INFO]   https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo [INFO] Then run this build script again.
    echo [INFO] ──────────────────────────────────────────────────────────────
    echo.
)

REM ── Install PyInstaller ─────────────────────────────────────────────────────
echo [....] Installing PyInstaller...
python -m pip install "pyinstaller>=5.13.0" --quiet
if errorlevel 1 (
    echo [ERROR] PyInstaller installation failed!
    pause
    exit /b 1
)
echo [OK] PyInstaller installed.
echo.

REM ── Optional soft dependencies (IPC / Flask) ────────────────────────────────
echo [....] Installing optional dependencies (flask, werkzeug — may skip)...
python -m pip install flask werkzeug jinja2 --quiet >nul 2>&1
echo [OK] Optional dependencies done.
echo.

REM ── Verify the icon file (generate if missing) ──────────────────────────────
echo [....] Checking icon file...
if exist "assets\icons\gmodular.ico" (
    echo [OK] Icon found: assets\icons\gmodular.ico
) else (
    echo [INFO] Icon not found — generating default icon...
    python tools\generate_icon.py >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Icon generation failed. Building without icon.
    ) else (
        echo [OK] Icon generated.
    )
)
echo.

REM ── Run quick self-test before building ─────────────────────────────────────
echo [....] Running fast import self-test...
python -c "from gmodular.formats.gff_types import GITData; from gmodular.core.module_state import ModuleState; from gmodular.gui.walkmesh_editor import WOKFace; from gmodular.ipc.callback_server import GModularIPCServer; print('  imports OK')"
if errorlevel 1 (
    echo [ERROR] Self-test import failed — your code has a syntax/import error.
    echo  Fix the error above before building the EXE.
    pause
    exit /b 1
)
echo [OK] Self-test passed.
echo.

REM ── Clean previous build artifacts ─────────────────────────────────────────
echo [....] Cleaning previous build...
if exist "dist\GModular.exe"  del  /f /q "dist\GModular.exe"  >nul 2>&1
if exist "dist\GModular"      rmdir /s /q "dist\GModular"     >nul 2>&1
if exist "build\GModular"     rmdir /s /q "build\GModular"    >nul 2>&1
echo [OK] Old artifacts removed.
echo.

REM ── Build the EXE ───────────────────────────────────────────────────────────
echo ============================================================
echo  Building GModular.exe via PyInstaller...
echo  This takes 1–3 minutes on first run.
echo ============================================================
echo.

python -m PyInstaller GModular.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [ERROR]  BUILD FAILED!
    echo ============================================================
    echo.
    echo  Common fixes:
    echo.
    echo  1. moderngl compile error / "Microsoft Visual C++ 14.0 required":
    echo       The script should have installed the pure-Python fallback above.
    echo       If you still see this, run:
    echo         pip install PyOpenGL --upgrade
    echo       Then re-run build.bat
    echo.
    echo  2. Antivirus blocking PyInstaller output:
    echo       Add the project folder to AV exclusions temporarily.
    echo.
    echo  3. Missing dependency:
    echo         pip install PyQt5 numpy pyinstaller
    echo.
    echo  4. UPX missing / broken (harmless — just makes EXE bigger):
    echo       Open GModular.spec, change  upx=True  to  upx=False
    echo.
    echo  5. Detailed log:
    echo         python -m PyInstaller GModular.spec --debug all
    echo.
    pause
    exit /b 1
)

REM ── Validate the output ─────────────────────────────────────────────────────
echo.
echo [....] Validating output...
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe not found after build!
    pause
    exit /b 1
)

REM Print file size for sanity check
for %%F in ("dist\GModular.exe") do (
    set /a size_mb=%%~zF / 1048576
    echo [OK] dist\GModular.exe  (!size_mb! MB)
)
echo.

REM ── Done ────────────────────────────────────────────────────────────────────
echo ============================================================
echo  BUILD COMPLETE!
echo ============================================================
echo.
echo  EXECUTABLE:
echo    dist\GModular.exe
echo.
echo  HOW TO RUN:
echo    1. Double-click  dist\GModular.exe  (no install needed).
echo    2. First run:
echo         Tools  ^>  Set Game Directory
echo         Point it at your KotOR 1 or 2 folder (the one with chitin.key).
echo    3. Click  "Load Assets"  to populate the palette.
echo.
echo  CREATE A NEW MODULE:
echo    File  ^>  New Module...
echo    Fill in the module name and game version, then click Create.
echo.
echo  PLACE OBJECTS:
echo    Double-click any asset in the palette, then click in the viewport.
echo    Press WASD in Play Mode to walk around the module.
echo.
echo  KOTOR MOD TOOLS SUITE (optional — all talk via IPC):
echo    GModular      — this module editor       (port 5003)
echo    GhostScripter — NWScript IDE              (port 5002)
echo    GhostRigger   — MDL model rigger          (port 5001)
echo.
if !MODERNGL_OK!==0 (
    echo  NOTE: Built with PyOpenGL fallback (moderngl not available).
    echo  For full 3D acceleration, install Visual C++ Build Tools from:
    echo    https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo  Then re-run build.bat to rebuild with moderngl.
    echo.
)
echo ============================================================
pause
