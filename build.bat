@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  GModular Build Script  v1.5
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
    echo ============================================================
    echo  HOW TO INSTALL PYTHON 3.12  (IMPORTANT: read carefully)
    echo ============================================================
    echo.
    echo  STEP 1:  Open this URL in your browser:
    echo.
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    echo  STEP 2:  Run the downloaded  python-3.12.9-amd64.exe
    echo.
    echo  STEP 3:  On the FIRST installer screen, tick BOTH boxes:
    echo             [x] Install launcher for all users
    echo             [x] Add Python 3.12 to PATH        <-- IMPORTANT
    echo           Then click "Install Now".
    echo.
    echo  IMPORTANT - DO NOT use the Microsoft Store version of Python.
    echo  If you see "Python Install Manager" or "Get" in the Store,
    echo  close it and use the direct .exe link above instead.
    echo  The Store version is blocked on many work/school PCs.
    echo.
    echo  STEP 4:  Close this window and re-run build.bat.
    echo ============================================================
    echo.
    echo  Want setup_python.bat to download Python 3.12 automatically?
    echo  Run:  setup_python.bat
    echo.
    pause
    exit /b 1
)
echo [OK] Python found:
python --version
echo.

REM ── Read the exact Python minor version ──────────────────────────────────────
for /f "tokens=*" %%V in ('python -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%V
for /f "tokens=*" %%V in ('python -c "import sys; print(sys.version_info.major)"') do set PY_MAJOR=%%V

REM ── Block Python 3.13+ — PyQt5 has no wheel for it yet ───────────────────────
if %PY_MAJOR% EQU 3 if %PY_MINOR% GEQ 13 (
    echo.
    echo ============================================================
    echo  [ERROR]  PYTHON %PY_MAJOR%.%PY_MINOR% IS NOT SUPPORTED
    echo ============================================================
    echo.
    echo  You have Python %PY_MAJOR%.%PY_MINOR% but GModular requires Python 3.12.
    echo.
    echo  WHY: PyQt5 (the GUI library) only has pre-built packages
    echo  for Python 3.8 through 3.12. Python 3.13 and 3.14 have
    echo  no PyQt5 package available yet.
    echo.
    echo  FIX — Install Python 3.12 using the DIRECT installer:
    echo.
    echo  STEP 1:  Open this link in your browser:
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    echo  STEP 2:  Run python-3.12.9-amd64.exe
    echo           Tick "Add Python 3.12 to PATH" and click Install Now.
    echo.
    echo  !! DO NOT use the Microsoft Store / python-manager.msix !!
    echo  !! The Store version is blocked on many PCs.             !!
    echo  !! Always use the direct .exe from python.org            !!
    echo.
    echo  STEP 3:  After Python 3.12 installs, open a NEW cmd window
    echo           and run build.bat again.
    echo.
    echo  TIP: If you need to keep Python %PY_MAJOR%.%PY_MINOR% as well, run:
    echo    py -3.12 -m venv venv
    echo    venv\Scripts\activate.bat
    echo    build.bat
    echo.
    echo  OR run  setup_python.bat  to auto-download Python 3.12.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

REM ── Enforce Python 3.10+ ────────────────────────────────────────────────────
python -c "import sys; assert sys.version_info >= (3,10), 'Need 3.10+'" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10 or higher is required (3.12 recommended).
    python --version
    echo.
    echo  Download Python 3.12 direct installer (NOT the Store version):
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    pause
    exit /b 1
)
echo [OK] Python %PY_MAJOR%.%PY_MINOR% — compatible.
echo.

REM ── Optional: use a virtual environment if 'venv' folder exists ─────────────
if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating existing virtual environment in .\venv ...
    call "venv\Scripts\activate.bat"
    echo [OK] Virtual environment activated.
    echo.
) else (
    echo [INFO] No .\venv folder found.
    echo        Recommended: create one with  python -m venv venv
    echo        then  venv\Scripts\activate.bat  and re-run build.bat.
    echo        Building with the system Python instead.
    echo.
)

REM ── Upgrade pip silently ────────────────────────────────────────────────────
echo [....] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo [OK] pip up to date.
echo.

REM ── Install PyQt5 — most likely to fail, diagnose carefully ─────────────────
echo [....] Installing PyQt5...
python -m pip install "PyQt5>=5.15.0" --quiet
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [ERROR]  PyQt5 installation failed!
    echo ============================================================
    echo.
    echo  PyQt5 pre-built wheels exist for Python 3.8 to 3.12 only.
    echo  You are running Python %PY_MAJOR%.%PY_MINOR%.
    echo.
    if %PY_MINOR% GEQ 13 (
        echo  Python %PY_MAJOR%.%PY_MINOR% is NOT supported by PyQt5.
        echo  Install Python 3.12 using the DIRECT .exe installer:
        echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
        echo.
        echo  DO NOT use the Microsoft Store / python-manager.msix
        echo  — it may be blocked by policy on your PC.
    ) else (
        echo  Possible causes on Python %PY_MAJOR%.%PY_MINOR%:
        echo    - No internet connection
        echo    - Corporate proxy  (add --proxy http://host:port to pip)
        echo    - pip cache corrupted  (run: pip cache purge)
    )
    echo.
    pause
    exit /b 1
)
echo [OK] PyQt5 installed.
echo.

