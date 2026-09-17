@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please run start.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
    echo Update failed. Please check your network.
) else (
    echo Updated. Restart Channel Keeper to use the new version.
)
pause
