"""
Dashboard Streamlit untuk Smart Gate Monitoring (ESP32 -> Flask -> PostgreSQL -> Streamlit).

Dashboard membaca langsung dari PostgreSQL dan memperbarui bagian live-nya otomatis
(st.fragment dengan run_every), jadi halaman tidak perlu di-refresh manual.

Jalankan:
    streamlit run streamlit_app.py
"""
import os

import altair as alt
import pandas as pd
import psycopg2
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/sensor_iot")
TZ = os.getenv("DASHBOARD_TZ", "Asia/Jakarta")
OFFLINE_AFTER_S = 10          # data lebih lama dari ini dianggap alat tidak terhubung

C_JARAK, C_SUHU, C_LEMBAP, C_BAHAYA, C_GERAK, C_GAS = "#2f6fb5", "#c75f17", "#12806f", "#b3261e", "#7b4fd6", "#b3261e"

st.set_page_config(page_title="Smart Gate Monitoring", page_icon="🚪", layout="wide")


# ----------------------------------------------------------------------------
# Akses database
# ----------------------------------------------------------------------------
@st.cache_resource
def _connect():
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
    conn.autocommit = True
    return conn


def query(sql, params=()):
    """Jalankan SELECT dan kembalikan DataFrame. Sambung ulang sekali bila koneksi putus."""
    for attempt in (1, 2):
        try:
            conn = _connect()
            if conn.closed:
                raise psycopg2.InterfaceError("koneksi tertutup")
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return pd.DataFrame(cur.fetchall(), columns=cols)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            _connect.clear()
            if attempt == 2:
                raise


@st.cache_data(ttl=30)
def daftar_perangkat():
    df = query("SELECT DISTINCT device_id FROM sensor_readings ORDER BY device_id")
    return df["device_id"].tolist()


def ambil_riwayat(device, menit):
    return query(
        """
        SELECT * FROM (
            SELECT created_at, jarak_cm, gerakan, suhu_c, kelembapan, gas, servo_deg
            FROM sensor_readings
            WHERE device_id = %s AND created_at >= now() - make_interval(mins => %s)
            ORDER BY created_at DESC LIMIT 10000
        ) t ORDER BY created_at
        """, (device, menit))


def ambil_umur_data(device):
    """Selisih detik antara sekarang dan data terakhir, dihitung oleh server database."""
    df = query("SELECT extract(epoch FROM (now() - max(created_at))) AS umur FROM sensor_readings WHERE device_id = %s", (device,))
    v = df["umur"].iloc[0]
    return None if pd.isna(v) else float(v)


def ambil_kejadian(device, limit=30):
    return query("SELECT created_at, level, keterangan FROM sensor_events WHERE device_id = %s ORDER BY id DESC LIMIT %s", (device, limit))


def hitung_kejadian(device, menit):
    df = query(
        "SELECT jenis, count(*) AS n FROM sensor_events "
        "WHERE device_id = %s AND created_at >= now() - make_interval(mins => %s) GROUP BY jenis", (device, menit))
    return {r.jenis: int(r.n) for r in df.itertuples()}


# ----------------------------------------------------------------------------
# Pembantu tampilan
# ----------------------------------------------------------------------------
def waktu_lokal(serie):
    """UTC dari database -> jam dinding zona waktu dashboard (tanpa info zona, agar grafik menampilkan jam yang sama)."""
    return pd.to_datetime(serie, utc=True).dt.tz_convert(TZ).dt.tz_localize(None)


def label_menit(m):
    if m < 60:
        return f"{m} menit"
    if m % 60 == 0:
        return f"{m // 60} jam"
    return f"{m} menit"


def label_umur(detik):
    detik = int(detik)
    if detik < 60:
        return f"{detik} detik"
    if detik < 3600:
        return f"{detik // 60} menit"
    if detik < 86400:
        return f"{detik // 3600} jam"
    return f"{detik // 86400} hari"


def nilai_terakhir(df, kolom):
    s = df[kolom].dropna()
    return None if s.empty else float(s.iloc[-1])


def sumbu_waktu():
    return alt.X("waktu:T", title=None, axis=alt.Axis(format="%H:%M:%S", labelOverlap=True, grid=False))


