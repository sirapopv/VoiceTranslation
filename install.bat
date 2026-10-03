@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ==============================================
echo   VoiceTranslation - Install
echo ==============================================

where py >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found. Installing Python 3.11 with winget ...
  winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python installed. Please CLOSE this window and run install.bat again.
  if not defined CI pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Creating virtual environment ...
  py -3.11 -m venv .venv || py -3 -m venv .venv
  if errorlevel 1 goto :fail
)
set "PY=.venv\Scripts\python.exe"

echo [2/5] Upgrading pip ...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo [3/5] Installing PyTorch ...
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo       No NVIDIA GPU driver found - installing CPU version.
  "%PY%" -m pip install torch
) else (
  echo       NVIDIA GPU found - installing CUDA version.
  "%PY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu128
)
if errorlevel 1 goto :fail

echo [4/5] Installing VoiceTranslation packages ...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
"%PY%" -m spacy download en_core_web_sm

echo [5/5] Downloading the Kokoro voice model (about 330 MB, first time only) ...
"%PY%" -c "from voicetrans.tts import KokoroTTS; t=KokoroTTS(); t.synthesize('Hello!'); print('Kokoro OK on', t.device_name)"
if errorlevel 1 goto :fail

if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo ==============================================
echo   Done! Double-click start.bat to open the app.
echo ==============================================
if not defined CI pause
exit /b 0

:fail
echo.
echo [X] Installation failed. Scroll up to see the error.
if not defined CI pause
exit /b 1
