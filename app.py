from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
from wakeonlan import send_magic_packet
from ping3 import ping

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

    # 2. MIGRASI: Tambah kolom kalau belum ada (Cegah KeyError)
    try:
        conn.execute('ALTER TABLE devices ADD COLUMN ip_address TEXT')
    except: pass
    try:
        conn.execute('ALTER TABLE devices ADD COLUMN mac_address TEXT')
    except: pass
    try:
        conn.execute('ALTER TABLE devices ADD COLUMN interface TEXT DEFAULT "eth0"')
    except: pass

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
    conn.close()

    devices = []
    for d in devices_raw:
        dev = dict(d)
        # Gunakan .get() agar aman jika kolom kosong
        ip = dev.get('ip_address')
        if ip:
            # PING: Cek status real-time
            status_ping = ping(ip, timeout=0.5)
            dev['status'] = 'Online' if status_ping else 'Offline'
        devices.append(dev)
    
    return render_template('dashboard.html', devices=devices)

@app.route('/add_device', methods=['POST'])
def add_device():
    name = request.form['name']
    dev_type = request.form['type']
    ip = request.form.get('ip_address')
    mac = request.form.get('mac_address')
    interface = request.form.get('interface', 'eth0') # Ambil input interface
    
    conn = get_db_connection()
    conn.execute('INSERT INTO devices (name, type, ip_address, mac_address, interface) VALUES (?, ?, ?, ?, ?)',
                 (name, dev_type, ip, mac, interface))
    conn.commit()
    conn.close()
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
    conn.execute('DELETE FROM devices WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0')