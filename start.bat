@echo off
cd /d "%~dp0"
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  echo Python was not found.
  echo Install Python 3.10 or later from https://www.python.org/downloads/
  echo and check "Add python.exe to PATH" in the installer, then run this file again.
  pause
  exit /b 1
)
%PY% -c "import hslog, hearthstone, anthropic" >nul 2>nul
if errorlevel 1 (
  echo Installing required packages. This takes a minute the first time...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Package installation failed. Check your internet connection and try again.
    pause
    exit /b 1
  )
)
echo Starting HS Review. If the browser does not open, go to http://127.0.0.1:8765/
echo Close this window to quit.
%PY% app.py
pause
