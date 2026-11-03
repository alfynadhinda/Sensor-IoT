-- Skema database PostgreSQL untuk proyek sensor ESP32
-- Dijalankan otomatis oleh app.py saat start (aman dijalankan berulang).

CREATE TABLE IF NOT EXISTS sensor_readings (
    id          BIGSERIAL PRIMARY KEY,
    device_id   TEXT        NOT NULL DEFAULT 'esp32-01',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    jarak_cm    REAL,                         -- NULL = tidak ada pantulan ultrasonik
    gerakan     BOOLEAN     NOT NULL DEFAULT FALSE,
    suhu_c      REAL,
    kelembapan  REAL,
    gas         BOOLEAN     NOT NULL DEFAULT FALSE,
    servo_deg   SMALLINT    NOT NULL DEFAULT 0,
    CONSTRAINT chk_kelembapan CHECK (kelembapan IS NULL OR kelembapan BETWEEN 0 AND 100),
    CONSTRAINT chk_jarak      CHECK (jarak_cm   IS NULL OR jarak_cm   BETWEEN 0 AND 500),
    CONSTRAINT chk_servo      CHECK (servo_deg  BETWEEN 0 AND 180)
);

CREATE INDEX IF NOT EXISTS idx_readings_device_time
    ON sensor_readings (device_id, created_at DESC);

CREATE TABLE IF NOT EXISTS sensor_events (
    id          BIGSERIAL PRIMARY KEY,
    device_id   TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    jenis       TEXT        NOT NULL,         -- gerakan_mulai, gerakan_berhenti, gas_terdeteksi, gas_normal, servo_buka, servo_tutup
    level       TEXT        NOT NULL,         -- good | warn | crit | neutral (dipakai warna di dashboard)
    keterangan  TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_device_time
    ON sensor_events (device_id, created_at DESC);

-- Opsional: hapus data lama (mis. simpan 30 hari)
-- DELETE FROM sensor_readings WHERE created_at < now() - interval '30 days';
-- DELETE FROM sensor_events   WHERE created_at < now() - interval '30 days';
