"""Virtual ESP8266 (software-in-the-loop) untuk smartHL.

Meniru struktur sketch Arduino: setup() / loop(), log gaya Serial.println,
sensor DHT22 (suhu/lembab acak), LED di GPIO2, koneksi MQTT ke Mosquitto lokal.
ESP8266 asli berarsitektur Xtensa — tidak bisa dijalankan sebagai VM ringan,
jadi praktik standarnya: simulator perilaku firmwarenya (file ini),
firmware betulan tetap di firmware/esp32_smarthl.ino untuk di-flash ke hardware.

Topik (sama dgn firmware):
  smarthl/{MQTT_ID}/up/telemetry  -> {"temp":..,"hum":..,"led":..,"rssi":..}
  smarthl/{MQTT_ID}/up/status     -> online/offline (retain + LWT)
  smarthl/{MQTT_ID}/down/cmd      -> {"led":1/0}

Usage: python3 virtual_esp8266.py [mqtt_id]
"""
import json
import random
import sys
import time

import paho.mqtt.client as mqtt
import os

MQTT_ID = sys.argv[1] if len(sys.argv) > 1 else "shl-esp01"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DEVICE_TOKEN", "")
BROKER = os.environ.get("MQTT_HOST", "127.0.0.1")
PORT = int(os.environ.get("MQTT_PORT", "1883"))

TOP_UP = f"smarthl/{MQTT_ID}/up/telemetry"
TOP_DOWN = f"smarthl/{MQTT_ID}/down/cmd"
TOP_STATUS = f"smarthl/{MQTT_ID}/up/status"

# --- state hardware virtual (output generik: key apa pun bisa dikontrol) ---
outputs = {"led": 0}
_wifi_connected = False


def Serial_println(msg):
    print(f"[ESP8266:{MQTT_ID}] {msg}", flush=True)


def setup():
    global _wifi_connected
    Serial_println("Serial.begin(115200)")
    Serial_println("Connecting to WiFi... (virtual, always OK)")
    _wifi_connected = True
    Serial_println("WiFi connected, IP: 192.168.18.200 (virtual)")


def on_connect(client, userdata, flags, rc, props=None):
    Serial_println(f"MQTT connected rc={rc}")
    client.subscribe(TOP_DOWN)
    client.publish(TOP_STATUS, "online", qos=1, retain=True)


def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    Serial_println(f"Message arrived [{msg.topic}]: {payload}")
    try:
        cmd = json.loads(payload)
        for k, v in cmd.items():
            key = str(k).lower()
            if key.replace("_", "").isalnum():
                outputs[key] = int(float(v))
                Serial_println(f"digitalWrite({key}, {outputs[key]})")
    except Exception as e:
        Serial_println(f"bad cmd: {e}")


def dht22_read():
    # Virtual DHT22: suhu 24-32C, hum 45-75%
    return round(25 + random.uniform(-1, 7), 1), round(60 + random.uniform(-15, 15), 1)


def loop(client):
    temp, hum = dht22_read()
    rssi = random.randint(-80, -45)
    payload = {"temp": temp, "hum": hum, "rssi": rssi}
    payload.update(outputs)
    client.publish(TOP_UP, json.dumps(payload))
    Serial_println(f"publish {TOP_UP}: {json.dumps(payload)}")
    time.sleep(5)


def main():
    setup()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"virt-{MQTT_ID}")
    if TOKEN:
        client.username_pw_set(MQTT_ID, TOKEN)
    client.will_set(TOP_STATUS, "offline", qos=1, retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_start()
    try:
        while True:
            loop(client)
    except KeyboardInterrupt:
        Serial_println("reboot (Ctrl+C)")
        client.publish(TOP_STATUS, "offline", qos=1, retain=True)
        client.loop_stop()


if __name__ == "__main__":
    main()