def grafik_garis(df, kolom, judul_y, warna, domain=None, garis_batas=None, tinggi=230):
    tip = [alt.Tooltip("waktu:T", title="Waktu", format="%H:%M:%S"), alt.Tooltip(f"{kolom}:Q", title=judul_y, format=".1f")]
    skala = alt.Scale(domain=domain) if domain else alt.Scale(zero=False)
    garis = alt.Chart(df).mark_line(color=warna, strokeWidth=2).encode(
        x=sumbu_waktu(), y=alt.Y(f"{kolom}:Q", title=judul_y, scale=skala), tooltip=tip)
    lapis = [garis]
    akhir = df.dropna(subset=[kolom]).tail(1)
    if len(akhir):
        lapis.append(alt.Chart(akhir).mark_point(color=warna, filled=True, size=80, opacity=1).encode(x="waktu:T", y=f"{kolom}:Q"))
    if garis_batas is not None:
        lapis.append(alt.Chart(pd.DataFrame({"batas": [garis_batas]})).mark_rule(color=C_BAHAYA, strokeDash=[5, 4]).encode(y="batas:Q"))
    return alt.layer(*lapis).properties(height=tinggi, width="container")


def grafik_biner(df, kolom, judul, warna):
    d = df[["waktu", kolom]].copy()
    d["nilai"] = d[kolom].astype(int)
    return alt.Chart(d).mark_area(interpolate="step-after", color=warna, opacity=0.35, line={"color": warna}).encode(
        x=sumbu_waktu(),
        y=alt.Y("nilai:Q", title=None, scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(values=[0, 1], labelExpr="datum.value == 1 ? 'Ya' : 'Tidak'")),
        tooltip=[alt.Tooltip("waktu:T", title="Waktu", format="%H:%M:%S"), alt.Tooltip("nilai:Q", title=judul)],
    ).properties(height=110, width="container", title=judul)


# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
st.title("🚪 Smart Gate Monitoring")
st.caption("ESP32 → Flask → PostgreSQL → Streamlit · pintu atau palang otomatis dengan monitoring lingkungan")

try:
    perangkat = daftar_perangkat() or ["esp32-01"]
except Exception as e:   # noqa: BLE001
    st.error(f"Tidak bisa terhubung ke PostgreSQL: {e}")
    st.info("Pastikan PostgreSQL berjalan, DATABASE_URL di file .env benar, dan sudah menjalankan `python setup_db.py`.")
    st.stop()

with st.sidebar:
    st.header("Pengaturan")
    device = st.selectbox("Perangkat", perangkat)
    menit = st.select_slider("Rentang data", options=[5, 15, 30, 60, 180, 720, 1440], value=15, format_func=label_menit)
    live = st.checkbox("Perbarui otomatis", value=True)
    interval = st.slider("Periksa data baru tiap (detik)", 1, 10, 2, disabled=not live)
    st.subheader("Batas peringatan")
    batas_jarak = st.number_input("Servo membuka bila jarak di bawah (cm)", 5, 100, 20)
    suhu_hangat = st.number_input("Suhu hangat mulai (°C)", 20, 50, 30)
    suhu_panas = st.number_input("Suhu panas mulai (°C)", 20, 60, 35)
    lembap_tinggi = st.number_input("Kelembapan tinggi mulai (%)", 50, 100, 80)
    lembap_rendah = st.number_input("Kelembapan rendah di bawah (%)", 0, 50, 30)
    st.caption(f"Zona waktu: {TZ}")


