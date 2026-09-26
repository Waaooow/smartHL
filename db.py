"""Lapisan database smartHL: SQLite (dev/lokal) atau MariaDB (compose/prod).

Pilih via env DB_TYPE=sqlite|mariadb. Semua SQL di app memakai placeholder
"?" ala sqlite; wrapper di sini menerjemahkannya ke "%s" untuk MariaDB.
Baris hasil selalu dict (sqlite3.Row -> dict, pymysql DictCursor).
"""
import os
import time

DIALECT = os.environ.get("DB_TYPE", "sqlite").lower()
DB_PATH = os.environ.get("DB_PATH", "database.db")

MYSQL_CFG = {
    "host": os.environ.get("DB_HOST", "mariadb"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "smarthl"),
    "password": os.environ.get("DB_PASS", "smarthl"),
    "database": os.environ.get("DB_NAME", "smarthl"),
}


import re

_RESERVED = re.compile(r"\b(key|value|read)\b")


def translate(sql):
    if DIALECT == "mariadb":
        # KEY/VALUE/READ reserved di MySQL -> quote; SQLite tak perlu.
        sql = _RESERVED.sub(r"`\1`", sql)
        return sql.replace("?", "%s")
    return sql


def recent_minutes_sql(minutes):
    """Fragmen 'timestamp kolom < X menit lalu' per dialek."""
    if DIALECT == "mariadb":
        return f"DATE_SUB(NOW(), INTERVAL {int(minutes)} MINUTE)"
    return f"datetime('now','-{int(minutes)} minutes')"


def auto_pk():
    if DIALECT == "mariadb":
        return "INT AUTO_INCREMENT PRIMARY KEY"
    return "INTEGER PRIMARY KEY AUTOINCREMENT"


def _fernet():
    import base64
    import hashlib
    from cryptography.fernet import Fernet
    secret = os.environ.get("SECRET_KEY", "smarthl_secret_key").encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def enc_pw(p):
    """Enkripsi password broker eksternal. Ganti SECRET_KEY = data tak terbaca."""
    if not p:
        return ""
    return "enc:" + _fernet().encrypt(p.encode()).decode()


def dec_pw(s):
    """Dekripsi; fallback plaintext untuk data lama (di-enkripsi ulang saat disimpan)."""
    if not s:
        return ""
    if s.startswith("enc:"):
        try:
            return _fernet().decrypt(s[4:].encode()).decode()
        except Exception:
            return ""
    return s


class Conn:
    """Wrapper tipis: execute() otomatis translate placeholder."""

    def __init__(self, raw, is_mysql):
        self._raw = raw
        self._mysql = is_mysql

    def execute(self, sql, params=()):
        sql = translate(sql) if self._mysql else sql
        cur = self._raw.cursor()
        cur.execute(sql, params or ())
        return cur

    def commit(self):
        return self._raw.commit()

    def close(self):
        return self._raw.close()


def _connect_sqlite(path):
    import sqlite3
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    rows = raw
    # bungkus agar baris jadi dict
    class DictConn(Conn):
        def execute(self, sql, params=()):
            cur = self._raw.execute(sql, params or ())
            return _RowCursor(cur)
    return DictConn(raw, False)


class _RowCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        r = self._cur.fetchone()
        return dict(r) if r is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]


def _connect_mysql(retries=30):
    import pymysql
    last = None
    for _ in range(retries):
        try:
            raw = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **MYSQL_CFG)
            return Conn(raw, True)
        except Exception as e:  # mariadb belum siap -> tunggu (penting saat compose up)
            last = e
            time.sleep(2)
    raise last


def connect():
    if DIALECT == "mariadb":
        return _connect_mysql()
    return _connect_sqlite(DB_PATH)
