@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

REM ============================================================
REM  GModular Build Script  v2.0.13
REM  KotOR Module Editor  |  Produces: dist\GModular.exe
REM
REM  This window will ALWAYS stay open on any error.
REM  Read the message carefully before closing.
REM ============================================================
echo ============================================================
echo  GModular Build Script  v2.0.13
echo  KotOR Module Editor  ^|  Produces: dist\GModular.exe
echo ============================================================
echo.

REM Change to the folder containing this .bat file
cd /d "%~dp0"
echo Working directory: %CD%
echo.

REM ---------------------------------------------------------------
REM  STEP 1 -- Find Python 3.10-3.12
REM  We find it ONCE here and store the path in PY_EXE.
REM  After venv activation we switch to VENV_PY (venv python).
REM ---------------------------------------------------------------
set "PY_EXE="

py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%P in ('py -3.12 -c "import sys; print(sys.executable)"') do set "PY_EXE=%%P"
    goto :found_python
)
py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%P in ('py -3.11 -c "import sys; print(sys.executable)"') do set "PY_EXE=%%P"
    goto :found_python
)
py -3.10 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%P in ('py -3.10 -c "import sys; print(sys.executable)"') do set "PY_EXE=%%P"
    goto :found_python
)
python --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%P in ('python -c "import sys; print(sys.executable)"') do set "PY_EXE=%%P"
    goto :found_python
)
python3 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%P in ('python3 -c "import sys; print(sys.executable)"') do set "PY_EXE=%%P"
    goto :found_python
)

echo [ERROR] Python not found.
echo.
echo  QUICK FIX: run setup_python.bat to auto-download Python 3.12.
echo  Or install manually:
echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
echo.
echo  WARNING: Do NOT use the Microsoft Store Python -- it breaks venv.
echo  Tick "Add Python 3.12 to PATH" during install, then re-run.
echo.
goto :die

:found_python
if "%PY_EXE%"=="" (
    echo [ERROR] Could not resolve Python executable path.
    goto :die
)
echo [OK] Python: %PY_EXE%

REM Check version
for /f "tokens=*" %%V in ('"%PY_EXE%" -c "import sys; print(sys.version_info.major)"') do set "PY_MAJOR=%%V"
for /f "tokens=*" %%V in ('"%PY_EXE%" -c "import sys; print(sys.version_info.minor)"') do set "PY_MINOR=%%V"
echo [OK] Version: %PY_MAJOR%.%PY_MINOR%
echo.

if %PY_MAJOR% EQU 3 if %PY_MINOR% GEQ 13 (
    echo [ERROR] Python %PY_MAJOR%.%PY_MINOR% is not supported.
    echo  PyQt5 only supports Python 3.8-3.12.
    echo  Run setup_python.bat to install Python 3.12 automatically.
    echo.
    goto :die
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo [ERROR] Python 3.10+ required. You have %PY_MAJOR%.%PY_MINOR%.
    echo  Run setup_python.bat to install Python 3.12 automatically.
    echo.
    goto :die
)
echo [OK] Python version is compatible.
echo.

REM ---------------------------------------------------------------
REM  STEP 2 -- Virtual environment
REM  After this we use VENV_PY (the venv python.exe directly).
REM  This avoids any confusion between system Python and venv.
REM ---------------------------------------------------------------
set "VENV_PY=%CD%\venv\Scripts\python.exe"

if exist "venv\Scripts\python.exe" (
    echo [INFO] Using existing venv.
) else (
    echo [INFO] Creating venv...
    "%PY_EXE%" -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        echo  Try running as Administrator, or delete the venv folder and retry.
        echo.
        goto :die
    )
    echo [OK] venv created.
)

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv\Scripts\python.exe not found after venv creation.
    echo.
    goto :die
)
echo [OK] venv Python: %VENV_PY%
echo.

REM ---------------------------------------------------------------
REM  STEP 3 -- Upgrade pip  (using venv python directly)
REM ---------------------------------------------------------------
echo [....] Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
echo [OK] pip ready.
echo.

