@echo off
rem NOTE: keep this file ASCII-only (cmd reads .bat with OEM codepage 932).
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Please run setup.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "record.py" %*

echo.
pause
