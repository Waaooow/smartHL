"""Simulator device ESP32 (tanpa hardware).
Publish tiap 5 dtk ke smarthl/{MQTT_ID}/up/telemetry
Subscribe smarthl/{MQTT_ID}/down/cmd untuk LED virtual.
Usage: python3 sim_device.py [mqtt_id]
"""
import json
import os
import random
import sys
import time
import paho.mqtt.client as mqtt

MQTT_ID = sys.argv[1] if len(sys.argv) > 1 else "shl-demo01"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DEVICE_TOKEN", "")
BROKER = os.environ.get("MQTT_HOST", "127.0.0.1")
PORT = int(os.environ.get("MQTT_PORT", "1883"))

led = 0

def on_connect(c, u, f, rc, props=None):
    print(f"[sim:{MQTT_ID}] connected {rc}", flush=True)
    c.subscribe(f"smarthl/{MQTT_ID}/down/cmd")
    c.publish(f"smarthl/{MQTT_ID}/up/status", "online", qos=1, retain=True)

def on_message(c, u, msg):
    global led
    try:
        cmd = json.loads(msg.payload.decode())
        if "led" in cmd:
            led = int(cmd["led"])
            print(f"[sim] LED -> {led}", flush=True)
    except Exception as e:
        print(f"[sim] bad cmd: {e}", flush=True)

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{MQTT_ID}")
if TOKEN:
    client.username_pw_set(MQTT_ID, TOKEN)
client.will_set(f"smarthl/{MQTT_ID}/up/status", "offline", qos=1, retain=True)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, PORT, 60)
client.loop_start()

try:
    while True:
        payload = {
            "temp": round(25 + random.uniform(-2, 5), 1),
            "hum": round(55 + random.uniform(-10, 15), 1),
            "led": led,
            "rssi": random.randint(-75, -45),
        }
        client.publish(f"smarthl/{MQTT_ID}/up/telemetry", json.dumps(payload))
        print(f"[sim] pub {payload}", flush=True)
        time.sleep(5)
except KeyboardInterrupt:
    client.publish(f"smarthl/{MQTT_ID}/up/status", "offline", qos=1, retain=True)
    client.loop_stop()
