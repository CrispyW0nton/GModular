@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

REM ============================================================
REM  GModular Build Script  v2.0.12
REM  KotOR Module Editor  |  Produces: dist\GModular.exe
REM
REM  This window will ALWAYS stay open (pause before any exit).
REM  Read any [ERROR] message carefully before closing.
REM ============================================================
echo ============================================================
echo  GModular Build Script  v2.0.12
echo  KotOR Module Editor  ^|  Produces: dist\GModular.exe
echo ============================================================
echo.

REM Change to the folder containing this .bat file
cd /d "%~dp0"
echo Working directory: %CD%
echo.

REM ---------------------------------------------------------------
REM  STEP 1 -- Find Python
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
echo.
echo  QUICK FIX: run setup_python.bat (in this folder) to auto-download
echo  Python 3.12 from python.org -- it handles everything for you.
echo.
echo  Or install manually:
echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
echo.
echo  WARNING: Do NOT use the Microsoft Store Python -- it breaks venv.
echo.
echo  Tick "Add Python 3.12 to PATH" during install, then re-run build.bat.
echo.
goto :die

:found_python
echo [OK] Python found:  %PY%
for /f "tokens=*" %%V in ('%PY% -c "import sys; print(sys.version_info.major)"') do set PY_MAJOR=%%V
for /f "tokens=*" %%V in ('%PY% -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%V
echo [OK] Version: %PY_MAJOR%.%PY_MINOR%
echo.

REM ---------------------------------------------------------------
REM  STEP 2 -- Block unsupported Python versions
REM ---------------------------------------------------------------
if "%PY_MAJOR%"=="" goto :bad_py_version
if "%PY_MINOR%"=="" goto :bad_py_version

if %PY_MAJOR% EQU 3 if %PY_MINOR% GEQ 13 (
    echo [ERROR] Python %PY_MAJOR%.%PY_MINOR% is not supported.
    echo.
    echo  PyQt5 wheels only exist for Python 3.8 to 3.12.
    echo  Run setup_python.bat to install Python 3.12 automatically.
    echo.
    goto :die
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo [ERROR] Python 3.10 or higher required. You have %PY_MAJOR%.%PY_MINOR%.
    echo.
    echo  Run setup_python.bat to install Python 3.12 automatically.
    echo.
    goto :die
)
goto :py_version_ok

:bad_py_version
echo [ERROR] Could not detect Python version.
echo.
goto :die

:py_version_ok
echo [OK] Python %PY_MAJOR%.%PY_MINOR% is compatible.
echo.

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
echo.

REM ---------------------------------------------------------------
REM  STEP 4 -- Upgrade pip
REM ---------------------------------------------------------------
echo [....] Upgrading pip...
%PY% -m pip install --upgrade pip --quiet --disable-pip-version-check
echo [OK] pip ready.
echo.

REM ---------------------------------------------------------------
REM  STEP 5 -- PyQt5 + qtpy
REM ---------------------------------------------------------------
echo [....] Installing PyQt5 + qtpy...
%PY% -m pip install "PyQt5>=5.15.0,<6.0" "qtpy>=2.4.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyQt5/qtpy install failed.
    echo.
    echo  PyQt5 wheels only exist for Python 3.8-3.12.
    echo  You are on Python %PY_MAJOR%.%PY_MINOR%.
    echo  Run setup_python.bat to get the correct Python version.
    echo.
    goto :die
)
echo [OK] PyQt5 + qtpy installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 6 -- numpy, watchdog, requests, typing-extensions
REM ---------------------------------------------------------------
echo [....] Installing numpy, watchdog, requests, typing-extensions...
%PY% -m pip install "numpy>=1.21.0" "watchdog>=2.0.0" "requests>=2.28.0" "typing_extensions>=4.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Failed to install numpy/watchdog/requests/typing_extensions.
    echo.
    goto :die
)
echo [OK] numpy, watchdog, requests, typing_extensions installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 7 -- moderngl (binary-only) or PyOpenGL fallback
REM ---------------------------------------------------------------
echo [....] Trying moderngl...
set "MODERNGL_OK=0"
%PY% -m pip install "moderngl>=5.8.0" --only-binary :all: --quiet --disable-pip-version-check >nul 2>&1
if not errorlevel 1 (
    set "MODERNGL_OK=1"
    echo [OK] moderngl installed.
) else (
    echo [INFO] No moderngl wheel -- using PyOpenGL fallback.
    %PY% -m pip install "PyOpenGL>=3.1.0" --quiet --disable-pip-version-check >nul 2>&1
    echo [OK] PyOpenGL installed.
)
echo.

REM ---------------------------------------------------------------
REM  STEP 8 -- PyInstaller
REM ---------------------------------------------------------------
echo [....] Installing PyInstaller...
%PY% -m pip install "pyinstaller>=5.13.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed.
    echo.
    goto :die
)
echo [OK] PyInstaller installed.
echo.

REM ---------------------------------------------------------------
REM  STEP 9 -- flask/werkzeug (optional)
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
echo.

