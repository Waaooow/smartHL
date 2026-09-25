"""Bridge MQTT -> SQLite untuk smartHL prototype.
Subscribe: smarthl/+/up/telemetry , smarthl/+/up/status
Tulis ke tabel telemetry + update devices.status/last_seen.
Jalan bareng app.py (thread) atau standalone: python3 mqtt_bridge.py
"""
import json
import os
import datetime
import paho.mqtt.client as mqtt

import db as dbmod

BROKER_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))
BROKER_USER = os.environ.get("MQTT_USER", "")
BROKER_PASS = os.environ.get("MQTT_PASS", "")

_client_cfgs = {}  # broker_id -> dict(host,port,user,pass)


def db():
    return dbmod.connect()


def load_brokers():
    """Koneksi broker aktif dari DB + pastikan default id=1 ada."""
    conn = db()
    try:
        rows = conn.execute(
            "SELECT * FROM broker_connections WHERE enabled=1 ORDER BY id").fetchall()
    except Exception:
        rows = []
    if not any(r["id"] == 1 for r in rows):
        rows = [{"id": 1, "name": "Broker bawaan", "host": BROKER_HOST,
                 "port": BROKER_PORT, "username": BROKER_USER,
                 "password": BROKER_PASS, "use_tls": 0}] + list(rows)
    conn.close()
    return rows