REM ---------------------------------------------------------------
REM  STEP 4 -- PyQt5 + qtpy
REM ---------------------------------------------------------------
echo [....] Installing PyQt5 + qtpy...
"%VENV_PY%" -m pip install "PyQt5>=5.15.0,<6.0" "qtpy>=2.4.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyQt5/qtpy install failed.
    echo  PyQt5 only has wheels for Python 3.8-3.12.
    echo  You are on Python %PY_MAJOR%.%PY_MINOR%.
    echo.
    goto :die
)
echo [OK] PyQt5 + qtpy installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 5 -- numpy, watchdog, requests, typing-extensions
REM ---------------------------------------------------------------
echo [....] Installing numpy, watchdog, requests, typing-extensions...
"%VENV_PY%" -m pip install "numpy>=1.21.0" "watchdog>=2.0.0" "requests>=2.28.0" "typing_extensions>=4.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Failed to install core dependencies.
    echo.
    goto :die
)
echo [OK] Core dependencies installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 6 -- moderngl (binary only) or PyOpenGL fallback
REM ---------------------------------------------------------------
echo [....] Trying moderngl...
set "MODERNGL_OK=0"
"%VENV_PY%" -m pip install "moderngl>=5.8.0" --only-binary :all: --quiet --disable-pip-version-check >nul 2>&1
if not errorlevel 1 (
    set "MODERNGL_OK=1"
    echo [OK] moderngl installed.
) else (
    echo [INFO] moderngl not available -- installing PyOpenGL fallback.
    "%VENV_PY%" -m pip install "PyOpenGL>=3.1.0" --quiet --disable-pip-version-check >nul 2>&1
    echo [OK] PyOpenGL installed.
)
echo.

REM ---------------------------------------------------------------
REM  STEP 7 -- PyInstaller
REM ---------------------------------------------------------------
echo [....] Installing PyInstaller...
"%VENV_PY%" -m pip install "pyinstaller>=5.13.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed.
    echo.
    goto :die
)
echo [OK] PyInstaller installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 8 -- Optional: flask/werkzeug
REM ---------------------------------------------------------------
"%VENV_PY%" -m pip install flask werkzeug jinja2 --quiet --disable-pip-version-check >nul 2>&1

REM ---------------------------------------------------------------
REM  STEP 9 -- Icon
REM ---------------------------------------------------------------
echo [....] Checking icon...
if exist "assets\icons\gmodular.ico" (
    echo [OK] Icon present.
) else (
    "%VENV_PY%" tools\generate_icon.py >nul 2>&1
    if exist "assets\icons\gmodular.ico" (
        echo [OK] Icon generated.
    ) else (
        echo [INFO] No icon -- EXE will use default Windows icon.
    )
)
echo.

REM ---------------------------------------------------------------
REM  STEP 10 -- Self-test
REM  Uses findstr (no Python file generation, no quoting issues).
REM ---------------------------------------------------------------
echo [....] GModular self-test...

if not exist "gmodular\core\module_state.py" (
    echo [ERROR] gmodular\core\module_state.py not found.
    echo  Run build.bat from the GModular root folder.
    echo  The folder must contain: gmodular\  build.bat  GModular.spec
    echo.
    goto :die
)

if not exist "gmodular\core\events.py" (
    echo [ERROR] gmodular\core\events.py not found.
    echo  Re-download the ZIP from GitHub -- this file is missing:
    echo    https://github.com/CrispyW0nton/GModular/archive/refs/heads/main.zip
    echo.
    goto :die
)

findstr /i "dmodular" "gmodular\core\module_state.py" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] gmodular\core\module_state.py has a bad import.
    echo  Re-download from GitHub:
    echo    https://github.com/CrispyW0nton/GModular/archive/refs/heads/main.zip
    echo.
    goto :die
)

"%VENV_PY%" -c "import gmodular; from gmodular.core.events import get_event_bus; from gmodular.core.module_state import ModuleState; print('[OK] GModular', gmodular.__version__, 'imports clean')"
if errorlevel 1 (
    echo.
    echo [ERROR] GModular import failed -- see traceback above.
    echo.
    echo  Most likely fix:
    echo    "%VENV_PY%" -m pip install PyQt5 qtpy moderngl numpy
    echo.
    goto :die
)
echo.

