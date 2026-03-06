@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

echo ============================================================
echo  GModular Build Script  v1.7
echo  KotOR Module Editor  ^|  Produces: dist\GModular.exe
echo ============================================================
echo.

cd /d "%~dp0"
echo Working directory: %CD%
echo.

REM ---------------------------------------------------------------
REM  STEP 1 -- Check Python is on PATH
REM ---------------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo.
    echo  ====================================================
    echo  HOW TO INSTALL PYTHON 3.12  (IMPORTANT)
    echo  ====================================================
    echo.
    echo  1. Open this link in your browser and download it:
    echo.
    echo     https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    echo  2. Run  python-3.12.9-amd64.exe
    echo.
    echo  3. On the first screen tick BOTH boxes:
    echo       [x] Install launcher for all users
    echo       [x] Add Python 3.12 to PATH    ^<-- REQUIRED
    echo     Then click "Install Now".
    echo.
    echo  4. Close this window and re-run build.bat.
    echo.
    echo  !! DO NOT use the Microsoft Store !!
    echo  !! The Store msix is blocked on many PCs. !!
    echo  !! Always use the direct .exe from python.org. !!
    echo.
    echo  (Or run setup_python.bat to download automatically)
    echo  ====================================================
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%V in ('python -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%V
for /f "tokens=*" %%V in ('python -c "import sys; print(sys.version_info.major)"') do set PY_MAJOR=%%V
echo [OK] Python %PY_MAJOR%.%PY_MINOR% found.
echo.

REM ---------------------------------------------------------------
REM  STEP 2 -- Block Python 3.13+ (no PyQt5 wheel above 3.12)
REM ---------------------------------------------------------------
if %PY_MAJOR% EQU 3 if %PY_MINOR% GEQ 13 (
    echo.
    echo  ====================================================
    echo  [ERROR]  Python %PY_MAJOR%.%PY_MINOR% is NOT supported
    echo  ====================================================
    echo.
    echo  You have Python %PY_MAJOR%.%PY_MINOR%.
    echo  GModular needs Python 3.12 because PyQt5 (the GUI
    echo  library) only has packages for Python 3.8 to 3.12.
    echo.
    echo  FIX:
    echo  1. Download Python 3.12 direct installer (NOT Store):
    echo     https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    echo  2. Tick "Add Python 3.12 to PATH", click Install Now.
    echo.
    echo  3. Open a NEW cmd window and re-run build.bat.
    echo.
    echo  If you want to keep Python %PY_MAJOR%.%PY_MINOR% as well, run:
    echo    py -3.12 -m venv venv
    echo    venv\Scripts\activate.bat
    echo    build.bat
    echo.
    echo  (Or run setup_python.bat to download automatically)
    echo  ====================================================
    echo.
    pause
    exit /b 1
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo [ERROR] Python 3.10 or higher required. You have %PY_MAJOR%.%PY_MINOR%.
    echo  Download: https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    pause
    exit /b 1
)
echo [OK] Python version %PY_MAJOR%.%PY_MINOR% is compatible.
echo.

REM ---------------------------------------------------------------
REM  STEP 3 -- Virtual environment (use if present)
REM ---------------------------------------------------------------
if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment...
    call "venv\Scripts\activate.bat"
    echo [OK] venv activated.
) else (
    echo [INFO] No venv found -- using system Python.
    echo        Tip: run  python -m venv venv  for an isolated build.
)
echo.

REM ---------------------------------------------------------------
REM  STEP 4 -- Upgrade pip
REM ---------------------------------------------------------------
echo [....] Upgrading pip...
python -m pip install --upgrade pip --quiet 2>nul
echo [OK] pip ready.
echo.

REM ---------------------------------------------------------------
REM  STEP 5 -- Install PyQt5  (own step -- most failure-prone)
REM ---------------------------------------------------------------
echo [....] Installing PyQt5...
python -m pip install "PyQt5>=5.15.0,<6.0" --quiet
if errorlevel 1 (
    echo.
    echo  ====================================================
    echo  [ERROR]  PyQt5 install failed!
    echo  ====================================================
    echo.
    echo  Likely cause: wrong Python version.
    echo  PyQt5 wheels only exist for Python 3.8 to 3.12.
    echo  You are running Python %PY_MAJOR%.%PY_MINOR%.
    echo.
    echo  Fix: use Python 3.12.
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo  (direct .exe installer -- NOT the Microsoft Store)
    echo.
    pause
    exit /b 1
)
echo [OK] PyQt5 installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 6 -- Install numpy, watchdog, requests
REM ---------------------------------------------------------------
echo [....] Installing numpy, watchdog, requests...
python -m pip install "numpy>=1.21.0" "watchdog>=2.0.0" "requests>=2.28.0" --quiet
if errorlevel 1 (
    echo [ERROR] Failed. Try:  pip install numpy watchdog requests
    pause
    exit /b 1
)
echo [OK] numpy, watchdog, requests installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 7 -- Install moderngl (binary-only) or PyOpenGL fallback
REM ---------------------------------------------------------------
echo [....] Trying moderngl (binary wheel -- no C++ compiler needed)...
set MODERNGL_OK=0
python -m pip install "moderngl>=5.8.0" --only-binary :all: --quiet >nul 2>&1
if not errorlevel 1 (
    set MODERNGL_OK=1
    echo [OK] moderngl installed.
) else (
    echo [WARN] No moderngl wheel for Python %PY_MAJOR%.%PY_MINOR% -- using PyOpenGL fallback.
    python -m pip install "PyOpenGL>=3.1.0" --quiet >nul 2>&1
    echo [OK] PyOpenGL fallback installed.
    echo.
    echo  Optional: install Visual C++ Build Tools for full moderngl:
    echo    https://visualstudio.microsoft.com/visual-cpp-build-tools/
)
echo.

