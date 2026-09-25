from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import sqlite3
import secrets
import json
import threading
from wakeonlan import send_magic_packet
from ping3 import ping

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
DB_PATH = os.environ.get("DB_PATH", "database.db")

_mqtt_client = None

def mqtt_pub(topic, payload: dict):
    global _mqtt_client
    if _mqtt_client is None:
        import uuid
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id=f"smarthl-web-{uuid.uuid4().hex[:6]}")
        if MQTT_USER:
            c.username_pw_set(MQTT_USER, MQTT_PASS)
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
app.secret_key = os.environ.get("SECRET_KEY", "smarthl_secret_key")
if app.secret_key == "smarthl_secret_key":
    print("[smarthl] WARNING: memakai SECRET_KEY default (ok untuk lokal, ganti di prod)", flush=True)

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # 1. Pastikan tabel utama ada
    conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)')
    try:
        conn.execute('ALTER TABLE users ADD COLUMN display_name TEXT')
    except Exception:
        pass
    try:
        conn.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0')
    except Exception:
        pass
    try:
        conn.execute('ALTER TABLE devices ADD COLUMN owner_id INTEGER')
    except Exception:
        pass
    # backfill: device tanpa pemilik -> admin pertama (atau user id 1)
    try:
        admin = conn.execute("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1").fetchone()
        if not admin:
            conn.execute('UPDATE users SET is_admin=1 WHERE id=1')
            admin = conn.execute('SELECT id FROM users WHERE id=1').fetchone()
        if admin:
            conn.execute('UPDATE devices SET owner_id=? WHERE owner_id IS NULL', (admin['id'],))
    except Exception:
        pass
    try:
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_mqtt_unique ON devices(mqtt_id)')
    except Exception:
        pass  # NULL boleh ganda di SQLite; duplikat nyata akan gagal di sini bila ada
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
    for col in ['alert_above REAL', 'alert_below REAL']:
        try:
            conn.execute(f'ALTER TABLE device_functions ADD COLUMN {col}')
        except Exception:
            pass
    # Notifikasi (fondasi alert sensor: smoke, movement, ambang, dsb.)
    conn.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER,
        key TEXT DEFAULT '',
        title TEXT NOT NULL,
        message TEXT DEFAULT '',
        level TEXT DEFAULT 'info',
        read INTEGER DEFAULT 0,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_notif_read_ts ON notifications(read, ts)')
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
    from werkzeug.security import check_password_hash, generate_password_hash
    username = request.form['username']
    password = request.form['password']
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    ok = False
    if user:
        stored = user['password'] or ''
        if stored.startswith(('pbkdf2:', 'scrypt:')):
            ok = check_password_hash(stored, password)
        elif stored == password:
            # migrasi sekali jalan: plaintext lama -> hash
            ok = True
            conn.execute('UPDATE users SET password=? WHERE id=?',
                         (generate_password_hash(password), user['id']))
            conn.commit()
    if user and ok:
        session['logged_in'] = True
        session['user_id'] = user['id']
        session['display_name'] = user['display_name'] or user['username']
        session['is_admin'] = bool(user['is_admin']) if 'is_admin' in user.keys() else (user['id'] == 1)
        conn.close()
        return redirect(url_for('dashboard'))
    conn.close()
    flash('Username atau password salah!', 'error')
    return redirect(url_for('login_page'))

def _uid():
    return session.get('user_id')

def _admin():
    return bool(session.get('is_admin'))

def visible_devices(conn):
    """Device yang boleh dilihat user saat ini (admin = semua)."""
    if _admin():
        return [dict(r) for r in conn.execute('SELECT * FROM devices ORDER BY id').fetchall()]
    return [dict(r) for r in conn.execute(
        'SELECT * FROM devices WHERE owner_id=? ORDER BY id', (_uid(),)).fetchall()]

