@echo off
chcp 65001 >nul
set CURRENT_DIR=%~dp0
set VENV_PYTHONW=%CURRENT_DIR%..\AutoMemeDetector-main\.venv\Scripts\pythonw.exe

cd /d "%CURRENT_DIR%"

if exist "%VENV_PYTHONW%" (
    start "" "%VENV_PYTHONW%" "%CURRENT_DIR%gui.py"
) else (
    start "" pythonw "%CURRENT_DIR%gui.py"
)
exit
