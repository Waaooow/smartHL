from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import secrets
import json
import threading
from wakeonlan import send_magic_packet
from ping3 import ping

import paho.mqtt.client as mqtt

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883

_mqtt_client = None

def mqtt_pub(topic, payload: dict):
    global _mqtt_client
    if _mqtt_client is None:
        import uuid
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id=f"smarthl-web-{uuid.uuid4().hex[:6]}")
        c.connect(MQTT_HOST, MQTT_PORT, 60)
        c.loop_start()
        _mqtt_client = c
    _mqtt_client.publish(topic, json.dumps(payload))

# Kinds fungsi device. sensor = tampil saja, sisanya kirim perintah MQTT.
FUNC_KINDS = ("sensor", "toggle", "button", "slider")

DEFAULT_FUNCS = [
    ("temp", "Suhu", "sensor", "°C", "", 1),
    ("hum", "Kelembaban", "sensor", "%", "", 2),
    ("rssi", "Sinyal WiFi", "sensor", "dBm", "", 3),
    ("led", "LED Built-in", "toggle", "", "GPIO2", 4),
]

def ensure_functions(conn, device):
    """Seed fungsi standar untuk device MQTT yang belum punya fungsi."""
    if not device["mqtt_id"]:
        return []
    rows = conn.execute(
        "SELECT * FROM device_functions WHERE device_id=? ORDER BY sort",
        (device["id"],)).fetchall()
    if rows:
        return rows
    for key, label, kind, unit, pin, sort in DEFAULT_FUNCS:
        conn.execute(
            "INSERT INTO device_functions (device_id, key, label, kind, unit, pin, sort)"
            " VALUES (?,?,?,?,?,?,?)",
            (device["id"], key, label, kind, unit, pin, sort))
    conn.commit()
    return conn.execute(
        "SELECT * FROM device_functions WHERE device_id=? ORDER BY sort",
        (device["id"],)).fetchall()

def latest_values(conn, device_id, keys):
    out = {}
    for k in keys:
        r = conn.execute(
            "SELECT value, ts FROM telemetry WHERE device_id=? AND key=? ORDER BY ts DESC LIMIT 1",
            (device_id, k)).fetchone()
        if r:
            out[k] = {"value": r["value"], "ts": r["ts"]}
    return out

app = Flask(__name__)
app.secret_key = 'smarthl_secret_key'

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # 1. Pastikan tabel utama ada
    conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            ip_address TEXT,
            mac_address TEXT,
            interface TEXT DEFAULT 'eth0',
            status TEXT DEFAULT 'Offline'
        )
    ''')

    # 2b. Skema IoT (Arduino-Cloud ala smartHL)
    conn.execute('''CREATE TABLE IF NOT EXISTS telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER,
        key TEXT NOT NULL,
        value REAL,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tel_dev_ts ON telemetry(device_id, ts)')
    conn.execute('''CREATE TABLE IF NOT EXISTS device_functions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'sensor',
        unit TEXT DEFAULT '',
        pin TEXT DEFAULT '',
        sort INTEGER DEFAULT 0
    )''')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_func_dev_key ON device_functions(device_id, key)')
    for col in ['mqtt_id TEXT', 'token TEXT', 'last_seen DATETIME']:
        try:
            conn.execute(f'ALTER TABLE devices ADD COLUMN {col}')
        except Exception:
            pass

    # 3. User dummy
    try:
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('admin', 'admin123'))
    except: pass
    
    conn.commit()
    conn.close()

@app.route('/')
def login_page():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login_action():
    username = request.form['username']
    password = request.form['password']
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
    conn.close()
    if user:
        session['logged_in'] = True
        return redirect(url_for('dashboard'))
    flash('Username atau password salah!', 'error')
    return redirect(url_for('login_page'))

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'): return redirect(url_for('login_page'))

    conn = get_db_connection()
    devices_raw = conn.execute('SELECT * FROM devices').fetchall()
    devices = []
    for d in devices_raw:
        dev = dict(d)
        ip = dev.get('ip_address')
        # Ping hanya untuk tipe PC yg punya IP; device IoT status dari MQTT (last_seen/telemetry)
        if dev.get('type') == 'PC' and ip:
            try:
                status_ping = ping(ip, timeout=0.5)
                dev['status'] = 'Online' if status_ping else 'Offline'
            except Exception:
                pass
        # Telemetri terakhir untuk device IoT
        last = conn.execute(
            "SELECT key, value, ts FROM telemetry WHERE device_id=? ORDER BY ts DESC LIMIT 4",
            (dev['id'],)).fetchall()
        dev['telemetry'] = {r['key']: r['value'] for r in last}
        devices.append(dev)
    conn.close()

    return render_template('dashboard.html', devices=devices)

