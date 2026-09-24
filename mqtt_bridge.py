"""Bridge MQTT -> SQLite untuk smartHL prototype.
Subscribe: smarthl/+/up/telemetry , smarthl/+/up/status
Tulis ke tabel telemetry + update devices.status/last_seen.
Jalan bareng app.py (thread) atau standalone: python3 mqtt_bridge.py
"""
import json
import sqlite3
import datetime
import paho.mqtt.client as mqtt

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883
DB_PATH = "database.db"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_schema():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER,
        key TEXT NOT NULL,
        value REAL,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tel_dev_ts ON telemetry(device_id, ts)")
    try:
        conn.execute("ALTER TABLE devices ADD COLUMN mqtt_id TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE devices ADD COLUMN token TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE devices ADD COLUMN last_seen DATETIME")
    except Exception:
        pass
    conn.commit()
    conn.close()

def handle_telemetry(mqtt_id, payload: dict):
    conn = db()
    row = conn.execute("SELECT * FROM devices WHERE mqtt_id=?", (mqtt_id,)).fetchone()
    if not row:
        conn.close()
        return
    dev_id = row["id"]
    now = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    for k, v in payload.items():
        if not isinstance(k, str) or not k.replace("_", "").isalnum() or len(k) > 24:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        conn.execute("INSERT INTO telemetry (device_id, key, value) VALUES (?,?,?)",
                     (dev_id, k.lower(), fv))
    conn.execute("UPDATE devices SET status='Online', last_seen=? WHERE id=?", (now, dev_id))
    conn.commit()
    conn.close()

def on_connect(client, userdata, flags, reason_code, props=None):
    print(f"[bridge] connected rc={reason_code}", flush=True)
    client.subscribe("smarthl/+/up/telemetry", qos=0)
    client.subscribe("smarthl/+/up/status", qos=0)

def on_message(client, userdata, msg):
    try:
        parts = msg.topic.split("/")
        # smarthl/{mqtt_id}/up/{kind}
        if len(parts) != 4:
            return
        _, mqtt_id, _, kind = parts
        payload_raw = msg.payload.decode(errors="ignore").strip()
        if kind == "status":
            conn = db()
            st = "Online" if payload_raw == "online" else "Offline"
            conn.execute("UPDATE devices SET status=? WHERE mqtt_id=?", (st, mqtt_id))
            conn.commit()
            conn.close()
            return
        if kind == "telemetry":
            payload = json.loads(payload_raw)
            if isinstance(payload, dict):
                handle_telemetry(mqtt_id, payload)
    except Exception as e:
        print(f"[bridge] error: {e}", flush=True)

def main():
    ensure_schema()
    import os
    import uuid
    cid = f"smarthl-bridge-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        from paho.mqtt.properties import PacketTypes  # noqa
    except Exception:
        pass
    # LWT tidak perlu untuk bridge
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_forever()

if __name__ == "__main__":
    main()
