# Firmware smartHL — panduan singkat

Dua contoh siap-flash, protokolnya sama (dashboard tidak peduli board apa):

| File | Board | Output | Sensor |
|---|---|---|---|
| `esp32_smarthl.ino` | ESP32 DevKit | LED GPIO2, relay GPIO5 + GPIO18 | temp/hum simulasi (siap DHT22) |
| `esp8266_smarthl.ino` | NodeMCU / Wemos D1 mini | LED built-in, relay D1 (GPIO5) | LDR A0 (`light`) + temp/hum simulasi |

## Cara pakai (5 langkah)

1. Arduino IDE → Boards Manager → install `esp32` (Espressif) / `esp8266`.
   Library Manager → install `PubSubClient` (Nick O'Leary). Itu saja.
2. Buka file `.ino` yang sesuai, isi 5 nilai: `WIFI_SSID`, `WIFI_PASS`,
   `BROKER_IP` (IP broker yang terjangkau dari WiFi rumah, cth `192.168.18.107`),
   `MQTT_ID` (contoh `shl-lampu-teras`), dan `MQTT_TOKEN` (kolom Token device
   di halaman Perangkat — broker menolak koneksi tanpa ini).
3. Flash, buka Serial Monitor 115200. Harus muncul `WiFi... <ip>` lalu `MQTT connect...ok`.
4. Di dashboard → Tambah Perangkat → tipe Sensor/Lampu → **MQTT ID persis sama**
   dengan langkah 2. Dalam ±5 detik status jadi Online + angka muncul.
5. Dashboard adalah acuan default: tiap perintah toggle/slider dikirim sebagai
   pesan **retained**. ESP yang baru boot langsung menerima perintah terakhir
   saat subscribe — pin mengikuti dashboard otomatis. (Kind `button` sengaja
   tidak di-retain agar tidak ke-trigger ulang tiap reboot.)
5. Tambah fungsi sesuai hardware: `led` (toggle), `relay1` (toggle, pin GPIO5),
   `temp`/`hum`/`light` (sensor). Tombol di web langsung menggerakkan pin.

## Aturan main key

* Key = huruf/angka/underscore, cth `relay1`. Nilai = angka (`1`/`0`, slider `0-100`).
* Dashboard → device: publish JSON ke `smarthl/{id}/down/cmd`, cth `{"relay1":1}`.
* Device → dashboard: publish JSON ke `smarthl/{id}/up/telemetry` tiap 5 detik.
* Key baru di dashboard HARUS ditangani di `handleCmd()` + ikut dikirim di telemetri,
  kalau tidak tombol web terkirim tapi hardware diam (cek Serial Monitor).

## Troubleshooting cepat

| Gejala | Penyebab paling sering |
|---|---|
| Serial `MQTT connect...gagal rc=-2` berulang | Salah `BROKER_IP` / ESP beda jaringan dari broker / port 1883 ketutup |
| Serial `MQTT connect...gagal rc=4` berulang | MQTT ID salah atau TOKEN salah (cek kolom Token di dashboard) |
| Serial ok, dashboard tetap Offline | MQTT ID beda antara firmware vs dashboard (cek huruf besar/kecil) |
| Tombol diklik, relay tidak gerak | Key belum ada di `handleCmd()` / salah pin |
| Toggle balik sendiri setelah diklik | Normal jika device belum lapor balik >8 detik; cek ESP masih publish |

## Jalur HTTP (cadangan, tanpa MQTT)

Kalau jaringan memblokir port 1883, device bisa POST langsung (token = kolom Token device):

```bash
curl -X POST http://192.168.18.107:5000/api/telemetry \
  -H 'Content-Type: application/json' -H 'X-Token: TOKEN_DEVICE' \
  -d '{"mqtt_id":"shl-esp01","temp":27.5,"hum":60}'
```
