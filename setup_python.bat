@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

echo ============================================================
echo  GModular - Python 3.12 Setup Helper
echo  Downloads and runs the official Python 3.12.9 installer
echo  directly from python.org (NOT the Microsoft Store)
echo ============================================================
echo.

cd /d "%~dp0"

REM -- Check if Python 3.12 is already available --------------------------
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    echo [OK] Python 3.12 is already installed:
    py -3.12 --version
    echo.
    echo  You can now run:
    echo    py -3.12 -m venv venv
    echo    venv\Scripts\activate.bat
    echo    build.bat
    echo.
    pause
    exit /b 0
)

echo [INFO] Python 3.12 not found. Downloading installer...
echo.
echo  Source: https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
echo  (This is the OFFICIAL installer -- NOT the Microsoft Store version)
echo.

REM -- Check curl is available (built into Windows 10 1803+ and Windows 11) ------
curl --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] curl not found.
    echo.
    echo  Your Windows version may be too old for the built-in curl.
    echo  Please download Python 3.12 manually:
    echo.
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    echo  Run the downloaded file and tick "Add Python 3.12 to PATH".
    echo  Then run build.bat.
    echo.
    pause
    exit /b 1
)

REM -- Download Python 3.12.9 installer --------------------------------------
set INSTALLER=%TEMP%\python-3.12.9-amd64.exe
set DOWNLOAD_URL=https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe

echo [....] Downloading python-3.12.9-amd64.exe ...
echo        (approx 25 MB -- this may take a minute)
echo.

curl -L --progress-bar -o "!INSTALLER!" "!DOWNLOAD_URL!"

if errorlevel 1 (
    echo.
    echo [ERROR] Download failed.
    echo.
    echo  Possible causes:
    echo    - No internet connection
    echo    - Corporate firewall / proxy blocking python.org
    echo.
    echo  Manual download:
    echo    https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe
    echo.
    pause
    exit /b 1
)

if not exist "!INSTALLER!" (
    echo [ERROR] Downloaded file not found at !INSTALLER!
    pause
    exit /b 1
)

echo.
echo [OK] Download complete: !INSTALLER!
echo.

REM -- Run the installer ----------------------------------------------------
echo ============================================================
echo  INSTALLER IS ABOUT TO OPEN
echo ============================================================
echo.
echo  In the installer window:
echo.
echo    1. Tick BOTH checkboxes on the first screen:
echo         [x] Install launcher for all users (recommended)
echo         [x] Add Python 3.12 to PATH          ^<-- REQUIRED
echo.
echo    2. Click "Install Now"
echo.
echo    3. Wait for installation to finish, then close the window.
echo.
echo  !! DO NOT click "Get" or open the Microsoft Store !!
echo  !! This is the direct .exe installer -- it will    !!
echo  !! work even if the Store is blocked by policy.    !!
echo.
pause

"!INSTALLER!" /passive PrependPath=1 InstallAllUsers=0

if errorlevel 1 (
    echo.
    echo [WARN] Installer returned non-zero. It may have been cancelled,
    echo        or UAC was denied. Check if Python 3.12 was installed by
    echo        opening a new cmd window and typing:  py -3.12 --version
    echo.
) else (
    echo.
    echo [OK] Python 3.12 installer finished.
)

echo.
echo ============================================================
echo  NEXT STEPS
echo ============================================================
echo.
echo  1. Close this window.
echo  2. Open a NEW Command Prompt (important -- PATH must reload).
echo  3. Run:
echo       cd /d "%~dp0"
echo       py -3.12 -m venv venv
echo       venv\Scripts\activate.bat
echo       build.bat
echo.
echo ============================================================
pause
