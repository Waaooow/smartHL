#!/bin/bash
# Jalankan mosquitto lokal tanpa sudo (binary di ~/mosquitto-root)
set -e
BASE="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$BASE/mosquitto/data"
export LD_LIBRARY_PATH="$HOME/mosquitto-root/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
exec "$HOME/mosquitto-root/usr/sbin/mosquitto" -c "$BASE/mosquitto/mosquitto.conf" -v