def owned_device(conn, id):
    """Satu device bila boleh diakses, else None."""
    row = conn.execute('SELECT * FROM devices WHERE id=?', (id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if _admin() or d.get('owner_id') == _uid():
        return d
    return None

def notify(conn, device_id, key, title, message, level='info'):
    conn.execute(
        "INSERT INTO notifications (device_id, key, title, message, level) VALUES (?,?,?,?,?)",
        (device_id, key, title, message, level))

def check_alerts(conn, device_id, readings: dict):
    """Buat notifikasi bila nilai melewati ambang fungsi. Cooldown 15 mnt per device+key."""
    funcs = conn.execute(
        "SELECT key, label, alert_above, alert_below FROM device_functions WHERE device_id=?",
        (device_id,)).fetchall()
    for f in funcs:
        if f['key'] not in readings:
            continue
        v = readings[f['key']]
        hit = None
        if f['alert_above'] is not None and v > f['alert_above']:
            hit = ('warning', f"melewati batas atas {f['alert_above']}")
        elif f['alert_below'] is not None and v < f['alert_below']:
            hit = ('warning', f"di bawah batas {f['alert_below']}")
        if not hit:
            continue
        recent = conn.execute(
            """SELECT id FROM notifications WHERE device_id=? AND key=?
               AND ts > datetime('now','-15 minutes') LIMIT 1""",
            (device_id, f['key'])).fetchone()
        if recent:
            continue
        notify(conn, device_id, f['key'], f"{f['label']}: {v}",
               f"Nilai {v} {hit[1]}", level=hit[0])

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'): return redirect(url_for('login_page'))

    conn = get_db_connection()
    devices_raw = visible_devices(conn)
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
    try:
        conn.execute('INSERT INTO devices (name, type, ip_address, mac_address, interface, mqtt_id, token, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (name, dev_type, ip, mac, interface, mqtt_id, token, _uid()))
        conn.commit()
    except Exception:
        conn.close()
        flash(f'MQTT ID {mqtt_id} sudah dipakai (harus unik global)', 'error')
        return redirect(url_for('dashboard'))
    conn.close()
    flash(f'Device {name} dibuat. MQTT ID: {mqtt_id}', 'success')
    return redirect(url_for('dashboard'))

@app.route('/wol/<int:id>')
def wake_device(id):
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    device = owned_device(conn, id)
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
    if not owned_device(conn, id):
        conn.close()
        return redirect(url_for('dashboard'))
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
    device = owned_device(conn, id)
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
    device = owned_device(conn, id)
    if not device:
        conn.close()
        return redirect(url_for('dashboard'))
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
    device = owned_device(conn, id)
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
    device = owned_device(conn, id)
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
    except Exception as e:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({"ok": False, "error": str(e)}), 502
        flash(f"Gagal kirim MQTT: {e}", 'error')
        return redirect(request.form.get('next') or url_for('device_detail', id=id))
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({"ok": True, "key": key, "value": value})
    flash(f"Terkirim ke {device['name']}: {key}={raw}", 'success')
    return redirect(request.form.get('next') or url_for('device_detail', id=id))

@app.route('/device/<int:id>/functions', methods=['POST'])
def add_function(id):
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    if not owned_device(conn, id):
        conn.close()
        return redirect(url_for('dashboard'))
    key = (request.form.get('key') or '').strip().lower()
    label = (request.form.get('label') or key).strip()
    kind = request.form.get('kind', 'sensor')
    unit = (request.form.get('unit') or '').strip()
    pin = (request.form.get('pin') or '').strip()
    if kind not in FUNC_KINDS or not key or not key.replace('_', '').isalnum():
        flash('Fungsi tidak valid (key alfanumerik, kind: sensor/toggle/button/slider)', 'error')
        return redirect(url_for('device_detail', id=id))
    def _num(v):
        v = (v or '').strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    alert_above = _num(request.form.get('alert_above'))
    alert_below = _num(request.form.get('alert_below'))
    try:
        conn.execute(
            "INSERT INTO device_functions (device_id, key, label, kind, unit, pin, sort, alert_above, alert_below)"
            " VALUES (?,?,?,?,?,?,COALESCE((SELECT MAX(sort)+1 FROM device_functions WHERE device_id=?),1),?,?)",
            (id, key, label, kind, unit, pin, id, alert_above, alert_below))
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
    if r and not owned_device(conn, r['device_id']):
        conn.close()
        return redirect(url_for('dashboard'))
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
    devices = visible_devices(conn)
    conn.close()
    return render_template('devices.html', devices=devices)

@app.route('/kontrol')
def kontrol_page():
    """Satu halaman berisi SEMUA kontrol (toggle/button/slider) dari semua device."""
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    devices = visible_devices(conn)
    groups = []
    for d in devices:
        funcs = [dict(r) for r in ensure_functions(conn, d)]
        ctrls = [f for f in funcs if f["kind"] in ("toggle", "button", "slider")]
        if ctrls:
            groups.append({"device": d, "funcs": ctrls,
                           "vals": latest_values(conn, d["id"], [f["key"] for f in ctrls])})
    conn.close()
    return render_template('kontrol.html', groups=groups)

@app.route('/kontrol/data')
def kontrol_data():
    """JSON untuk polling halaman Kontrol: nilai terbaru semua kontrol."""
    if not session.get('logged_in'):
        return jsonify({"error": "unauthorized"}), 401
    conn = get_db_connection()
    devices = visible_devices(conn)
    out = []
    for d in devices:
        funcs = [dict(r) for r in ensure_functions(conn, d)]
        keys = [f["key"] for f in funcs]
        if keys:
            out.append({"device_id": d["id"], "status": d["status"],
                        "vals": latest_values(conn, d["id"], keys)})
    conn.close()
    return jsonify({"groups": out})

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
    readings = {}
    for k, v in data.items():
        if k == "mqtt_id" or not k.replace("_", "").isalnum() or len(k) > 24:
            continue
        try:
            fv = float(v)
            conn.execute("INSERT INTO telemetry (device_id, key, value) VALUES (?,?,?)",
                         (device['id'], k.lower(), fv))
            readings[k.lower()] = fv
        except (TypeError, ValueError):
            pass
    check_alerts(conn, device['id'], readings)
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

@app.route('/profile')
def profile_page():
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    user = conn.execute('SELECT id, username, display_name FROM users WHERE id=?',
                        (session.get('user_id'),)).fetchone()
    conn.close()
    return render_template('profile.html', user=dict(user) if user else {})

@app.route('/profile', methods=['POST'])
def profile_update():
    from werkzeug.security import check_password_hash, generate_password_hash
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id=?', (session.get('user_id'),)).fetchone()
    if not user:
        conn.close()
        return redirect(url_for('login_page'))
    name = (request.form.get('display_name') or '').strip() or user['username']
    conn.execute('UPDATE users SET display_name=? WHERE id=?', (name, user['id']))
    session['display_name'] = name
    new_pw = request.form.get('new_password') or ''
    if new_pw:
        stored = user['password'] or ''
        cur_ok = (check_password_hash(stored, request.form.get('current_password', ''))
                  if stored.startswith(('pbkdf2:', 'scrypt:'))
                  else stored == request.form.get('current_password', ''))
        if not cur_ok:
            conn.close()
            flash('Password lama salah', 'error')
            return redirect(url_for('profile_page'))
        if len(new_pw) < 4:
            conn.close()
            flash('Password baru minimal 4 karakter', 'error')
            return redirect(url_for('profile_page'))
        conn.execute('UPDATE users SET password=? WHERE id=?',
                     (generate_password_hash(new_pw), user['id']))
    conn.commit()
    conn.close()
    flash('Profil disimpan', 'success')
    return redirect(url_for('profile_page'))

@app.route('/notifications')
def notifications_page():
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    if _admin():
        rows = conn.execute(
            """SELECT n.*, d.name AS device_name FROM notifications n
               LEFT JOIN devices d ON d.id=n.device_id
               ORDER BY n.ts DESC LIMIT 100""").fetchall()
    else:
        rows = conn.execute(
            """SELECT n.*, d.name AS device_name FROM notifications n
               LEFT JOIN devices d ON d.id=n.device_id
               WHERE n.device_id IS NULL OR d.owner_id=?
               ORDER BY n.ts DESC LIMIT 100""", (_uid(),)).fetchall()
    conn.close()
    return render_template('notifications.html', items=[dict(r) for r in rows])

@app.route('/notifications/read', methods=['POST'])
def notifications_read():
    if not session.get('logged_in'):
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    if _admin():
        conn.execute('UPDATE notifications SET read=1 WHERE read=0')
    else:
        conn.execute(
            """UPDATE notifications SET read=1 WHERE read=0 AND (device_id IS NULL OR device_id IN
               (SELECT id FROM devices WHERE owner_id=?))""", (_uid(),))
    conn.commit()
    conn.close()
    flash('Semua notifikasi ditandai dibaca', 'success')
    return redirect(url_for('notifications_page'))

@app.route('/api/notifications')
def api_notifications():
    if not session.get('logged_in'):
        return jsonify({"error": "unauthorized"}), 401
    try:
        limit = max(1, min(50, int(request.args.get('limit', '8'))))
    except ValueError:
        limit = 8
    conn = get_db_connection()
    if _admin():
        rows = conn.execute(
            """SELECT n.*, d.name AS device_name FROM notifications n
               LEFT JOIN devices d ON d.id=n.device_id
               ORDER BY n.ts DESC LIMIT ?""", (limit,)).fetchall()
        unread = conn.execute('SELECT COUNT(*) c FROM notifications WHERE read=0').fetchone()['c']
    else:
        rows = conn.execute(
            """SELECT n.*, d.name AS device_name FROM notifications n
               LEFT JOIN devices d ON d.id=n.device_id
               WHERE n.device_id IS NULL OR d.owner_id=?
               ORDER BY n.ts DESC LIMIT ?""", (_uid(), limit)).fetchall()
        unread = conn.execute(
            """SELECT COUNT(*) c FROM notifications n
               LEFT JOIN devices d ON d.id=n.device_id
               WHERE n.read=0 AND (n.device_id IS NULL OR d.owner_id=?)""",
            (_uid(),)).fetchone()['c']
    conn.close()
    return jsonify({"unread": unread, "items": [dict(r) for r in rows]})

@app.route('/api/notifications/read', methods=['POST'])
def api_notifications_read():
    if not session.get('logged_in'):
        return jsonify({"error": "unauthorized"}), 401
    conn = get_db_connection()
    if _admin():
        conn.execute('UPDATE notifications SET read=1 WHERE read=0')
    else:
        conn.execute(
            """UPDATE notifications SET read=1 WHERE read=0 AND (device_id IS NULL OR device_id IN
               (SELECT id FROM devices WHERE owner_id=?))""", (_uid(),))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route('/users')
def users_page():
    if not session.get('logged_in') or not _admin():
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT u.*, (SELECT COUNT(*) FROM devices d WHERE d.owner_id=u.id) AS ndev
           FROM users u ORDER BY u.id""").fetchall()
    conn.close()
    return render_template('users.html', users=[dict(r) for r in rows])

@app.route('/users', methods=['POST'])
def users_create():
    from werkzeug.security import generate_password_hash
    if not session.get('logged_in') or not _admin():
        return redirect(url_for('dashboard'))
    username = (request.form.get('username') or '').strip().lower()
    password = request.form.get('password') or ''
    display = (request.form.get('display_name') or username).strip()
    if not username or not username.replace('_', '').isalnum() or len(password) < 4:
        flash('Username alfanumerik + password min. 4 karakter', 'error')
        return redirect(url_for('users_page'))
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (username, password, display_name, is_admin) VALUES (?,?,?,0)',
                     (username, generate_password_hash(password), display))
        conn.commit()
        flash(f'User {username} dibuat', 'success')
    except Exception:
        flash(f'Username {username} sudah dipakai', 'error')
    conn.close()
    return redirect(url_for('users_page'))

@app.route('/users/<int:id>/delete')
def users_delete(id):
    if not session.get('logged_in') or not _admin():
        return redirect(url_for('dashboard'))
    if id == session.get('user_id'):
        flash('Tidak bisa hapus akun sendiri', 'error')
        return redirect(url_for('users_page'))
    conn = get_db_connection()
    devs = conn.execute('SELECT id FROM devices WHERE owner_id=?', (id,)).fetchall()
    for d in devs:
        conn.execute('DELETE FROM device_functions WHERE device_id=?', (d['id'],))
        conn.execute('DELETE FROM telemetry WHERE device_id=?', (d['id'],))
        conn.execute('DELETE FROM notifications WHERE device_id=?', (d['id'],))
    conn.execute('DELETE FROM devices WHERE owner_id=?', (id,))
    conn.execute('DELETE FROM users WHERE id=?', (id,))
    conn.commit()
    conn.close()
    flash('User + semua device-nya dihapus', 'success')
    return redirect(url_for('users_page'))

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

# Dipakai saat dijalankan via WSGI prod (gunicorn di container):
# gunicorn tidak mengeksekusi blok __main__, jadi bridge+skema di-trigger env.
if os.environ.get("RUN_BRIDGE") == "1" and not os.environ.get("WERKZEUG_RUN_MAIN"):
    init_db()
    _start_bridge_thread()