REM ---------------------------------------------------------------
REM  STEP 8 -- Install PyInstaller
REM ---------------------------------------------------------------
echo [....] Installing PyInstaller...
python -m pip install "pyinstaller>=5.13.0" --quiet
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed.
    echo  Try:  pip install pyinstaller
    pause
    exit /b 1
)
echo [OK] PyInstaller installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 9 -- Optional: flask/werkzeug (IPC server, may skip)
REM ---------------------------------------------------------------
python -m pip install flask werkzeug jinja2 --quiet >nul 2>&1

REM ---------------------------------------------------------------
REM  STEP 10 -- Generate icon if missing
REM ---------------------------------------------------------------
echo [....] Checking icon...
if exist "assets\icons\gmodular.ico" (
    echo [OK] Icon present.
) else (
    python tools\generate_icon.py >nul 2>&1
    if exist "assets\icons\gmodular.ico" (
        echo [OK] Icon generated.
    ) else (
        echo [WARN] Icon missing -- EXE will use default Windows icon.
    )
)
echo.

REM ---------------------------------------------------------------
REM  STEP 11 -- Quick import self-test
REM ---------------------------------------------------------------
echo [....] Import self-test...
python -c "from gmodular.formats.gff_types import GITData; from gmodular.core.module_state import ModuleState; print('OK')"
if errorlevel 1 (
    echo [ERROR] Import failed -- fix the error shown above first.
    pause
    exit /b 1
)
echo [OK] Self-test passed.
echo.

REM ---------------------------------------------------------------
REM  STEP 12 -- Clean old build artifacts
REM ---------------------------------------------------------------
echo [....] Cleaning old build...
if exist "dist\GModular.exe" del /f /q "dist\GModular.exe" >nul 2>&1
if exist "dist\GModular"     rmdir /s /q "dist\GModular"   >nul 2>&1
if exist "build\GModular"    rmdir /s /q "build\GModular"  >nul 2>&1
echo [OK] Cleaned.
echo.

REM ---------------------------------------------------------------
REM  STEP 13 -- BUILD
REM ---------------------------------------------------------------
echo ============================================================
echo  Building GModular.exe ...  (1-3 minutes)
echo ============================================================
echo.

python -m PyInstaller GModular.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [ERROR]  BUILD FAILED
    echo ============================================================
    echo.
    echo  Common fixes:
    echo.
    echo  1. Wrong Python version -- use Python 3.12:
    echo       https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo       (direct .exe -- NOT the Microsoft Store)
    echo.
    echo  2. Antivirus blocking the build output:
    echo       Temporarily exclude this folder from AV, then retry.
    echo.
    echo  3. Permission error writing to dist\ or build\:
    echo       Run this window as Administrator.
    echo.
    echo  4. See full log above for the exact error line.
    echo       Or run:  python -m PyInstaller GModular.spec --debug all
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------
REM  STEP 14 -- Validate
REM ---------------------------------------------------------------
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe not found after build!
    pause
    exit /b 1
)
for %%F in ("dist\GModular.exe") do (
    set /a size_mb=%%~zF / 1048576
    echo [OK] dist\GModular.exe  built successfully  (!size_mb! MB^)
)
echo.

REM ---------------------------------------------------------------
REM  DONE
REM ---------------------------------------------------------------
echo ============================================================
echo  BUILD COMPLETE!
echo ============================================================
echo.
echo  File:  dist\GModular.exe
echo.
echo  HOW TO RUN:
echo    Double-click dist\GModular.exe  (no install needed)
echo.
echo  FIRST TIME:
echo    Tools ^> Set Game Directory
echo    Point it at your KotOR folder (contains chitin.key)
echo    Click "Load Assets"
echo.
if !MODERNGL_OK!==0 (
    echo  NOTE: 3D viewport using PyOpenGL fallback.
    echo  Optional upgrade: install Visual C++ Build Tools +
    echo  run build.bat again for full moderngl acceleration.
    echo.
)
echo ============================================================
pause
