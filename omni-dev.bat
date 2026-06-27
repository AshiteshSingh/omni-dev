@echo off
setlocal
cd /d "%~dp0"

REM Activate venv if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo WARNING: venv not found. Using system Python.
)

echo Starting Omni-Dev...
python omni_dev.py %*
endlocal
