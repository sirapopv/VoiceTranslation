#!/bin/bash
# Start VoiceTranslation on macOS. Double-click in Finder.
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Please run install.command first."
  read -r -p "Press Enter to close..." _
  exit 1
fi
echo "Starting VoiceTranslation ... (the browser will open automatically)"
echo "Close this window (or press Ctrl+C) to stop the app."
exec .venv/bin/python app.py
