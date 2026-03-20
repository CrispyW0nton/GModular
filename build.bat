@echo off
cd /d "%~dp0"

echo ============================================================
echo  GModular Build Script  v2.13
echo ============================================================
echo.
echo  Working directory: %CD%
echo.

REM -- Keep window open no matter what --
REM    cmd /k re-runs this script as a child process; /k means the
REM    cmd window stays open after the child exits, always.
if "%1"=="CHILD" goto :main
cmd /k "%~f0" CHILD
exit /b 0

:main

REM ---------------------------------------------------------------
REM  Find Python
REM ---------------------------------------------------------------
set PY=
py -3.12 --version >nul 2>&1 && set PY=py -3.12
if not defined PY (
    py -3.11 --version >nul 2>&1 && set PY=py -3.11
)
if not defined PY (
    py -3.10 --version >nul 2>&1 && set PY=py -3.10
)
if not defined PY (
    python --version >nul 2>&1 && set PY=python
)
if not defined PY (
    python3 --version >nul 2>&1 && set PY=python3
)
if not defined PY (
    echo.
    echo [ERROR] Python not found.
    echo.
    echo  Run setup_python.bat to install Python 3.12 automatically.
    echo  Or download from: https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo  Tick "Add Python to PATH" during install.
    echo.
    goto :end
)
echo [OK] Python: %PY%
%PY% --version
echo.

REM ---------------------------------------------------------------
REM  Version check
REM ---------------------------------------------------------------
%PY% -c "import sys; v=sys.version_info; exit(0 if 10<=v.minor<=12 and v.major==3 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10, 3.11 or 3.12 required.
    echo  Your version:
    %PY% --version
    echo  Run setup_python.bat to install Python 3.12.
    echo.
    goto :end
)
echo [OK] Python version OK.
echo.

REM ---------------------------------------------------------------
REM  Create venv
REM ---------------------------------------------------------------
if not exist venv\Scripts\python.exe (
    echo [....] Creating venv...
    %PY% -m venv venv
    if errorlevel 1 (
        echo [ERROR] venv creation failed.
        echo  Try: right-click build.bat and Run as Administrator
        echo.
        goto :end
    )
    echo [OK] venv created.
) else (
    echo [OK] venv already exists.
)
echo.

REM ---------------------------------------------------------------
REM  Block Python 3.13+  (no PyQt5 wheel)
REM ---------------------------------------------------------------
for /f "tokens=*" %%M in ('%PY% -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%M
if not defined PY_MINOR set PY_MINOR=0
if %PY_MINOR% GEQ 13 (
    echo [ERROR] Python 3.13+ is not supported.
    echo  PyQt5 has no wheel for Python 3.13+.
    echo  Run setup_python.bat to install Python 3.12 automatically.
    echo.
    exit /b 1
)

REM -- Use venv python for everything from here --
set VP="%CD%\venv\Scripts\python.exe"
echo [OK] Using: %VP%
echo.

REM ---------------------------------------------------------------
REM  Install packages
REM ---------------------------------------------------------------
echo [....] Installing packages (this takes 1-3 minutes on first run)...
%VP% -m pip install --upgrade pip --quiet --disable-pip-version-check
%VP% -m pip install "PyQt5>=5.15.0,<6.0" "qtpy>=2.4.0" "numpy>=1.21.0" "watchdog>=2.0.0" "requests>=2.28.0" "typing_extensions>=4.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Package install failed.
    echo  Check your internet connection and try again.
    echo.
    goto :end
)
%VP% -m pip install "moderngl>=5.8.0" --only-binary :all: --quiet --disable-pip-version-check >nul 2>&1
if errorlevel 1 (
    %VP% -m pip install "PyOpenGL>=3.1.0" --quiet --disable-pip-version-check >nul 2>&1
)
%VP% -m pip install "pyinstaller>=5.13.0" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed.
    echo.
    goto :end
)
%VP% -m pip install flask werkzeug jinja2 --quiet --disable-pip-version-check >nul 2>&1
echo [OK] All packages installed.
echo.

REM ---------------------------------------------------------------
REM  Check required source files
REM ---------------------------------------------------------------
if not exist "gmodular\core\events.py" (
    echo [ERROR] gmodular\core\events.py is missing.
    echo  Re-download the ZIP from:
    echo    https://github.com/CrispyW0nton/GModular/archive/refs/heads/main.zip
    echo.
    goto :end
)
echo [OK] Source files OK.
echo.

REM ---------------------------------------------------------------
REM  Quick import test
REM ---------------------------------------------------------------
echo [....] Testing imports...
%VP% -c "import gmodular; from gmodular.core.events import get_event_bus; print('OK:', gmodular.__version__)"
if errorlevel 1 (
    echo [ERROR] Import test failed - see error above.
    echo.
    goto :end
)
echo.

REM ---------------------------------------------------------------
REM  Build
REM ---------------------------------------------------------------
echo ============================================================
echo  Building GModular.exe (1-3 minutes)...
echo ============================================================
echo.

if exist "dist\GModular.exe" del /f /q "dist\GModular.exe" >nul 2>&1
if exist "build\GModular" rmdir /s /q "build\GModular" >nul 2>&1

%VP% -m PyInstaller GModular.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed - see errors above.
    echo.
    echo  Common fixes:
    echo    1. Antivirus blocking output - add this folder to exclusions
    echo    2. Right-click build.bat and Run as Administrator
    echo.
    goto :end
)

REM ---------------------------------------------------------------
REM  Done
REM ---------------------------------------------------------------
if not exist "dist\GModular.exe" (
    echo [ERROR] dist\GModular.exe not found after build.
    echo.
    goto :end
)

echo.
echo ============================================================
echo  BUILD COMPLETE - dist\GModular.exe is ready
echo ============================================================
echo.
echo  Double-click dist\GModular.exe to run.
echo  First time: Tools ^> Set Game Directory ^> point to KotOR folder.
echo.

:end
echo  (window will stay open - press Ctrl+C or close when done)
