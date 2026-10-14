@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First run: setting up Python packages, this takes a minute...
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv || (echo Python 3 was not found. Install Python 3.12 from python.org, then run this again. & pause & exit /b 1)
  .venv\Scripts\python.exe -m pip install -r requirements.txt || (pause & exit /b 1)
)
if /i "%1"=="debug" (
  .venv\Scripts\python.exe echo_overlay.py
  pause
) else (
  start "" .venv\Scripts\pythonw.exe echo_overlay.py
)
