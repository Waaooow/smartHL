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

## Multi-user (1 broker + 1 dashboard untuk banyak orang)

* Tiap device punya pemilik (`owner_id`). User biasa hanya melihat device,
  notifikasi, dan badge miliknya; admin melihat semua + halaman Pengguna.
* `mqtt_id` unik global. Tambah device duplikat otomatis diganti acak.
* Broker mengunci per device: username = `mqtt_id`, password = kolom Token.
  Setelah tambah/hapus device, jalankan lalu reload broker:
```bash
python3 scripts/sync-mqtt-auth.py
kill -HUP $(pgrep -f 'mosquitto.*local.conf')   # lokal; di docker: compose restart mosquitto
```
* Onboarding teman: admin buat akun di Pengguna → teman login → Add Device →
  isi `MQTT_ID` + `MQTT_TOKEN` di sketch → flash.
* App/bridge publish sebagai user `bridge` (`MQTT_USER`/`MQTT_PASS`,
  password di `mosquitto/auth/bridge.env`, jangan commit).

## 1 stack compose: app + MariaDB (+ Mosquitto opsional)
```bash
cp .env.example .env   # isi SECRET_KEY, DB_PASS, DB_ROOT_PASS, BRIDGE_PASSWORD
# Dashboard saja (pakai broker luar via Settings):
docker compose up -d --build
# Lengkap dengan broker bawaan:
docker compose --profile broker up -d --build
docker compose logs -f app
```
Tanpa broker bawaan: matikan baris "Broker bawaan" di Settings (tombol power,
khusus admin) agar bridge tidak retry terus. Device yang broker-nya broker
bawaan akan gagal kirim (pesan error wajar) — pindahkan device ke broker luar
atau ikutkan `--profile broker`.
* Dashboard: `http://<ip-server>:5000` (prod: reverse-proxy + TLS di depan,
  cth `https://iot.alfins.my.id` via Cloudflare proxy ON).
* Broker MQTT: `<ip-server>:1883`, WebSocket `:9001`.
* Dashboard broker: tab **Settings** (admin) → kartu live dari metrik `$SYS`
  (uptime, klien, pesan, load, retained, versi). Tanpa service tambahan.
  (Cedalo Management Center sempat dicoba tapi butuh lisensi — UI-nya crash
  tanpa license — jadi dicabut dari compose.)
* Data di MariaDB (volume `mariadb-data`). Berhenti: `docker compose down`
  (data aman), hapus total: `docker compose down -v`.
* Lokal tanpa docker tetap bisa: default `DB_TYPE=sqlite`, Mosquitto via
  `scripts/run-mosquitto.sh`, app via `scripts/run-app.sh`.
* WAJIB `workers=1` di CMD gunicorn: bridge MQTT jalan sebagai thread.

## Keamanan (ringkas)

* Semua aksi ubah-data wajib POST + token CSRF (form biasa otomatis, AJAX via header).
* Login dibatasi (8x gagal/5 mnt per IP); cookie HttpOnly + SameSite Lax
  (set `SECURE_COOKIES=1` bila sudah HTTPS).
* MQTT ID divalidasi `^[a-z0-9_-]{3,24}$` (cegah wildcard injection ke ACL).
* Password broker eksternal terenkripsi (Fernet, kunci = SECRET_KEY).
  Ganti SECRET_KEY = password itu tak terbaca lagi.
* Container jalan sebagai `appuser` (non-root). Telemetri lama diprune
  (`TELEMETRY_RETENTION_DAYS`, default 90). Backup: `scripts/backup.sh`
  (cron harian disarankan).

## Deploy non-docker (didokumentasikan, tidak disupport prioritaskan)

```bash
pip install -r requirements.txt
./scripts/run-mosquitto.sh  # terminal 1 (butuh ~/mosquitto-root, lihat skrip)
./scripts/run-app.sh        # terminal 2 (SQLite lokal, http://localhost:5000)
```
MariaDB non-docker: set `DB_TYPE=mariadb DB_HOST=... DB_USER=... DB_PASS=... DB_NAME=...`,
jalankan sekali `RUN_BRIDGE=1 python3 -c "import app"` untuk init skema
(lalu jalankan seperti biasa). Auth broker: `python3 scripts/sync-mqtt-auth.py`
(butuh `mosquitto_passwd`) + restart mosquitto.

## Settings → koneksi broker per user

Settings berisi broker bawaan (id=1, ikut paket) + koneksi milikmu (host/port/TLS/auth).
Device memilih broker saat dibuat. Bridge subscribe semua koneksi aktif otomatis
(koneksi baru langsung disubscribe, tanpa restart). Tombol Test cek TCP saja.

## API
- `GET /health`, `GET /device/<id>` (JSON, perlu login)
- `POST /device/<id>/cmd` form `led=1/0` (perlu login)
- `POST /api/telemetry` JSON + header `X-Token` (fallback HTTP device)

## Catatan jaringan
- Cloudflare proxy hanya untuk HTTP/WSS 443 (dashboard).
  MQTT native 1883/8883 pakai DNS-only (AAAA langsung ke IPv6 server).
- Prod: set `allow_anonymous false` + `passwd`/`acl` di `mosquitto/`.
