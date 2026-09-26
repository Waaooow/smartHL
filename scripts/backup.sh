#!/bin/bash
# Backup MariaDB + file auth broker. Jalan di host (butuh docker + .env).
# Cron: 0 2 * * * /home/alfin/devplace/smartHL/scripts/backup.sh
set -e
set -o pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
set -a; . ./.env; set +a
OUT="$BASE/backups/$(date +%F)"
mkdir -p "$OUT"
chmod 700 "$BASE/backups"
docker compose exec -T mariadb mariadb-dump -uroot -p"$DB_ROOT_PASS" "$DB_NAME" \
  | gzip > "$OUT/smarthl.sql.gz"
cp mosquitto/auth/passwd mosquitto/auth/acl "$OUT/" 2>/dev/null || true
cp mosquitto/auth/bridge.env "$OUT/" 2>/dev/null || true
chmod 600 "$OUT"/*
find "$BASE/backups" -maxdepth 1 -mtime +14 -exec rm -rf {} +
echo "backup -> $OUT"