REM ── Install remaining core dependencies ─────────────────────────────────────
echo [....] Installing numpy, watchdog, requests...
python -m pip install ^
    "numpy>=1.21.0" ^
    "watchdog>=2.0.0" ^
    "requests>=2.28.0" ^
    --quiet
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install numpy / watchdog / requests.
    echo  Try manually:  pip install numpy watchdog requests
    echo  Then re-run build.bat.
    echo.
    pause
    exit /b 1
)
echo [OK] Core dependencies installed.
echo.

REM ── Try to install moderngl from pre-built binary wheel ─────────────────────
echo [....] Trying moderngl (pre-built binary wheel only — no compiler needed)...
set MODERNGL_OK=0

python -m pip install "moderngl>=5.8.0" --only-binary :all: --quiet >nul 2>&1
if not errorlevel 1 (
    set MODERNGL_OK=1
    echo [OK] moderngl installed from pre-built wheel.
) else (
    echo [WARN] No pre-built moderngl wheel for Python %PY_MAJOR%.%PY_MINOR%.
    echo        Falling back to PyOpenGL (pure-Python — no C++ compiler needed).
    echo.
    python -m pip install "PyOpenGL>=3.1.0" "PyOpenGL_accelerate>=3.1.0" --quiet >nul 2>&1
    if not errorlevel 1 (
        echo [OK] PyOpenGL fallback installed.
    ) else (
        python -m pip install "PyOpenGL>=3.1.0" --quiet >nul 2>&1
        echo [OK] PyOpenGL installed (without accelerate — still functional).
    )
    echo.
    echo [INFO] OPTIONAL: For full moderngl, install Visual C++ Build Tools:
    echo [INFO]   https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo.
)

REM ── Install PyInstaller ─────────────────────────────────────────────────────
echo [....] Installing PyInstaller...
python -m pip install "pyinstaller>=5.13.0" --quiet
if errorlevel 1 (
    echo [ERROR] PyInstaller installation failed!
    echo  Try:  pip install pyinstaller
    pause
    exit /b 1
)
echo [OK] PyInstaller installed.
echo.

REM ── Optional soft dependencies ──────────────────────────────────────────────
echo [....] Installing optional dependencies (flask, werkzeug)...
python -m pip install flask werkzeug jinja2 --quiet >nul 2>&1
echo [OK] Optional dependencies done.
echo.

REM ── Verify the icon file ─────────────────────────────────────────────────────
echo [....] Checking icon file...
if exist "assets\icons\gmodular.ico" (
    echo [OK] Icon found: assets\icons\gmodular.ico
) else (
    echo [INFO] Generating default icon...
    python tools\generate_icon.py >nul 2>&1
    if errorlevel 1 (echo [WARN] Icon generation failed.) else (echo [OK] Icon generated.)
)
echo.

REM ── Run quick self-test before building ─────────────────────────────────────
echo [....] Running fast import self-test...
python -c "from gmodular.formats.gff_types import GITData; from gmodular.core.module_state import ModuleState; from gmodular.gui.walkmesh_editor import WOKFace; from gmodular.ipc.callback_server import GModularIPCServer; print('  imports OK')"
if errorlevel 1 (
    echo [ERROR] Self-test import failed. Fix the error above first.
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
echo  This takes 1-3 minutes on first run.
echo ============================================================
echo.

python -m PyInstaller GModular.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [ERROR]  BUILD FAILED
    echo ============================================================
    echo.
    echo  1. Wrong Python version (most common):
    echo       Use Python 3.12. Download direct installer (NOT Store):
    echo       https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    echo  2. moderngl / "Visual C++ 14.0 required":
    echo       pip install PyOpenGL --upgrade
    echo       then re-run build.bat
    echo.
    echo  3. Antivirus blocking PyInstaller output:
    echo       Add this folder to AV exclusions, then retry.
    echo.
    echo  4. UPX error (makes EXE bigger, not broken):
    echo       Open GModular.spec, change upx=True to upx=False
    echo.
    echo  5. Detailed log:
    echo       python -m PyInstaller GModular.spec --debug all
    echo.
    pause
    exit /b 1
)

REM ── Validate output ──────────────────────────────────────────────────────────
echo.
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe not found after build!
    pause
    exit /b 1
)
for %%F in ("dist\GModular.exe") do (
    set /a size_mb=%%~zF / 1048576
    echo [OK] dist\GModular.exe  (!size_mb! MB)
)
echo.

echo ============================================================
echo  BUILD COMPLETE!
echo ============================================================
echo.
echo  EXECUTABLE:   dist\GModular.exe
echo.
echo  HOW TO RUN:
echo    1. Double-click  dist\GModular.exe  (no install needed).
echo    2. First run:  Tools ^> Set Game Directory
echo       Point it at your KotOR folder (contains chitin.key).
echo    3. Click "Load Assets" to fill the palette.
echo.
echo  NEW MODULE:   File ^> New Module...
echo  PLACE ITEMS:  Double-click palette, click in viewport.
echo  PLAY MODE:    Press WASD to walk around the module.
echo.
if !MODERNGL_OK!==0 (
    echo  NOTE: Built with PyOpenGL fallback (no moderngl).
    echo  Optional upgrade:
    echo    https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo.
)
echo ============================================================
pause
