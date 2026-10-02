@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
echo Starting VoiceTranslation ... (the browser will open automatically)
echo Close this window to stop the app.
".venv\Scripts\python.exe" app.py
pause
