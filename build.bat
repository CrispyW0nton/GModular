@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

echo ============================================================
echo  GModular Build Script  v2.0.8
echo  KotOR Module Editor  ^|  Produces: dist\GModular.exe
echo ============================================================
echo(

cd /d "%~dp0"
echo Working directory: %CD%
echo(

REM ---------------------------------------------------------------
REM  STEP 1 -- Find Python  (py launcher  OR  python  OR  python3)
REM  Same logic used by GhostRigger + GhostScripter
REM ---------------------------------------------------------------
set "PY="

REM Try py launcher with 3.12 first (best)
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.12"
    goto :found_python
)

REM Try py launcher with 3.11
py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.11"
    goto :found_python
)

REM Try py launcher with 3.10
py -3.10 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.10"
    goto :found_python
)

REM Try bare py launcher (picks highest installed)
py --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py"
    goto :found_python
)

REM Try python on PATH
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :found_python
)

REM Try python3 on PATH
python3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=python3"
    goto :found_python
)

REM Nothing found
echo [ERROR] Python not found.
echo(
echo  Install Python 3.12 from:
echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
echo(
echo  Tick "Add Python 3.12 to PATH" during install, then re-run build.bat.
echo(
echo  (Or run setup_python.bat to download and install automatically)
echo(
pause
exit /b 1

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
    echo  Fix: run  py -3.12  or install Python 3.12:
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo(
    pause
    exit /b 1
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo [ERROR] Python 3.10+ required. You have %PY_MAJOR%.%PY_MINOR%.
    echo(
    pause
    exit /b 1
)
echo [OK] Python %PY_MAJOR%.%PY_MINOR% is compatible.
echo(

REM ---------------------------------------------------------------
REM  STEP 3 -- Virtual environment (use if present, else create)
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
REM  STEP 4 -- Upgrade pip  (silent)
REM ---------------------------------------------------------------
echo [....] Upgrading pip...
%PY% -m pip install --upgrade pip --quiet --disable-pip-version-check
echo [OK] pip ready.
echo(

REM ---------------------------------------------------------------
REM  STEP 5 -- PyQt5
REM ---------------------------------------------------------------
echo [....] Installing PyQt5...
%PY% -m pip install "PyQt5>=5.15.0,<6.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyQt5 install failed.
    echo(
    echo  PyQt5 wheels only exist for Python 3.8-3.12.
    echo  You are on Python %PY_MAJOR%.%PY_MINOR%.
    echo  Install Python 3.12: https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo(
    pause
    exit /b 1
)
echo [OK] PyQt5 installed.
echo(

REM ---------------------------------------------------------------
REM  STEP 6 -- numpy, watchdog, requests
REM ---------------------------------------------------------------
echo [....] Installing numpy, watchdog, requests...
%PY% -m pip install "numpy>=1.21.0" "watchdog>=2.0.0" "requests>=2.28.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Failed to install numpy/watchdog/requests.
    pause
    exit /b 1
)
echo [OK] numpy, watchdog, requests installed.
echo(

REM ---------------------------------------------------------------
REM  STEP 7 -- moderngl  (binary-only)  or  PyOpenGL  fallback
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
    pause
    exit /b 1
)
echo [OK] PyInstaller installed.
echo(

REM ---------------------------------------------------------------
REM  STEP 9 -- flask/werkzeug  (optional IPC dependency)
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
REM  STEP 11 -- Quick self-test
REM ---------------------------------------------------------------
echo [....] GModular import self-test...
%PY% -c "from gmodular.formats.gff_types import GITData; from gmodular.core.module_state import ModuleState; from gmodular.gui.viewport_camera import OrbitCamera; from gmodular.gui.viewport_shaders import ALL_SHADERS; from gmodular.gui.viewport_renderer import _EGLRenderer; from gmodular.formats.mdl_writer import MDLWriter, NODE_EMITTER, NODE_DANGLY; print('GModular OK')"
if errorlevel 1 (
    echo [ERROR] GModular import failed -- fix the error shown above, then re-run.
    pause
    exit /b 1
)
echo [OK] GModular self-test passed.
echo(

REM ---------------------------------------------------------------
REM  STEP 11b -- GhostRigger self-test
REM ---------------------------------------------------------------
echo [....] GhostRigger import self-test...
%PY% -c "import sys, os; sys.path.insert(0, os.path.join(os.getcwd(),'ghostrigger')); from ghostrigger.core.blueprint_state import BlueprintRegistry, Blueprint; from ghostrigger.ipc.server import PORT; print('GhostRigger OK, port', PORT)"
if errorlevel 1 (
    echo [WARN] GhostRigger import failed (non-fatal).
) else (
    echo [OK] GhostRigger self-test passed.
)
echo(

REM ---------------------------------------------------------------
REM  STEP 11c -- GhostScripter self-test
REM ---------------------------------------------------------------
echo [....] GhostScripter import self-test...
%PY% -c "import sys, os; sys.path.insert(0, os.path.join(os.getcwd(),'ghostscripter')); from ghostscripter.core.script_state import ScriptRegistry, NWScriptCompiler; from ghostscripter.ipc.server import PORT; print('GhostScripter OK, port', PORT)"
if errorlevel 1 (
    echo [WARN] GhostScripter import failed (non-fatal).
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
echo  Building GModular.exe ...  (1-3 minutes)
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
    echo  2. Antivirus blocking the output -- temporarily exclude this folder.
    echo(
    echo  3. Permission error -- run cmd as Administrator.
    echo(
    echo  4. Run with debug output:
    echo       %PY% -m PyInstaller GModular.spec --debug all
    echo(
    pause
    exit /b 1
)

REM ---------------------------------------------------------------
REM  STEP 14 -- Validate exe exists + show size
REM ---------------------------------------------------------------
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe not found after build!
    pause
    exit /b 1
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
echo    Point to your KotOR folder  (contains chitin.key)
echo    Click "Load Assets"
echo(
if !MODERNGL_OK!==0 (
    echo  NOTE: 3D viewport using PyOpenGL fallback.
    echo  For full moderngl acceleration, install Visual C++ Build Tools
    echo  and re-run build.bat.
    echo(
)
echo ============================================================
pause
