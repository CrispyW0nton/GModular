@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  GModular Build Script  v1.2
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

REM ── Install / upgrade core runtime dependencies ─────────────────────────────
echo [....] Installing runtime dependencies (PyQt5, moderngl, numpy, etc.)...
python -m pip install ^
    "PyQt5>=5.15.0" ^
    "moderngl>=5.8.0" ^
    "numpy>=1.21.0" ^
    "watchdog>=2.0.0" ^
    "requests>=2.28.0" ^
    --quiet

if errorlevel 1 (
    echo.
    echo [ERROR] Runtime dependency installation failed!
    echo  Possible causes:
    echo    - No internet connection
    echo    - Corporate proxy blocking pip  (try: pip install ... --proxy http://...)
    echo    - Antivirus quarantining pip packages
    echo.
    pause
    exit /b 1
)
echo [OK] Runtime dependencies installed.
echo.

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
    python tools\generate_icon.py
    if errorlevel 1 (
        echo [WARN] Icon generation failed. Building without icon (EXE will have default icon).
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
if exist "dist\GModular"      rmdir /s /q "dist\GModular"    >nul 2>&1
if exist "build\GModular"     rmdir /s /q "build\GModular"   >nul 2>&1
echo [OK] Old artifacts removed.
echo.

REM ── Build the EXE ───────────────────────────────────────────────────────────
echo ============================================================
echo  Building GModular.exe via PyInstaller...
echo  This takes 1–3 minutes on first run (UPX compression).
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
    echo    1. Antivirus blocking PyInstaller output:
    echo       Add the project folder to AV exclusions temporarily.
    echo    2. Missing dependency:
    echo       pip install PyQt5 moderngl numpy pyinstaller
    echo    3. UPX missing / broken:
    echo       Remove the UPX lines from GModular.spec  (set upx=False)
    echo    4. Detailed log:
    echo       python -m PyInstaller GModular.spec --debug all
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
echo    GModular     — this module editor       (port 5003)
echo    GhostScripter — NWScript IDE             (port 5002)
echo    GhostRigger   — MDL model rigger         (port 5001)
echo.
echo ============================================================
pause