REM ---------------------------------------------------------------
REM  STEP 11 -- Self-test
REM
REM  IMPORTANT: We do NOT generate a Python file with echo here
REM  because Windows echo strips double-quotes from strings,
REM  which breaks Python syntax.  Instead we use findstr to check
REM  the file content directly (no Python quoting needed).
REM ---------------------------------------------------------------
echo [....] GModular self-test...

REM Check that gmodular/core/module_state.py exists
if not exist "gmodular\core\module_state.py" (
    echo [ERROR] gmodular\core\module_state.py not found.
    echo.
    echo  Make sure you are running build.bat from the GModular root folder.
    echo  The folder must contain gmodular\, ghostrigger\, build.bat, etc.
    echo.
    goto :die
)

REM Check for the dmodular typo using findstr (no Python quoting needed)
findstr /i "dmodular" "gmodular\core\module_state.py" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] gmodular\core\module_state.py contains a bad import: dmodular
    echo.
    echo  Your local file is out of date.  Fix with one of these:
    echo.
    echo  Option A -- git:
    echo    git fetch origin main
    echo    git checkout origin/main -- gmodular/core/module_state.py
    echo.
    echo  Option B -- re-download the ZIP:
    echo    https://github.com/CrispyW0nton/GModular/archive/refs/heads/main.zip
    echo.
    goto :die
)

REM Run the import test
%PY% -c "import gmodular; from gmodular.core.module_state import ModuleState; from gmodular.formats.gff_types import GITData; print('[OK] GModular', gmodular.__version__, 'imports clean')"
if errorlevel 1 (
    echo.
    echo [ERROR] GModular import failed -- see traceback above.
    echo.
    echo  Common fixes:
    echo    qtpy or PyQt5 missing:  %PY% -m pip install PyQt5 qtpy
    echo    moderngl or numpy:      %PY% -m pip install moderngl numpy
    echo    other error:  open cmd, cd to this folder, run:
    echo                  %PY% -c "import gmodular"
    echo.
    goto :die
)
echo.

REM ---------------------------------------------------------------
REM  STEP 11b -- GhostRigger (non-fatal)
REM ---------------------------------------------------------------
echo [....] GhostRigger self-test...
%PY% -c "import sys,os; sys.path.insert(0,os.path.join(os.getcwd(),'ghostrigger')); from ghostrigger.core.blueprint_state import BlueprintRegistry; print('GhostRigger OK')"
if errorlevel 1 (
    echo [WARN] GhostRigger import failed (non-fatal, build continues).
) else (
    echo [OK] GhostRigger self-test passed.
)
echo.

REM ---------------------------------------------------------------
REM  STEP 11c -- GhostScripter (non-fatal)
REM ---------------------------------------------------------------
echo [....] GhostScripter self-test...
%PY% -c "import sys,os; sys.path.insert(0,os.path.join(os.getcwd(),'ghostscripter')); from ghostscripter.core.script_state import ScriptRegistry; print('GhostScripter OK')"
if errorlevel 1 (
    echo [WARN] GhostScripter import failed (non-fatal, build continues).
) else (
    echo [OK] GhostScripter self-test passed.
)
echo.

REM ---------------------------------------------------------------
REM  STEP 12 -- Clean old artifacts
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
echo  Building GModular.exe ...  (this takes 1-3 minutes)
echo ============================================================
echo.

%PY% -m PyInstaller GModular.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [ERROR]  BUILD FAILED
    echo ============================================================
    echo.
    echo  Common fixes:
    echo    1. Wrong Python version -- run setup_python.bat to get 3.12
    echo    2. Antivirus blocked output -- add this folder to exclusions
    echo    3. Permission error -- right-click build.bat, Run as Administrator
    echo    4. Verbose log:  %PY% -m PyInstaller GModular.spec --clean --log-level DEBUG
    echo.
    goto :die
)

REM ---------------------------------------------------------------
REM  STEP 14 -- Validate + report
REM ---------------------------------------------------------------
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe not found after build.
    echo.
    goto :die
)

for %%F in ("dist\GModular.exe") do (
    set /a "SIZE_MB=%%~zF / 1048576"
    echo [OK] dist\GModular.exe  --  !SIZE_MB! MB
)
echo.

REM ---------------------------------------------------------------
REM  DONE
REM ---------------------------------------------------------------
echo ============================================================
echo  BUILD COMPLETE!
echo ============================================================
echo.
echo  File:   dist\GModular.exe
echo.
echo  HOW TO RUN:
echo    Double-click dist\GModular.exe   (no install needed)
echo.
echo  FIRST TIME:
echo    Tools ^> Set Game Directory
echo    Point to your KotOR folder  (the folder with chitin.key)
echo    Click "Load Assets"
echo.
if "!MODERNGL_OK!"=="0" (
    echo  NOTE: 3D viewport is using PyOpenGL fallback.
    echo  For full moderngl, install Visual C++ Build Tools and re-run.
    echo.
)
echo ============================================================
echo.
echo  Press any key to close this window.
pause
goto :eof

REM ---------------------------------------------------------------
REM  :die  -- every error path lands here so window never auto-closes
REM ---------------------------------------------------------------
:die
echo.
echo ============================================================
echo  BUILD STOPPED.  Read the [ERROR] message above, then press
echo  any key to close this window.
echo ============================================================
echo.
pause
exit /b 1
