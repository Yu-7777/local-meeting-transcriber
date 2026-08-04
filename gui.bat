@echo off
rem NOTE: keep this file ASCII-only.
rem cmd.exe reads .bat with the OEM codepage (932 here), so non-ASCII
rem characters corrupt parsing. Japanese messages belong in the Python code.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] venv not found. Please run setup.bat first.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "gui.py"
