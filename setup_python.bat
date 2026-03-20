@echo off
REM ============================================================
REM  setup_python.bat  —  GModular Python 3.12 installer helper
REM  Automatically downloads and installs Python 3.12 from the
REM  official python.org/ftp mirror (NOT the Microsoft Store).
REM  Works on Windows 10/11.  Run this ONCE, then run build.bat.
REM ============================================================
REM  WARNING: Do NOT install Python from the Microsoft Store.
REM  The Microsoft Store version is sandboxed, does not support
REM  virtual environments properly, and may cause pip/venv errors.
REM  Always use the official installer from python.org/ftp/python.
REM ============================================================

setlocal enabledelayedexpansion

echo.
echo  *** GModular — Python 3.12 Setup ***
echo.
echo  This script installs Python 3.12 from python.org/ftp/python.
echo  IMPORTANT: Avoid the Microsoft Store Python — it lacks proper
echo  venv support and will cause build errors.
echo.

REM --- Check if Python 3.12 is already installed -----------------------
echo  Checking for existing Python 3.12 installation...
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    echo.
    echo  [OK] Python 3.12 is already installed:
    py -3.12 --version
    echo.
    echo  You are ready to build.  Run:  build.bat
    echo.
    pause
    exit /b 0
)

echo  Python 3.12 not found — downloading installer from python.org...
echo.

REM --- Check for curl availability -------------------------------------
curl --version >nul 2>&1
if errorlevel 1 (
    echo  [WARN] curl is not available on this machine.
    echo.
    echo  Please download Python 3.12 manually from:
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    echo  When running the installer:
    echo    - Tick "Add Python 3.12 to PATH"  (important!)
    echo    - Choose "Install Now"
    echo.
    echo  After installation, run build.bat to build GModular.
    echo.
    start https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    pause
    exit /b 1
)

REM --- Download Python 3.12 installer via curl -------------------------
set "INSTALLER=%TEMP%\python-3.12.9-amd64.exe"
set "DL_URL=https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe"

echo  Downloading:  %DL_URL%
echo  Saving to:    %INSTALLER%
echo.

curl -L --progress-bar -o "%INSTALLER%" "%DL_URL%"
if errorlevel 1 (
    echo.
    echo  [ERROR] Download failed.
    echo  Check your internet connection, then try again, or download manually:
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    pause
    exit /b 1
)

echo.
echo  Download complete.  Launching installer...
echo.
echo  IMPORTANT: In the installer, tick "Add Python 3.12 to PATH" before
echo  clicking Install Now.  This makes "py -3.12" available globally and
echo  is required for build.bat to find Python automatically.
echo.
pause

REM --- Run the installer -----------------------------------------------
"%INSTALLER%" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1
if errorlevel 1 (
    echo.
    echo  [ERROR] Installer returned an error.  Try running as Administrator.
    echo.
    pause
    exit /b 1
)

REM --- Clean up installer file -----------------------------------------
del /q "%INSTALLER%" 2>nul

REM --- Verify installation ---------------------------------------------
echo.
echo  Verifying installation...
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN] py -3.12 not found after install.  You may need to:
    echo   1. Close this window and reopen a new Command Prompt
    echo   2. Then run:  build.bat
    echo.
    echo  If py -3.12 still fails, add Python to PATH manually via:
    echo    Settings ^> System ^> About ^> Advanced system settings ^> Environment Variables
    echo.
) else (
    echo.
    echo  [OK] Python 3.12 installed successfully:
    py -3.12 --version
)

echo.
echo  ============================================================
echo   Next step:  run  build.bat  to compile GModular.exe
echo  ============================================================
echo.
pause
exit /b 0
