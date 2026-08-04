@echo off
rem NOTE: keep this file ASCII-only (cmd reads .bat with OEM codepage 932).
chcp 65001 > nul
cd /d "%~dp0"

echo ============================================================
echo   Local meeting recorder / transcriber - setup
echo ============================================================
echo.

if not exist ".venv\" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo [1/3] Virtual environment already exists. Skipping.
)

echo.
echo [2/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo [3/3] Downloading speech models (about 4.6 GB, this takes a while)...
".venv\Scripts\python.exe" "download_models.py" --all
if errorlevel 1 (
    echo.
    echo First attempt failed. This is often a temporary block by
    echo Smart App Control. Retrying once...
    echo.
    ".venv\Scripts\python.exe" "download_models.py" --all
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
