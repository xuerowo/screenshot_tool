@echo off
cd /d "%~dp0"
python main.py
if errorlevel 1 (
  echo Failed to start. Make sure Python and deps are installed:
  echo   pip install -r requirements.txt
  pause
)
