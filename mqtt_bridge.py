"""Bridge MQTT -> SQLite untuk smartHL prototype.
Subscribe: smarthl/+/up/telemetry , smarthl/+/up/status
Tulis ke tabel telemetry + update devices.status/last_seen.
Jalan bareng app.py (thread) atau standalone: python3 mqtt_bridge.py
"""
import json
import os
import sqlite3
import datetime
import paho.mqtt.client as mqtt

BROKER_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))
DB_PATH = os.environ.get("DB_PATH", "database.db")

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
    conn.execute("""CREATE TABLE IF NOT EXISTS device_functions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'sensor',
        unit TEXT DEFAULT '',
        pin TEXT DEFAULT '',
        sort INTEGER DEFAULT 0
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_func_dev_key ON device_functions(device_id, key)")
    for col in ["mqtt_id TEXT", "token TEXT", "last_seen DATETIME"]:
        try:
            conn.execute(f"ALTER TABLE devices ADD COLUMN {col}")
        except Exception:
            pass
    for col in ["alert_above REAL", "alert_below REAL"]:
        try:
            conn.execute(f"ALTER TABLE device_functions ADD COLUMN {col}")
        except Exception:
            pass
    conn.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER,
        key TEXT DEFAULT '',
        title TEXT NOT NULL,
        message TEXT DEFAULT '',
        level TEXT DEFAULT 'info',
        read INTEGER DEFAULT 0,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_read_ts ON notifications(read, ts)")
    conn.commit()
    conn.close()

def check_alerts(conn, dev_id, readings: dict):
    funcs = conn.execute(
        "SELECT key, label, alert_above, alert_below FROM device_functions WHERE device_id=?",
        (dev_id,)).fetchall()
    for f in funcs:
        if f["key"] not in readings:
            continue
        v = readings[f["key"]]
        hit = None
        if f["alert_above"] is not None and v > f["alert_above"]:
            hit = f"melewati batas atas {f['alert_above']}"
        elif f["alert_below"] is not None and v < f["alert_below"]:
            hit = f"di bawah batas {f['alert_below']}"
        if not hit:
            continue
        recent = conn.execute(
            """SELECT id FROM notifications WHERE device_id=? AND key=?
               AND ts > datetime('now','-15 minutes') LIMIT 1""",
            (dev_id, f["key"])).fetchone()
        if recent:
            continue
        conn.execute(
            "INSERT INTO notifications (device_id, key, title, message, level) VALUES (?,?,?,?,?)",
            (dev_id, f["key"], f"{f['label']}: {v}", f"Nilai {v} {hit}", "warning"))

def handle_telemetry(mqtt_id, payload: dict):
    conn = db()
    row = conn.execute("SELECT * FROM devices WHERE mqtt_id=?", (mqtt_id,)).fetchone()
    if not row:
        conn.close()
        return
    dev_id = row["id"]
    now = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    readings = {}
    for k, v in payload.items():
        if not isinstance(k, str) or not k.replace("_", "").isalnum() or len(k) > 24:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        conn.execute("INSERT INTO telemetry (device_id, key, value) VALUES (?,?,?)",
                     (dev_id, k.lower(), fv))
        readings[k.lower()] = fv
    check_alerts(conn, dev_id, readings)
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
