"""
Server Flask untuk dashboard sensor ESP32.

Alur data:  ESP32 --POST JSON--> /api/data --> PostgreSQL --> /api/* --> dashboard & notebook

Jalankan:
    python app.py                # server pengembangan
    flask --app app run --host 0.0.0.0 --port 5000
"""
import csv
import io
import os
import hmac
from contextlib import contextmanager

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request, send_from_directory

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/sensor_iot")
API_KEY = os.getenv("API_KEY", "rahasia")          # kosong = POST tanpa kunci (hanya untuk uji lokal)
PORT = int(os.getenv("PORT", "5000"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
_pool = None


def get_pool():
    """Buat connection pool saat pertama kali dibutuhkan."""
    global _pool
    if _pool is None:
        from psycopg2.pool import ThreadedConnectionPool
        _pool = ThreadedConnectionPool(1, 10, DATABASE_URL)
    return _pool


@contextmanager
def db_cursor(commit=False):
    from psycopg2.extras import RealDictCursor
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db():
    """Jalankan schema.sql (idempotent)."""
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        sql = f.read()
    with db_cursor(commit=True) as cur:
        cur.execute(sql)


# ----------------------------------------------------------------------------
# Validasi payload & deteksi kejadian (fungsi murni, mudah diuji)
# ----------------------------------------------------------------------------
class PayloadError(ValueError):
    pass


def _num(js, key, lo, hi):
    v = js.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PayloadError(f"{key} harus angka atau null")
    if v != v or not (lo <= v <= hi):          # v != v -> NaN
        raise PayloadError(f"{key} di luar rentang {lo}..{hi}")
    return float(v)


def _bool(js, key):
    v = js.get(key, False)
    if not isinstance(v, bool):
        raise PayloadError(f"{key} harus true atau false")
    return v


def parse_payload(js):
    if not isinstance(js, dict):
        raise PayloadError("Body harus berupa objek JSON")
    device = str(js.get("device_id") or "esp32-01").strip()[:64] or "esp32-01"
    jarak = _num(js, "jarak_cm", 0, 500)
    if jarak is not None and jarak == 0:
        jarak = None                              # 0 = tidak ada pantulan
    servo = js.get("servo_deg", 0)
    if isinstance(servo, bool) or not isinstance(servo, (int, float)) or not (0 <= servo <= 180):
        raise PayloadError("servo_deg harus 0..180")
    return {
        "device_id": device,
        "jarak_cm": jarak,
        "gerakan": _bool(js, "gerakan"),
        "suhu_c": _num(js, "suhu_c", -40, 125),
        "kelembapan": _num(js, "kelembapan", 0, 100),
        "gas": _bool(js, "gas"),
        "servo_deg": int(servo),
    }


def detect_events(prev, cur):
    """Bandingkan dua pembacaan berurutan, hasilkan daftar (jenis, level, keterangan)."""
    if not prev:
        return []
    out = []
    if cur["gerakan"] != prev["gerakan"]:
        out.append(("gerakan_mulai", "warn", "Gerakan terdeteksi") if cur["gerakan"]
                   else ("gerakan_berhenti", "neutral", "Gerakan berhenti"))
    if cur["gas"] != prev["gas"]:
        out.append(("gas_terdeteksi", "crit", "Gas terdeteksi") if cur["gas"]
                   else ("gas_normal", "good", "Udara kembali normal"))
    if cur["servo_deg"] != prev["servo_deg"]:
        out.append(("servo_buka", "warn", f"Servo ke {cur['servo_deg']}°") if cur["servo_deg"] > 0
                   else ("servo_tutup", "neutral", f"Servo ke {cur['servo_deg']}°"))
    return out


def row_out(r):
    r = dict(r)
    if r.get("created_at") is not None:
        r["created_at"] = r["created_at"].isoformat()
    return r


def int_arg(name, default, lo, hi):
    try:
        v = int(request.args.get(name, default))
    except ValueError:
        abort(400, f"{name} harus bilangan bulat")
    return max(lo, min(hi, v))


def device_arg():
    return request.args.get("device_id", "esp32-01")


# ----------------------------------------------------------------------------
# Route
# ----------------------------------------------------------------------------
@app.get("/")
def dashboard():
    return send_from_directory(os.path.join(BASE_DIR, "templates"), "dashboard.html")


@app.get("/health")
def health():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        return jsonify(status="ok", database="ok")
    except Exception as e:                        # noqa: BLE001
        return jsonify(status="error", database=str(e)), 503


@app.post("/api/data")
def ingest():
    if API_KEY:
        sent = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(sent.encode(), API_KEY.encode()):
            abort(401, "API key salah")
    js = request.get_json(silent=True)
    try:
        d = parse_payload(js)
    except PayloadError as e:
        return jsonify(error=str(e)), 400

    with db_cursor(commit=True) as cur:
        cur.execute(
            "SELECT gerakan, gas, servo_deg FROM sensor_readings "
            "WHERE device_id = %s ORDER BY id DESC LIMIT 1", (d["device_id"],))
        prev = cur.fetchone()
        cur.execute(
            "INSERT INTO sensor_readings (device_id, jarak_cm, gerakan, suhu_c, kelembapan, gas, servo_deg) "
            "VALUES (%(device_id)s, %(jarak_cm)s, %(gerakan)s, %(suhu_c)s, %(kelembapan)s, %(gas)s, %(servo_deg)s) "
            "RETURNING id, created_at", d)
        saved = cur.fetchone()
        for jenis, level, ket in detect_events(prev, d):
            cur.execute(
                "INSERT INTO sensor_events (device_id, created_at, jenis, level, keterangan) "
                "VALUES (%s, %s, %s, %s, %s)", (d["device_id"], saved["created_at"], jenis, level, ket))
    return jsonify(id=saved["id"], created_at=saved["created_at"].isoformat()), 201


@app.get("/api/latest")
def latest():
    with db_cursor() as cur:
        cur.execute("SELECT * FROM sensor_readings WHERE device_id = %s ORDER BY id DESC LIMIT 1", (device_arg(),))
        r = cur.fetchone()
    return jsonify(row_out(r) if r else {})


@app.get("/api/history")
def history():
    minutes = int_arg("minutes", 60, 1, 60 * 24 * 90)
    limit = int_arg("limit", 500, 1, 20000)
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM (SELECT * FROM sensor_readings "
            "WHERE device_id = %s AND created_at >= now() - make_interval(mins => %s) "
            "ORDER BY id DESC LIMIT %s) t ORDER BY id ASC",
            (device_arg(), minutes, limit))
        rows = cur.fetchall()
    return jsonify([row_out(r) for r in rows])


@app.get("/api/events")
def events():
    limit = int_arg("limit", 40, 1, 500)
    with db_cursor() as cur:
        cur.execute("SELECT * FROM sensor_events WHERE device_id = %s ORDER BY id DESC LIMIT %s",
                    (device_arg(), limit))
        rows = cur.fetchall()
    return jsonify([row_out(r) for r in rows])


@app.get("/api/stats")
def stats():
    hours = int_arg("hours", 24, 1, 24 * 90)
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT count(*)                                  AS jumlah,
                   round(avg(suhu_c)::numeric, 2)            AS suhu_rata,
                   min(suhu_c)                               AS suhu_min,
                   max(suhu_c)                               AS suhu_max,
                   round(avg(kelembapan)::numeric, 2)        AS lembap_rata,
                   min(kelembapan)                           AS lembap_min,
                   max(kelembapan)                           AS lembap_max,
                   round(avg(jarak_cm)::numeric, 2)          AS jarak_rata,
                   min(jarak_cm)                             AS jarak_min,
                   count(*) FILTER (WHERE gerakan)           AS jumlah_gerakan,
                   count(*) FILTER (WHERE gas)               AS jumlah_gas,
                   count(*) FILTER (WHERE servo_deg > 0)     AS jumlah_servo_buka
            FROM sensor_readings
            WHERE device_id = %s AND created_at >= now() - make_interval(hours => %s)
            """, (device_arg(), hours))
        r = cur.fetchone()
    return jsonify({k: (float(v) if hasattr(v, "quantize") else v) for k, v in r.items()})


@app.get("/api/export.csv")
def export_csv():
    hours = int_arg("hours", 24, 1, 24 * 90)
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, device_id, created_at, jarak_cm, gerakan, suhu_c, kelembapan, gas, servo_deg "
            "FROM sensor_readings WHERE device_id = %s AND created_at >= now() - make_interval(hours => %s) "
            "ORDER BY id", (device_arg(), hours))
        rows = cur.fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "device_id", "created_at", "jarak_cm", "gerakan", "suhu_c", "kelembapan", "gas", "servo_deg"])
    for r in rows:
        w.writerow([r["id"], r["device_id"], r["created_at"].isoformat(), r["jarak_cm"], r["gerakan"],
                    r["suhu_c"], r["kelembapan"], r["gas"], r["servo_deg"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sensor_readings.csv"})


@app.errorhandler(400)
@app.errorhandler(401)
def json_error(e):
    return jsonify(error=e.description), e.code


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT, debug=False)
