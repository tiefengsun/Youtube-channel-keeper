@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :failed
)
if not exist ".venv\installed.marker" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :failed
    echo installed>".venv\installed.marker"
)
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start-background.ps1"
if errorlevel 1 goto :failed
exit /b 0
:failed
echo.
echo Startup failed. Please check the messages above and README.md.
pause
exit /b 1
