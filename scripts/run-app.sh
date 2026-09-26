#!/bin/bash
# DEV SAJA: debug server + reloader. JANGAN menghadap LAN/internet
# (debugger PIN interaktif = eksekusi kode jarak jauh). Prod pakai compose.
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
# Kredensial bridge MQTT (dibuat oleh scripts/sync-mqtt-auth.py)
if [ -f mosquitto/auth/bridge.env ]; then
  set -a; . ./mosquitto/auth/bridge.env; set +a
  export MQTT_USER=bridge MQTT_PASS="$BRIDGE_PASSWORD"
fi
python3 -c "import flask" 2>/dev/null || pip3 install --user --break-system-packages -r requirements.txt
python3 app.py
