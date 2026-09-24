# smartHL — IoT self-hosted ala Arduino Cloud (prototype)

Broker MQTT (Mosquitto) + dashboard Flask dalam satu repo.

## Struktur
- `app.py` — dashboard + API + bridge thread
- `mqtt_bridge.py` — subscriber `smarthl/+/up/#` → SQLite `telemetry`
- `mosquitto/mosquitto.conf` — listener `1883` (MQTT) + `9001` (WebSocket)
- `sim_device.py` — simulator device tanpa hardware
- `firmware/esp32_smarthl.ino` — contoh firmware ESP32 (PubSubClient)
- `scripts/run-mosquitto.sh`, `scripts/run-app.sh`
- `docker-compose.yml` — deploy prod (mosquitto + app)

## Topik
- `smarthl/{mqtt_id}/up/telemetry` JSON `{"temp":..,"hum":..,"led":..,"rssi":..}`
- `smarthl/{mqtt_id}/up/status` `online`/`offline` (retain + LWT)
- `smarthl/{mqtt_id}/down/cmd` JSON `{"led":1}`

## Jalankan lokal (VM ini, tanpa sudo/docker)
```bash
./scripts/run-mosquitto.sh   # terminal 1
./scripts/run-app.sh         # terminal 2 (http://192.168.18.107:5000)
python3 sim_device.py shl-demo01  # terminal 3
```
Login dashboard: `admin` / `admin123`. Buat device tipe Sensor/Lampu,
isi MQTT ID misal `shl-demo01`, lalu telemetri + tombol LED langsung jalan.

## Coba di prod server pakai container
```bash
cp .env.example .env   # lalu isi SECRET_KEY random
docker compose up -d --build
docker compose ps
docker compose logs -f app
```
* Dashboard: `http://<ip-server>:5000` (prod: taruh di belakang reverse-proxy + TLS,
  cth `https://iot.alfins.my.id` via Cloudflare proxy ON → `127.0.0.1:5000`).
* Broker MQTT: `<ip-server>:1883` (ESP), WebSocket `:9001`.
* Data SQLite persisten di volume `smarthl-data` (`/data/database.db` di container).
* Berhenti: `docker compose down` (data aman), hapus total: `docker compose down -v`.
* WAJIB `workers=1` di CMD (sudah): bridge MQTT jalan sebagai thread dalam worker.

## API
- `GET /health`, `GET /device/<id>` (JSON, perlu login)
- `POST /device/<id>/cmd` form `led=1/0` (perlu login)
- `POST /api/telemetry` JSON + header `X-Token` (fallback HTTP device)

## Catatan jaringan
- Cloudflare proxy hanya untuk HTTP/WSS 443 (dashboard).
  MQTT native 1883/8883 pakai DNS-only (AAAA langsung ke IPv6 server).
- Prod: set `allow_anonymous false` + `passwd`/`acl` di `mosquitto/`.
