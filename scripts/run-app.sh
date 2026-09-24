#!/bin/bash
# Jalankan backend Flask + bridge (butuh mosquitto jalan dulu)
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
python3 -c "import flask" 2>/dev/null || pip3 install --user --break-system-packages -r requirements.txt
python3 app.py
