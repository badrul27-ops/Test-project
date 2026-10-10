@echo off
title AutoEditor setup
cd /d "%~dp0"
echo ============================================
echo   AutoEditor - one-time setup
echo ============================================
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version >nul 2>nul
if not %errorlevel%==0 (
  echo.
  echo Python was not found. Install Python 3.11 or 3.12 from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add python.exe to PATH" in the installer, then run setup.bat again.
  pause
  exit /b 1
)
%PY% --version
if not exist venv (
  echo Creating virtual environment...
  %PY% -m venv venv || (echo Failed to create venv & pause & exit /b 1)
)
echo Installing packages (a few minutes the first time)...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt || (echo Package install failed - see messages above & pause & exit /b 1)
echo.
echo Checking installation...
venv\Scripts\python.exe -c "import faster_whisper, PySide6, cv2; from autoedit.util import ffmpeg_exe; print('FFmpeg:', ffmpeg_exe()); print('OK')" || (pause & exit /b 1)
echo.
echo Setup complete. Double-click run.bat to start AutoEditor.
pause