def ensure_schema():
    PK = dbmod.auto_pk()
    conn = db()
    conn.execute(f"""CREATE TABLE IF NOT EXISTS telemetry (
        id {PK},
        device_id INTEGER,
        key TEXT NOT NULL,
        value REAL,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    for idx in ['CREATE INDEX idx_tel_dev_ts ON telemetry(device_id, ts)']:
        try:
            conn.execute(idx)
        except Exception:
            pass
    conn.execute(f"""CREATE TABLE IF NOT EXISTS device_functions (
        id {PK},
        device_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'sensor',
        unit TEXT DEFAULT '',
        pin TEXT DEFAULT '',
        sort INTEGER DEFAULT 0
    )""")
    try:
        conn.execute("CREATE UNIQUE INDEX idx_func_dev_key ON device_functions(device_id, key)")
    except Exception:
        pass
    try:
        has = conn.execute('SELECT id FROM broker_connections WHERE id=1').fetchone()
        if not has:
            conn.execute(
                'INSERT INTO broker_connections (id, user_id, name, host, port, ws_port, use_tls, enabled)'
                ' VALUES (1, NULL, ?, ?, 1883, 9001, 0, 1)',
                ('Broker bawaan (include)', BROKER_HOST))
    except Exception:
        pass
    for col in ["mqtt_id TEXT", "token TEXT", "last_seen DATETIME", "broker_id INTEGER DEFAULT 1"]:
        try:
            conn.execute(f"ALTER TABLE devices ADD COLUMN {col}")
        except Exception:
            pass
    for col in ["alert_above REAL", "alert_below REAL"]:
        try:
            conn.execute(f"ALTER TABLE device_functions ADD COLUMN {col}")
        except Exception:
            pass
    conn.execute(f"""CREATE TABLE IF NOT EXISTS notifications (
        id {PK},
        device_id INTEGER,
        key TEXT DEFAULT '',
        title TEXT NOT NULL,
        message TEXT DEFAULT '',
        level TEXT DEFAULT 'info',
        read INTEGER DEFAULT 0,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS broker_connections (
        id {PK},
        user_id INTEGER,
        name TEXT NOT NULL,
        host TEXT NOT NULL,
        port INTEGER DEFAULT 1883,
        ws_port INTEGER DEFAULT 9001,
        use_tls INTEGER DEFAULT 0,
        username TEXT DEFAULT '',
        password TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1
    )""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS broker_stats (
        id {PK},
        broker_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT DEFAULT '',
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    try:
        conn.execute("CREATE UNIQUE INDEX idx_bstat_broker_key ON broker_stats(broker_id, key)")
    except Exception:
        pass
    try:
        conn.execute("CREATE INDEX idx_notif_read_ts ON notifications(read, ts)")
    except Exception:
        pass
    try:
        conn.execute('UPDATE devices SET broker_id=1 WHERE broker_id IS NULL')
    except Exception:
        pass
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
            f"""SELECT id FROM notifications WHERE device_id=? AND key=?
               AND ts > {dbmod.recent_minutes_sql(15)} LIMIT 1""",
            (dev_id, f["key"])).fetchone()
        if recent:
            continue
        conn.execute(
            "INSERT INTO notifications (device_id, key, title, message, level) VALUES (?,?,?,?,?)",
            (dev_id, f["key"], f"{f['label']}: {v}", f"Nilai {v} {hit}", "warning"))

def handle_telemetry(broker_id, mqtt_id, payload: dict):
    conn = db()
    row = conn.execute("SELECT * FROM devices WHERE mqtt_id=? AND broker_id=?",
                       (mqtt_id, broker_id)).fetchone()
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

# Metrik $SYS mosquitto yang disimpan (topik -> key tampil)
SYS_MAP = {
    "uptime": "uptime",
    "version": "version",
    "clients/connected": "clients_connected",
    "clients/total": "clients_total",
    "clients/maximum": "clients_max",
    "messages/received": "msg_in",
    "messages/sent": "msg_out",
    "subscriptions/count": "subscriptions",
    "bytes/received": "bytes_in",
    "bytes/sent": "bytes_out",
    "publish/messages/received": "pub_in",
    "publish/messages/sent": "pub_out",
}


def store_sys(broker_id, topic, payload):
    if not topic.startswith("$SYS/broker/"):
        return
    tail = topic[len("$SYS/broker/"):]
    key = SYS_MAP.get(tail)
    if not key:
        return
    try:
        conn = db()
        conn.execute("REPLACE INTO broker_stats (broker_id, key, value, ts) VALUES (?,?,?,CURRENT_TIMESTAMP)",
                     (broker_id, key, payload[:64]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[bridge:{broker_id}] stat error: {e}", flush=True)


def make_callbacks(broker_id):
    def on_connect(client, userdata, flags, reason_code, props=None):
        print(f"[bridge:{broker_id}] connected rc={reason_code}", flush=True)
        client.subscribe("smarthl/+/up/telemetry", qos=0)
        client.subscribe("smarthl/+/up/status", qos=0)
        if broker_id == 1:
            client.subscribe("$SYS/#", qos=0)

    def on_message(client, userdata, msg):
        try:
            if msg.topic.startswith("$SYS/"):
                store_sys(broker_id, msg.topic,
                          msg.payload.decode(errors="ignore").strip())
                return
            parts = msg.topic.split("/")
            # smarthl/{mqtt_id}/up/{kind}
            if len(parts) != 4:
                return
            _, mqtt_id, _, kind = parts
            payload_raw = msg.payload.decode(errors="ignore").strip()
            if kind == "status":
                conn = db()
                st = "Online" if payload_raw == "online" else "Offline"
                conn.execute("UPDATE devices SET status=? WHERE mqtt_id=? AND broker_id=?",
                             (st, mqtt_id, broker_id))
                conn.commit()
                conn.close()
                return
            if kind == "telemetry":
                payload = json.loads(payload_raw)
                if isinstance(payload, dict):
                    handle_telemetry(broker_id, mqtt_id, payload)
        except Exception as e:
            print(f"[bridge:{broker_id}] error: {e}", flush=True)
    return on_connect, on_message


def run_broker(b):
    import uuid
    cid = f"smarthl-bridge-{b['id']}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    user = b.get("username") or (BROKER_USER if b["id"] == 1 else "")
    pw = b.get("password") or (BROKER_PASS if b["id"] == 1 else "")
    if user:
        client.username_pw_set(user, pw)
    on_connect, on_message = make_callbacks(b["id"])
    client.on_connect = on_connect
    client.on_message = on_message
    if b.get("use_tls"):
        client.tls_set()
    client.connect(b["host"], int(b.get("port") or 1883), 60)
    client.loop_forever()


def main():
    import threading
    ensure_schema()
    brokers = load_brokers()
    if not brokers:
        print("[bridge] tidak ada koneksi broker aktif", flush=True)
        return
    for b in brokers:
        t = threading.Thread(target=run_broker, args=(b,), daemon=True,
                             name=f"bridge-{b['id']}")
        t.start()
        print(f"[bridge] subscribe {b['name']} ({b['host']}:{b.get('port') or 1883})", flush=True)
    for t in threading.enumerate():
        if t.name.startswith("bridge-"):
            t.join()

if __name__ == "__main__":
    main()