@app.route('/add_device', methods=['POST'])
def add_device():
    name = request.form['name']
    dev_type = request.form['type']
    ip = request.form.get('ip_address')
    mac = request.form.get('mac_address')
    interface = request.form.get('interface', 'eth0') # Ambil input interface
    mqtt_id = request.form.get('mqtt_id') or ('shl-' + secrets.token_hex(3))
    # mqtt_id unik sederhana
    conn = get_db_connection()
    exists = conn.execute('SELECT id FROM devices WHERE mqtt_id=?', (mqtt_id,)).fetchone()
    if exists:
        mqtt_id = 'shl-' + secrets.token_hex(4)
    token = secrets.token_hex(8)
    conn.execute('INSERT INTO devices (name, type, ip_address, mac_address, interface, mqtt_id, token) VALUES (?, ?, ?, ?, ?, ?, ?)',
                 (name, dev_type, ip, mac, interface, mqtt_id, token))
    conn.commit()
    conn.close()
    flash(f'Device {name} dibuat. MQTT ID: {mqtt_id}', 'success')
    return redirect(url_for('dashboard'))

@app.route('/wol/<int:id>')
def wake_device(id):
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM devices WHERE id = ?', (id,)).fetchone()
    conn.close()
    
    if device and device['mac_address']:
        try:
            # Kirim magic packet lewat interface yang ditentukan user (misal: eth0)
            send_magic_packet(device['mac_address'], interface=device['interface'])
            flash(f"Magic Packet dikirim ke {device['name']} via {device['interface']}!", 'success')
        except Exception as e:
            flash(f"Gagal mengirim WoL: {str(e)}", 'error')
    return redirect(url_for('dashboard'))

@app.route('/delete_device/<int:id>')
def delete_device(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM device_functions WHERE device_id=?', (id,))
    conn.execute('DELETE FROM telemetry WHERE device_id=?', (id,))
    conn.execute('DELETE FROM devices WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/device/<int:id>/cmd', methods=['POST'])
def device_cmd(id):
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM devices WHERE id=?', (id,)).fetchone()
    conn.close()
    if not device or not device['mqtt_id']:
        flash('Device belum punya MQTT ID', 'error')
        return redirect(url_for('dashboard'))
    led = request.form.get('led', '0')
    try:
        mqtt_pub(f"smarthl/{device['mqtt_id']}/down/cmd", {"led": int(led)})
        flash(f"Perintah LED={led} dikirim ke {device['name']}", 'success')
    except Exception as e:
        flash(f"Gagal kirim MQTT: {e}", 'error')
    return redirect(url_for('dashboard'))

@app.route('/device/<int:id>')
def device_detail(id):
    """Halaman per-device: sensor + kontrol (render dari device_functions) + kelola fungsi."""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM devices WHERE id=?', (id,)).fetchone()
    if not device:
        conn.close()
        return redirect(url_for('dashboard'))
    device = dict(device)
    funcs = [dict(r) for r in ensure_functions(conn, device)]
    vals = latest_values(conn, id, [f["key"] for f in funcs])
    hist = [dict(r) for r in conn.execute(
        "SELECT key, value, ts FROM telemetry WHERE device_id=? ORDER BY ts DESC LIMIT 60",
        (id,)).fetchall()]
    conn.close()
    return render_template('device_detail.html', device=device, funcs=funcs, vals=vals, hist=hist)

@app.route('/device/<int:id>/data')
def device_data(id):
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM devices WHERE id=?', (id,)).fetchone()
    rows = conn.execute(
        "SELECT key, value, ts FROM telemetry WHERE device_id=? ORDER BY ts DESC LIMIT 50",
        (id,)).fetchall()
    conn.close()
    if not device:
        return jsonify({"error": "not found"}), 404
    return jsonify({"device": dict(device), "telemetry": [dict(r) for r in rows]})

@app.route('/device/<int:id>/action', methods=['POST'])
def device_action(id):
    """Aksi generik: kirim {key: value} ke smarthl/{mqtt_id}/down/cmd."""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM devices WHERE id=?', (id,)).fetchone()
    conn.close()
    if not device or not device['mqtt_id']:
        flash('Device belum punya MQTT ID', 'error')
        return redirect(url_for('dashboard'))
    key = (request.form.get('key') or '').strip().lower()
    raw = request.form.get('value', '0')
    if not key or not key.replace('_', '').isalnum():
        flash('Key fungsi tidak valid', 'error')
        return redirect(url_for('device_detail', id=id))
    try:
        value = float(raw)
    except ValueError:
        flash('Value harus angka', 'error')
        return redirect(url_for('device_detail', id=id))
    try:
        mqtt_pub(f"smarthl/{device['mqtt_id']}/down/cmd", {key: value})
        flash(f"Terkirim ke {device['name']}: {key}={raw}", 'success')
    except Exception as e:
        flash(f"Gagal kirim MQTT: {e}", 'error')
    return redirect(request.form.get('next') or url_for('device_detail', id=id))

@app.route('/device/<int:id>/functions', methods=['POST'])
def add_function(id):
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    key = (request.form.get('key') or '').strip().lower()
    label = (request.form.get('label') or key).strip()
    kind = request.form.get('kind', 'sensor')
    unit = (request.form.get('unit') or '').strip()
    pin = (request.form.get('pin') or '').strip()
    if kind not in FUNC_KINDS or not key or not key.replace('_', '').isalnum():
        flash('Fungsi tidak valid (key alfanumerik, kind: sensor/toggle/button/slider)', 'error')
        return redirect(url_for('device_detail', id=id))
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO device_functions (device_id, key, label, kind, unit, pin, sort)"
            " VALUES (?,?,?,?,?,?,COALESCE((SELECT MAX(sort)+1 FROM device_functions WHERE device_id=?),1))",
            (id, key, label, kind, unit, pin, id))
        conn.commit()
        flash(f"Fungsi {label} ditambah", 'success')
    except Exception:
        flash(f"Key '{key}' sudah ada di device ini", 'error')
    conn.close()
    return redirect(url_for('device_detail', id=id))

