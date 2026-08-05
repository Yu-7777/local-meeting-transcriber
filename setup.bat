@echo off
rem NOTE: keep this file ASCII-only (cmd reads .bat with OEM codepage 932).
rem Japanese messages belong in the Python code, not here.
setlocal enabledelayedexpansion
chcp 65001 > nul
cd /d "%~dp0"

rem Python 3.12 is what this tool is tested against. The official python.org
rem build is signed by the Python Software Foundation, which matters: Smart App
rem Control blocks unsigned interpreters (uv's bundled Python is blocked).
set "PY_VERSION=3.12.10"
set "PY_URL=https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-amd64.exe"
set "PY_LOCAL=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

echo ============================================================
echo   Local meeting recorder / transcriber - setup
echo ============================================================
echo.

echo [1/4] Looking for Python...
call :find_python
if defined PYEXE goto :have_python

echo   Not found. Downloading the official installer (about 25 MB)...
set "PY_SETUP=%TEMP%\python-%PY_VERSION%-amd64.exe"
curl.exe -L --fail -o "%PY_SETUP%" "%PY_URL%"
if errorlevel 1 goto :fail_download

echo   Installing Python %PY_VERSION% for the current user...
echo   (no administrator rights needed, this takes a minute)
rem InstallAllUsers=0 keeps it per-user so no UAC prompt appears.
"%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%PY_SETUP%" > nul 2>&1

rem PATH changes do not reach this already-running shell, so look at the
rem known per-user install location directly.
call :find_python
if not defined PYEXE goto :fail_install

:have_python
echo   Using: !PYEXE!
"!PYEXE!" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)"
if errorlevel 1 goto :fail_version

echo.
if not exist ".venv\" (
    echo [2/4] Creating virtual environment...
    "!PYEXE!" -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo [2/4] Virtual environment already exists. Skipping.
)

echo.
echo [3/4] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

rem Only the default model is fetched here. The higher accuracy model
rem (large-v3, 2.9 GB) is downloaded on demand the first time it is picked,
rem so first-time setup stays at about 1.6 GB instead of 4.6 GB.
echo.
echo [4/4] Downloading the speech model (about 1.6 GB, this takes a while)...
".venv\Scripts\python.exe" "download_models.py" large-v3-turbo --diarization
if errorlevel 1 (
    echo.
    echo First attempt failed. This is often a temporary block by
    echo Smart App Control. Retrying once...
    echo.
    ".venv\Scripts\python.exe" "download_models.py" large-v3-turbo --diarization
    if errorlevel 1 goto :fail
)

echo.
echo ============================================================
echo   Setup complete
echo ============================================================
echo.
echo   Double-click gui.bat to record and transcribe.
echo.
echo   Command line:
echo     .venv\Scripts\python.exe check_devices.py
echo     record.bat
echo     .venv\Scripts\python.exe transcribe.py
echo.
pause
exit /b 0


rem ---------------------------------------------------------------- helpers

rem Sets PYEXE to a usable interpreter, or leaves it empty.
rem Order matters: the copy we install ourselves comes first, then the py
rem launcher, then PATH. Bare "python" is checked last because on a machine
rem without Python it is a Microsoft Store stub that opens the Store instead.
:find_python
set "PYEXE="
if exist "%PY_LOCAL%" (
    set "PYEXE=%PY_LOCAL%"
    goto :eof
)
py -3 -c "import sys" > nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)"') do set "PYEXE=%%i"
    goto :eof
)
python -c "import sys" > nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set "PYEXE=%%i"
)
goto :eof


:fail_download
echo.
echo *** Could not download the Python installer. ***
echo.
echo Check your network connection, or install Python %PY_VERSION% manually:
echo   %PY_URL%
echo Then run setup.bat again.
pause
exit /b 1

:fail_install
echo.
echo *** Python was installed but could not be found. ***
echo.
echo Please close this window, open a new one, and run setup.bat again.
echo A new window picks up the updated PATH.
pause
exit /b 1

:fail_version
echo.
echo *** Unsupported Python version. ***
echo.
"!PYEXE!" --version
echo This tool needs Python 3.10 - 3.13. Install %PY_VERSION% from:
echo   %PY_URL%
pause
exit /b 1

:fail
echo.
echo *** Setup failed. Please check the error above. ***
echo.
echo If you see "blocked by an application control policy",
echo it is Smart App Control. Simply run setup.bat again -
echo it usually succeeds on the second try.
echo See README.md "note 8" for details.
pause
exit /b 1