REM ---------------------------------------------------------------
REM  STEP 10b -- GhostRigger (non-fatal)
REM ---------------------------------------------------------------
echo [....] GhostRigger self-test...
"%VENV_PY%" -c "import sys,os; sys.path.insert(0,os.path.join(os.getcwd(),'ghostrigger')); from ghostrigger.core.blueprint_state import BlueprintRegistry; print('GhostRigger OK')"
if errorlevel 1 (
    echo [WARN] GhostRigger unavailable (non-fatal).
) else (
    echo [OK] GhostRigger OK.
)
echo.

REM ---------------------------------------------------------------
REM  STEP 10c -- GhostScripter (non-fatal)
REM ---------------------------------------------------------------
echo [....] GhostScripter self-test...
"%VENV_PY%" -c "import sys,os; sys.path.insert(0,os.path.join(os.getcwd(),'ghostscripter')); from ghostscripter.core.script_state import ScriptRegistry; print('GhostScripter OK')"
if errorlevel 1 (
    echo [WARN] GhostScripter unavailable (non-fatal).
) else (
    echo [OK] GhostScripter OK.
)
echo.

REM ---------------------------------------------------------------
REM  STEP 11 -- Clean old artifacts
REM ---------------------------------------------------------------
echo [....] Cleaning old build artifacts...
if exist "dist\GModular.exe" del /f /q "dist\GModular.exe" >nul 2>&1
if exist "dist\GModular"     rmdir /s /q "dist\GModular"   >nul 2>&1
if exist "build\GModular"    rmdir /s /q "build\GModular"  >nul 2>&1
echo [OK] Cleaned.
echo.

REM ---------------------------------------------------------------
REM  STEP 12 -- BUILD EXE
REM ---------------------------------------------------------------
echo ============================================================
echo  Building GModular.exe ...  (this takes 1-3 minutes)
echo ============================================================
echo.

"%VENV_PY%" -m PyInstaller GModular.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [ERROR]  BUILD FAILED
    echo ============================================================
    echo.
    echo  Common fixes:
    echo    1. Antivirus blocked output -- add this folder to exclusions
    echo    2. Permission error -- right-click build.bat, Run as Administrator
    echo    3. For a detailed log run:
    echo         "%VENV_PY%" -m PyInstaller GModular.spec --clean --log-level DEBUG
    echo.
    goto :die
)

REM ---------------------------------------------------------------
REM  STEP 13 -- Validate
REM ---------------------------------------------------------------
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe was not produced. Build may have silently failed.
    echo.
    goto :die
)

for %%F in ("dist\GModular.exe") do (
    set /a "SIZE_MB=%%~zF / 1048576"
    echo [OK] dist\GModular.exe -- !SIZE_MB! MB
)
echo.

REM ---------------------------------------------------------------
REM  DONE
REM ---------------------------------------------------------------
echo ============================================================
echo  BUILD COMPLETE
echo ============================================================
echo.
echo  File:  dist\GModular.exe
echo.
echo  HOW TO RUN:
echo    Double-click dist\GModular.exe
echo.
echo  FIRST TIME:
echo    Tools ^> Set Game Directory
echo    Point to your KotOR folder (the folder with chitin.key)
echo    Click "Load Assets"
echo.
if "!MODERNGL_OK!"=="0" (
    echo  NOTE: 3D viewport is using PyOpenGL (no moderngl wheel available^).
    echo.
)
echo ============================================================
echo.
echo  Press any key to close this window.
pause
goto :eof

REM ---------------------------------------------------------------
REM  :die -- all error paths land here; window NEVER closes by itself
REM ---------------------------------------------------------------
:die
echo.
echo ============================================================
echo  BUILD STOPPED.  Read the [ERROR] above.
echo  Press any key to close this window.
echo ============================================================
echo.
pause
exit /b 1