# ----------------------------------------------------------------------------
# Bagian live (diperbarui otomatis)
# ----------------------------------------------------------------------------
@st.fragment(run_every=float(interval) if live else None)
def tampilan_live():
    try:
        df = ambil_riwayat(device, menit)
        umur = ambil_umur_data(device)
        kejadian = ambil_kejadian(device)
        hitung = hitung_kejadian(device, menit)
    except Exception as e:   # noqa: BLE001
        st.error(f"Gagal membaca database: {e}")
        return

    if df.empty:
        if umur is None:
            st.info("Belum ada data untuk perangkat ini. Nyalakan ESP32 atau jalankan `python seed_demo.py`.")
        else:
            st.warning(f"Tidak ada data dalam {label_menit(menit)} terakhir. Data terakhir {label_umur(umur)} lalu. Perbesar rentang data di sidebar.")
        return

    df["waktu"] = waktu_lokal(df["created_at"])
    akhir = df.iloc[-1]
    jarak = None if pd.isna(akhir["jarak_cm"]) else float(akhir["jarak_cm"])
    suhu, lembap = nilai_terakhir(df, "suhu_c"), nilai_terakhir(df, "kelembapan")
    gas, gerak, servo = bool(akhir["gas"]), bool(akhir["gerakan"]), int(akhir["servo_deg"])

    # Status koneksi
    if umur is not None and umur <= OFFLINE_AFTER_S:
        st.success(f"🟢 Alat terhubung · data terakhir {label_umur(umur)} lalu · diperbarui {akhir['waktu']:%H:%M:%S}")
    else:
        st.warning(f"🔴 Tidak ada data baru selama {label_umur(umur)} · nilai di bawah adalah pembacaan terakhir ({akhir['waktu']:%d %b %H:%M:%S})")

    # Peringatan otomatis
    masalah = []
    if gas:
        masalah.append("gas terdeteksi, periksa ventilasi")
    if suhu is not None and suhu >= suhu_panas:
        masalah.append(f"suhu panas ({suhu:.1f} °C)")
    if lembap is not None and (lembap >= lembap_tinggi or lembap < lembap_rendah):
        masalah.append(f"kelembapan di luar batas ({lembap:.0f}%)")
    if masalah:
        st.error("⚠️ Peringatan: " + "; ".join(masalah) + ".")
    elif jarak is not None and jarak < batas_jarak:
        st.warning(f"🚪 Objek berada {jarak:.1f} cm dari sensor, pintu atau palang terbuka.")
    else:
        st.success("✅ Kondisi normal.")

    # Kartu nilai
    k = st.columns(6)
    k[0].metric("Jarak", "Tidak ada pantulan" if jarak is None else f"{jarak:.1f} cm")
    k[1].metric("Pintu / servo", "Terbuka" if servo > 0 else "Tertutup", f"{servo}°", delta_color="off")
    k[2].metric("Gerakan", "Terdeteksi" if gerak else "Tidak ada")
    if suhu is not None:
        serie = df["suhu_c"].dropna()
        selisih = suhu - float(serie.iloc[0])
        status = "panas" if suhu >= suhu_panas else "hangat" if suhu >= suhu_hangat else "normal"
        k[3].metric("Suhu", f"{suhu:.1f} °C", f"{selisih:+.1f} °C ({status})", delta_color="off")
    else:
        k[3].metric("Suhu", "--")
    k[4].metric("Kelembapan", "--" if lembap is None else f"{lembap:.1f} %")
    k[5].metric("Kualitas udara", "Gas terdeteksi" if gas else "Normal")

    # Ringkasan rentang
    st.markdown(f"**Dalam {label_menit(menit)} terakhir**")
    r = st.columns(4)
    r[0].metric("Orang mendekat", hitung.get("gerakan_mulai", 0))
    r[1].metric("Pintu terbuka", hitung.get("servo_buka", 0), help="Berapa kali servo membuka")
    r[2].metric("Kejadian gas", hitung.get("gas_terdeteksi", 0))
    r[3].metric("Jumlah data", f"{len(df):,}")

    # Grafik
    t1, t2, t3 = st.tabs(["Jarak & servo", "Suhu & kelembapan", "Gerakan & gas"])
    with t1:
        atas = max(100.0, float(df["jarak_cm"].max()) if df["jarak_cm"].notna().any() else 100.0)
        st.altair_chart(grafik_garis(df, "jarak_cm", "Jarak (cm)", C_JARAK, domain=[0, atas], garis_batas=batas_jarak))
        st.caption(f"Garis putus-putus: batas {batas_jarak} cm. Di bawah garis, servo membuka. Garis terputus berarti tidak ada pantulan ultrasonik.")
    with t2:
        c1, c2 = st.columns(2)
        c1.altair_chart(grafik_garis(df, "suhu_c", "Suhu (°C)", C_SUHU))
        c2.altair_chart(grafik_garis(df, "kelembapan", "Kelembapan (%)", C_LEMBAP))
    with t3:
        st.altair_chart(grafik_biner(df, "gerakan", "Gerakan (PIR)", C_GERAK))
        st.altair_chart(grafik_biner(df, "gas", "Gas (MQ-135)", C_GAS))

    # Log kejadian dan data mentah
    kiri, kanan = st.columns([1, 1])
    with kiri:
        st.subheader("Log kejadian")
        if kejadian.empty:
            st.caption("Belum ada kejadian.")
        else:
            ikon = {"good": "🟢", "warn": "🟡", "crit": "🔴", "neutral": "⚪"}
            tabel = pd.DataFrame({
                "Waktu": waktu_lokal(kejadian["created_at"]).dt.strftime("%H:%M:%S"),
                "Kejadian": kejadian["level"].map(ikon).fillna("⚪") + " " + kejadian["keterangan"],
            })
            st.dataframe(tabel, hide_index=True)
    with kanan:
        st.subheader("Data mentah")
        mentah = df.sort_values("waktu", ascending=False).head(200)[["waktu", "jarak_cm", "gerakan", "suhu_c", "kelembapan", "gas", "servo_deg"]]
        st.dataframe(mentah, hide_index=True)
        st.download_button("Unduh CSV (rentang terpilih)", df.drop(columns=["created_at"]).to_csv(index=False).encode("utf-8"),
                           file_name=f"sensor_{device}_{menit}menit.csv", mime="text/csv")


tampilan_live()