@app.route('/functions/<int:fid>/delete')
def delete_function(fid):
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    r = conn.execute('SELECT device_id FROM device_functions WHERE id=?', (fid,)).fetchone()
    if r:
        conn.execute('DELETE FROM device_functions WHERE id=?', (fid,))
        conn.commit()
    conn.close()
    return redirect(url_for('device_detail', id=r['device_id']) if r else url_for('dashboard'))

@app.route('/devices')
def devices_page():
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM devices ORDER BY id').fetchall()
    devices = [dict(r) for r in rows]
    conn.close()
    return render_template('devices.html', devices=devices)

@app.route('/kontrol')
def kontrol_page():
    """Satu halaman berisi SEMUA kontrol (toggle/button/slider) dari semua device."""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    devices = [dict(r) for r in conn.execute('SELECT * FROM devices ORDER BY id').fetchall()]
    groups = []
    for d in devices:
        funcs = [dict(r) for r in ensure_functions(conn, d)]
        ctrls = [f for f in funcs if f["kind"] in ("toggle", "button", "slider")]
        if ctrls:
            groups.append({"device": d, "funcs": ctrls,
                           "vals": latest_values(conn, d["id"], [f["key"] for f in ctrls])})
    conn.close()
    return render_template('kontrol.html', groups=groups)

@app.route('/api/telemetry', methods=['POST'])
def api_telemetry():
    """Fallback HTTP ala Arduino Cloud (kalau device tidak bisa MQTT). Auth: header X-Token."""
    data = request.get_json(force=True, silent=True) or {}
    mqtt_id = data.get('mqtt_id')
    token = request.headers.get('X-Token', '')
    if not mqtt_id:
        return jsonify({"error": "mqtt_id required"}), 400
    conn = get_db_connection()
    device = conn.execute('SELECT * FROM devices WHERE mqtt_id=?', (mqtt_id,)).fetchone()
    if not device or device['token'] != token:
        conn.close()
        return jsonify({"error": "unauthorized"}), 401
    for k, v in data.items():
        if k == "mqtt_id" or not k.replace("_", "").isalnum() or len(k) > 24:
            continue
        try:
            conn.execute("INSERT INTO telemetry (device_id, key, value) VALUES (?,?,?)",
                         (device['id'], k.lower(), float(v)))
        except (TypeError, ValueError):
            pass
    conn.execute("UPDATE devices SET status='Online', last_seen=CURRENT_TIMESTAMP WHERE id=?",
                 (device['id'],))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route('/health')
def health():
    return jsonify({"ok": True, "mqtt": f"{MQTT_HOST}:{MQTT_PORT}"})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

def _start_bridge_thread():
    try:
        from mqtt_bridge import main as bridge_main
        t = threading.Thread(target=bridge_main, daemon=True)
        t.start()
        print("[smarthl] mqtt bridge thread started", flush=True)
    except Exception as e:
        print(f"[smarthl] bridge gagal start: {e}", flush=True)

if __name__ == '__main__':
    init_db()
    _start_bridge_thread()
    app.run(debug=True, host='0.0.0.0', port=5000)