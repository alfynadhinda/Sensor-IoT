"""
Menyiapkan database PostgreSQL tanpa Docker.

Membuat database (bila belum ada) sesuai DATABASE_URL di .env, lalu membuat tabel dari schema.sql.
Aman dijalankan berulang.

    python setup_db.py
"""
import os
import re
import sys
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/sensor_iot")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    u = urlparse(DATABASE_URL)
    dbname = u.path.lstrip("/") or "sensor_iot"
    if not re.fullmatch(r"[A-Za-z0-9_]+", dbname):
        sys.exit(f"Nama database '{dbname}' tidak valid. Pakai huruf, angka, dan garis bawah saja.")

    # 1. Buat database lewat koneksi ke database bawaan "postgres"
    admin_url = u._replace(path="/postgres").geturl()
    try:
        conn = psycopg2.connect(admin_url)
    except psycopg2.OperationalError as e:
        sys.exit(
            "Tidak bisa terhubung ke PostgreSQL:\n"
            f"  {str(e).strip()}\n\n"
            "Periksa: (1) layanan PostgreSQL sudah berjalan, (2) user dan password di DATABASE_URL "
            "(file .env) benar, (3) port 5432 sesuai."
        )
    conn.autocommit = True                     # CREATE DATABASE tidak boleh di dalam transaksi
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone():
            print(f"Database '{dbname}' sudah ada.")
        else:
            cur.execute(f'CREATE DATABASE "{dbname}"')
            print(f"Database '{dbname}' dibuat.")
    conn.close()

    # 2. Buat tabel dari schema.sql
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        schema = f.read()
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(schema)
    print("Tabel sensor_readings dan sensor_events siap.")


if __name__ == "__main__":
    main()
