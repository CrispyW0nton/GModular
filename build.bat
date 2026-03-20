@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

REM ============================================================
REM  GModular Build Script  v2.0.11
REM  KotOR Module Editor  |  Produces: dist\GModular.exe
REM
REM  IMPORTANT: This window will stay open on any error.
REM  Read the [ERROR] message carefully before closing.
REM ============================================================
echo ============================================================
echo  GModular Build Script  v2.0.11
echo  KotOR Module Editor  ^|  Produces: dist\GModular.exe
echo ============================================================
echo(

REM Change to the folder containing this .bat file
cd /d "%~dp0"
echo Working directory: %CD%
echo(

REM ---------------------------------------------------------------
REM  STEP 1 -- Find Python  (py launcher  OR  python  OR  python3)
REM ---------------------------------------------------------------
set "PY="

py -3.12 --version >nul 2>&1
if not errorlevel 1 ( set "PY=py -3.12" & goto :found_python )

py -3.11 --version >nul 2>&1
if not errorlevel 1 ( set "PY=py -3.11" & goto :found_python )

py -3.10 --version >nul 2>&1
if not errorlevel 1 ( set "PY=py -3.10" & goto :found_python )

py --version >nul 2>&1
if not errorlevel 1 ( set "PY=py" & goto :found_python )

python --version >nul 2>&1
if not errorlevel 1 ( set "PY=python" & goto :found_python )

python3 --version >nul 2>&1
if not errorlevel 1 ( set "PY=python3" & goto :found_python )

echo [ERROR] Python not found.
echo(
echo  QUICK FIX: run  setup_python.bat  (in this folder) to auto-download
echo  Python 3.12 from python.org/ftp/python — it handles everything for you.
echo(
echo  Or install manually from:
echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
echo(
echo  NOTE: Do NOT install Python from the Microsoft Store — it lacks
echo  proper venv support and will cause build errors.
echo(
echo  Tick "Add Python 3.12 to PATH" during install, then re-run build.bat.
echo(
goto :die

:found_python
echo [OK] Python found:  %PY%
for /f "tokens=*" %%V in ('%PY% -c "import sys; print(sys.version_info.major)"') do set PY_MAJOR=%%V
for /f "tokens=*" %%V in ('%PY% -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%V
echo [OK] Version: %PY_MAJOR%.%PY_MINOR%
echo(

REM ---------------------------------------------------------------
REM  STEP 2 -- Block Python 3.13+  (no PyQt5 wheel above 3.12)
REM ---------------------------------------------------------------
if %PY_MAJOR% EQU 3 if %PY_MINOR% GEQ 13 (
    echo [ERROR] Python %PY_MAJOR%.%PY_MINOR% is not supported.
    echo(
    echo  PyQt5 wheels only exist for Python 3.8 to 3.12.
    echo  Fix: install Python 3.12 and re-run.
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo(
    goto :die
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo [ERROR] Python 3.10 or higher required. You have %PY_MAJOR%.%PY_MINOR%.
    echo(
    goto :die
)
echo [OK] Python %PY_MAJOR%.%PY_MINOR% is compatible.
echo(

REM ---------------------------------------------------------------
REM  STEP 3 -- Virtual environment
REM ---------------------------------------------------------------
if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating existing venv...
    call "venv\Scripts\activate.bat"
    echo [OK] venv activated.
) else (
    echo [INFO] Creating venv...
    %PY% -m venv venv
    if errorlevel 1 (
        echo [WARN] Could not create venv -- using system Python.
    ) else (
        call "venv\Scripts\activate.bat"
        echo [OK] venv created and activated.
    )
)
echo(

REM ---------------------------------------------------------------
REM  STEP 4 -- Upgrade pip
REM ---------------------------------------------------------------
echo [....] Upgrading pip...
%PY% -m pip install --upgrade pip --quiet --disable-pip-version-check
echo [OK] pip ready.
echo(

REM ---------------------------------------------------------------
REM  STEP 5 -- PyQt5 + qtpy
REM ---------------------------------------------------------------
echo [....] Installing PyQt5 + qtpy...
%PY% -m pip install "PyQt5>=5.15.0,<6.0" "qtpy>=2.4.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyQt5/qtpy install failed.
    echo(
    echo  PyQt5 wheels only exist for Python 3.8-3.12.
    echo  You are on Python %PY_MAJOR%.%PY_MINOR%.
    echo  Install Python 3.12: https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo(
    goto :die
)
echo [OK] PyQt5 + qtpy installed.
echo(

REM ---------------------------------------------------------------
REM  STEP 6 -- numpy, watchdog, requests, typing-extensions
REM ---------------------------------------------------------------
echo [....] Installing numpy, watchdog, requests, typing-extensions...
%PY% -m pip install "numpy>=1.21.0" "watchdog>=2.0.0" "requests>=2.28.0" "typing_extensions>=4.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Failed to install numpy/watchdog/requests/typing_extensions.
    goto :die
)
echo [OK] numpy, watchdog, requests, typing_extensions installed.
echo(

REM ---------------------------------------------------------------
REM  STEP 7 -- moderngl (binary-only) or PyOpenGL fallback
REM ---------------------------------------------------------------
echo [....] Trying moderngl...
set MODERNGL_OK=0
%PY% -m pip install "moderngl>=5.8.0" --only-binary :all: --quiet --disable-pip-version-check >nul 2>&1
if not errorlevel 1 (
    set MODERNGL_OK=1
    echo [OK] moderngl installed.
) else (
    echo [INFO] No moderngl wheel -- using PyOpenGL fallback.
    %PY% -m pip install "PyOpenGL>=3.1.0" --quiet --disable-pip-version-check >nul 2>&1
    echo [OK] PyOpenGL installed.
)
echo(

REM ---------------------------------------------------------------
REM  STEP 8 -- PyInstaller
REM ---------------------------------------------------------------
echo [....] Installing PyInstaller...
%PY% -m pip install "pyinstaller>=5.13.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed.
    goto :die
)
echo [OK] PyInstaller installed.
echo(

REM ---------------------------------------------------------------
REM  STEP 9 -- flask/werkzeug (optional IPC dependency)
REM ---------------------------------------------------------------
%PY% -m pip install flask werkzeug jinja2 --quiet --disable-pip-version-check >nul 2>&1

REM ---------------------------------------------------------------
REM  STEP 10 -- Icon
REM ---------------------------------------------------------------
echo [....] Checking icon...
if exist "assets\icons\gmodular.ico" (
    echo [OK] Icon present.
) else (
    %PY% tools\generate_icon.py >nul 2>&1
    if exist "assets\icons\gmodular.ico" (
        echo [OK] Icon generated.
    ) else (
        echo [INFO] No icon -- EXE will use default Windows icon.
    )
)
echo(

REM ---------------------------------------------------------------
REM  STEP 11 -- Self-test: verify gmodular imports cleanly
REM
REM  Uses a separate .py file written to the CURRENT directory
REM  (not %TEMP%) to avoid path-with-spaces issues on Windows.
REM  The file is deleted immediately after use.
REM ---------------------------------------------------------------
echo [....] GModular import self-test...

REM Write the preflight checker to the current directory (no spaces in path)
echo import pathlib, sys > _gm_check.py
echo p = pathlib.Path("gmodular/core/module_state.py") >> _gm_check.py
echo if not p.exists(): >> _gm_check.py
echo     print("ERROR: gmodular\\core\\module_state.py not found.") >> _gm_check.py
echo     print("Make sure you run build.bat from the GModular root folder.") >> _gm_check.py
echo     sys.exit(1) >> _gm_check.py
echo txt = p.read_text(encoding="utf-8", errors="replace") >> _gm_check.py
echo if "dmodular" in txt: >> _gm_check.py
echo     print("ERROR: gmodular\\core\\module_state.py has a bad import (dmodular).") >> _gm_check.py
echo     print("Your local file is out of date. Re-download from GitHub:") >> _gm_check.py
echo     print("  https://github.com/CrispyW0nton/GModular/archive/refs/heads/main.zip") >> _gm_check.py
echo     print("Or restore with git: git checkout origin/main -- gmodular/core/module_state.py") >> _gm_check.py
echo     sys.exit(1) >> _gm_check.py
echo elif "from .events import" not in txt: >> _gm_check.py
echo     print("ERROR: Unexpected import in module_state.py. Re-download from GitHub.") >> _gm_check.py
echo     sys.exit(1) >> _gm_check.py
echo print("preflight OK") >> _gm_check.py

%PY% _gm_check.py
set PREFLIGHT_RC=%ERRORLEVEL%
del _gm_check.py >nul 2>&1

if %PREFLIGHT_RC% NEQ 0 goto :die

REM Main import test
%PY% -c "from gmodular.formats.gff_types import GITData; from gmodular.core.module_state import ModuleState; from gmodular.gui.viewport_camera import OrbitCamera; from gmodular.gui.viewport_shaders import ALL_SHADERS; from gmodular.gui.viewport_renderer import _EGLRenderer; from gmodular.formats.mdl_writer import MDLWriter, NODE_EMITTER, NODE_DANGLY; import gmodular; print('[OK] GModular', gmodular.__version__, 'imports clean')"
if errorlevel 1 (
    echo(
    echo [ERROR] GModular import failed -- see traceback above.
    echo(
    echo  Fixes:
    echo   1. dmodular error: re-download from https://github.com/CrispyW0nton/GModular
    echo   2. No module qtpy/PyQt5: run  %PY% -m pip install PyQt5 qtpy
    echo   3. No module moderngl/numpy: run  %PY% -m pip install moderngl numpy
    echo   4. Other error: open a command prompt, cd to this folder, run:
    echo        %PY% -c "import gmodular"   to see the full traceback.
    echo(
    goto :die
)
echo(

REM ---------------------------------------------------------------
REM  STEP 11b -- GhostRigger self-test (non-fatal)
REM ---------------------------------------------------------------
echo [....] GhostRigger import self-test...
%PY% -c "import sys, os; sys.path.insert(0, os.path.join(os.getcwd(),'ghostrigger')); from ghostrigger.core.blueprint_state import BlueprintRegistry, Blueprint; from ghostrigger.ipc.server import PORT; print('GhostRigger OK, port', PORT)"
if errorlevel 1 (
    echo [WARN] GhostRigger import failed (non-fatal, build continues).
) else (
    echo [OK] GhostRigger self-test passed.
)
echo(

REM ---------------------------------------------------------------
REM  STEP 11c -- GhostScripter self-test (non-fatal)
REM ---------------------------------------------------------------
echo [....] GhostScripter import self-test...
%PY% -c "import sys, os; sys.path.insert(0, os.path.join(os.getcwd(),'ghostscripter')); from ghostscripter.core.script_state import ScriptRegistry, NWScriptCompiler; from ghostscripter.ipc.server import PORT; print('GhostScripter OK, port', PORT)"
if errorlevel 1 (
    echo [WARN] GhostScripter import failed (non-fatal, build continues).
) else (
    echo [OK] GhostScripter self-test passed.
)
echo(

REM ---------------------------------------------------------------
REM  STEP 12 -- Clean old build artifacts
REM ---------------------------------------------------------------
echo [....] Cleaning old build...
if exist "dist\GModular.exe" del /f /q "dist\GModular.exe" >nul 2>&1
if exist "dist\GModular"     rmdir /s /q "dist\GModular"   >nul 2>&1
if exist "build\GModular"    rmdir /s /q "build\GModular"  >nul 2>&1
echo [OK] Cleaned.
echo(

REM ---------------------------------------------------------------
REM  STEP 13 -- BUILD
REM ---------------------------------------------------------------
echo ============================================================
echo  Building GModular.exe ...  (this takes 1-3 minutes)
echo ============================================================
echo(

%PY% -m PyInstaller GModular.spec --clean --noconfirm

if errorlevel 1 (
    echo(
    echo ============================================================
    echo  [ERROR]  BUILD FAILED
    echo ============================================================
    echo(
    echo  Common fixes:
    echo(
    echo  1. Wrong Python version -- use Python 3.12
    echo       https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo(
    echo  2. Antivirus blocked the output -- add this folder to exclusions.
    echo(
    echo  3. Permission error -- right-click build.bat and "Run as Administrator".
    echo(
    echo  4. See full PyInstaller log above for the specific error.
    echo     To re-run with verbose output:
    echo       %PY% -m PyInstaller GModular.spec --clean --log-level DEBUG
    echo(
    goto :die
)

REM ---------------------------------------------------------------
REM  STEP 14 -- Validate exe + report size
REM ---------------------------------------------------------------
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe not found after build -- something went wrong.
    goto :die
)
for %%F in ("dist\GModular.exe") do (
    set /a SIZE_MB=%%~zF / 1048576
    echo [OK] dist\GModular.exe  --  !SIZE_MB! MB
)
echo(

REM ---------------------------------------------------------------
REM  DONE
REM ---------------------------------------------------------------
echo ============================================================
echo  BUILD COMPLETE!
echo ============================================================
echo(
echo  File:   dist\GModular.exe
echo(
echo  HOW TO RUN:
echo    Double-click dist\GModular.exe   (no install needed)
echo(
echo  FIRST TIME:
echo    Tools ^> Set Game Directory
echo    Point to your KotOR folder  (the folder with chitin.key)
echo    Click "Load Assets"
echo(
if !MODERNGL_OK!==0 (
    echo  NOTE: 3D viewport is using PyOpenGL fallback.
    echo  For full moderngl acceleration, install Visual C++ Build Tools
    echo  and re-run build.bat.
    echo(
)
echo ============================================================
echo(
echo  Press any key to close this window.
pause >nul
goto :eof

REM ---------------------------------------------------------------
REM  :die  -- always show a pause before exiting with error
REM  This label is used by every error path so the window NEVER
REM  closes without giving you a chance to read the message.
REM ---------------------------------------------------------------
:die
echo(
echo ============================================================
echo  BUILD STOPPED.  Read the [ERROR] message above.
echo ============================================================
echo(
echo  Press any key to close this window.
pause >nul
exit /b 1
