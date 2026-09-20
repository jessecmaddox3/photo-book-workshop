@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py %*
) else (
  python launch.py %*
)
if errorlevel 1 (
  echo Install Python 3.12 or newer from https://www.python.org/downloads/ if Python was not found.
  pause
  exit /b 1
)
