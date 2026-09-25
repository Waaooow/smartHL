#!/usr/bin/env python3
"""Generate Mosquitto password_file + acl_file dari database smartHL.

Isolasi per user: tiap device (username = mqtt_id, password = token)
hanya boleh tulis topik up/ miliknya + baca down/ miliknya.
User 'bridge' (dipakai app + bridge) boleh semua.

Usage:
  python3 scripts/sync-mqtt-auth.py [--db database.db] [--out mosquitto/auth]

Butuh binary mosquitto_passwd (mosquitto_root lokal atau PATH).
Setelah sync: restart mosquitto ATAU kill -HUP <pid> agar config dibaca ulang.
"""
import argparse
import os
import secrets
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as dbmod

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASSWD_BINS = [
    shutil.which("mosquitto_passwd"),
    os.path.expanduser("~/mosquitto-root/usr/bin/mosquitto_passwd"),
]


def find_passwd_bin():
    for b in PASSWD_BINS:
        if b and os.path.isfile(b) and os.access(b, os.X_OK):
            return b
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("DB_PATH", os.path.join(BASE, "database.db")))
    ap.add_argument("--out", default=os.path.join(BASE, "mosquitto", "auth"))
    args = ap.parse_args()

    passwd_bin = find_passwd_bin()
    if not passwd_bin:
        print("ERROR: mosquitto_passwd tidak ketemu (PATH / ~/mosquitto-root)", file=sys.stderr)
        return 1
    if args.db:
        dbmod.DB_PATH = args.db

    os.makedirs(args.out, mode=0o700, exist_ok=True)
    passwd_file = os.path.join(args.out, "passwd")
    acl_file = os.path.join(args.out, "acl")
    bridge_env = os.path.join(args.out, "bridge.env")

    conn = dbmod.connect()
    # Hanya device di broker bawaan (id=1); broker eksternal milik user-nya.
    try:
        devs = conn.execute(
            "SELECT mqtt_id, token FROM devices WHERE mqtt_id IS NOT NULL AND mqtt_id != '' AND (broker_id=1 OR broker_id IS NULL)").fetchall()
    except Exception:
        devs = conn.execute(
            "SELECT mqtt_id, token FROM devices WHERE mqtt_id IS NOT NULL AND mqtt_id != ''").fetchall()
    conn.close()

    # password bridge: buat sekali, simpan, pakai ulang
    bridge_pw = None
    if os.path.isfile(bridge_env):
        for line in open(bridge_env):
            if line.startswith("BRIDGE_PASSWORD="):
                bridge_pw = line.strip().split("=", 1)[1]
    if not bridge_pw:
        bridge_pw = secrets.token_hex(16)
        with open(bridge_env, "w") as f:
            f.write(f"BRIDGE_PASSWORD={bridge_pw}\n")
        os.chmod(bridge_env, 0o600)
        print(f"bridge password baru dibuat di {bridge_env}")

    if os.path.isfile(passwd_file):
        os.remove(passwd_file)
    open(passwd_file, "a").close()  # -b butuh file sudah ada
    users = [("bridge", bridge_pw)]
    for d in devs:
        if d["token"]:
            users.append((d["mqtt_id"], d["token"]))
        else:
            print(f"SKIP {d['mqtt_id']}: belum punya token")

    for u, p in users:
        r = subprocess.run([passwd_bin, "-b", passwd_file, u, p],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"ERROR passwd {u}: {r.stderr}", file=sys.stderr)
            return 1
    os.chmod(passwd_file, 0o600)

    with open(acl_file, "w") as f:
        f.write("# GENERATED oleh sync-mqtt-auth.py — jangan edit manual\n")
        f.write("user bridge\ntopic readwrite smarthl/#\ntopic read $SYS/#\n")
        for u, _ in users:
            if u == "bridge":
                continue
            f.write(f"\nuser {u}\n")
            f.write(f"topic write smarthl/{u}/up/#\n")
            f.write(f"topic read smarthl/{u}/down/#\n")
    os.chmod(acl_file, 0o600)

    print(f"OK: {len(users)} user -> {passwd_file}, {acl_file}")
    print("App/bridge pakai: MQTT_USER=bridge MQTT_PASS=(isi bridge.env)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
