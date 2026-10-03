#!/bin/bash
# VoiceTranslation installer for macOS (Apple Silicon). Double-click in Finder.
set -euo pipefail
cd "$(dirname "$0")"

pause() { [ -n "${CI:-}" ] || read -r -p "Press Enter to close..." _; }
fail() { echo; echo "[X] Installation failed: $1"; pause; exit 1; }
trap 'fail "see the error above"' ERR

echo "=============================================="
echo "  VoiceTranslation - Install (macOS)"
echo "=============================================="

if [ "$(uname -m)" != "arm64" ]; then
  echo "[!] This Mac is not Apple Silicon (M1/M2/M3/M4)."
  echo "    PyTorch no longer supports Intel Macs, so the voice engine may not install."
fi

# uv manages Python for us: no Homebrew or python.org installer needed.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "[1/5] Installing uv (Python manager) ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "[2/5] Creating Python 3.11 environment ..."
  # --seed adds pip, which 'spacy download' needs.
  uv venv --seed --python 3.11 .venv
fi
PY=".venv/bin/python"

echo "[3/5] Installing PyTorch and VoiceTranslation packages ..."
uv pip install --python "$PY" torch -r requirements.txt

echo "[4/5] Installing the English language model for pronunciation ..."
"$PY" -m spacy download en_core_web_sm

echo "[5/5] Downloading the Kokoro voice model (about 330 MB, first time only) ..."
"$PY" -c "from voicetrans.tts import KokoroTTS; t=KokoroTTS(); t.synthesize('Hello!'); print('Kokoro OK on', t.device_name)"

[ -f .env ] || cp .env.example .env
chmod +x start.command

echo
echo "=============================================="
echo "  Done! Double-click start.command to open the app."
echo "=============================================="
trap - ERR
pause
