# Smart Gate Monitoring: ESP32 → Flask → PostgreSQL → Streamlit

```
ESP32 --WiFi, HTTP POST JSON--> Flask (app.py) --> PostgreSQL --> Dashboard Streamlit + notebook
```

## Isi proyek
| File | Fungsi |
|---|---|
| `sensor_clear/sensor_clear.ino` | Kode ESP32: baca sensor, gerakkan servo, kirim data tiap 1 detik |
| `app.py` | Server Flask: terima data dari ESP32, simpan ke PostgreSQL, sediakan API |
| `streamlit_app.py` | **Dashboard Streamlit** (realtime, membaca langsung dari PostgreSQL) |
| `setup_db.py` | Membuat database dan tabel (tanpa Docker) |
| `schema.sql` | Tabel `sensor_readings` dan `sensor_events` |
| `analisis_sensor.ipynb` | Analisis data dengan pandas dan matplotlib |
| `seed_demo.py` | Mengisi data contoh tanpa ESP32 |
| `templates/dashboard.html` | Dashboard HTML ringan (opsional), dibuka di `http://localhost:5000` |

## Langkah menjalankan (tanpa Docker)

### 1. Pasang PostgreSQL
- **Windows**: unduh installer dari postgresql.org, ingat password user `postgres` yang Anda buat saat instalasi.
- **macOS**: `brew install postgresql@16` lalu `brew services start postgresql@16`. Dengan Homebrew, user-nya adalah nama akun macOS Anda tanpa password, jadi `DATABASE_URL` menjadi `postgresql://NAMA_USER@localhost:5432/sensor_iot`.
- **Linux (Ubuntu/Debian)**: `sudo apt install postgresql`, lalu atur password: `sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"`.

### 2. Siapkan proyek
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # lalu isi DATABASE_URL dan API_KEY (Windows: copy)
python setup_db.py                                    # membuat database sensor_iot dan tabelnya
```

### 3. Jalankan server dan dashboard (dua terminal)
```bash
python app.py                      # terminal 1: server Flask di port 5000
streamlit run streamlit_app.py     # terminal 2: dashboard di http://localhost:8501
```

### 4. ESP32
Buka `sensor_clear/sensor_clear.ino`, ubah `WIFI_SSID`, `WIFI_PASS`, `SERVER_URL` (IP komputer, bukan `localhost`), dan `API_KEY` (sama dengan `.env`), lalu upload.
Jika Windows Firewall bertanya, izinkan Python menerima koneksi di jaringan pribadi. Di macOS, port 5000 sering dipakai AirPlay: isi `PORT=5001` di `.env` dan samakan `SERVER_URL`.

### 5. Notebook
`jupyter notebook analisis_sensor.ipynb`, lalu Run All.

Tanpa ESP32: `python seed_demo.py --device esp32-01` mengisi 6 jam data contoh, lalu pilih perangkat itu di dashboard.

## Dashboard Streamlit
- **Realtime**: bagian live memakai `st.fragment(run_every=...)`, diperbarui otomatis tiap 1 sampai 10 detik (atur di sidebar, bisa dijeda). Halaman tidak perlu di-refresh.
- **Status koneksi**: hijau bila data terakhir kurang dari 10 detik, merah bila alat berhenti mengirim. Umur data dihitung oleh server database, jadi tidak terpengaruh jam komputer.
- **Peringatan otomatis**: gas terdeteksi, suhu panas, kelembapan di luar batas, atau objek dekat (servo membuka). Semua batas dapat diubah di sidebar.
- **Isi**: kartu nilai terkini, ringkasan kejadian per rentang waktu, grafik (jarak dengan garis batas 20 cm, suhu, kelembapan, gerakan, gas), log kejadian, data mentah, dan unduh CSV.
- Butuh Streamlit 1.37 atau lebih baru.

## API Flask
| Endpoint | Keterangan |
|---|---|
| `POST /api/data` | Terima pembacaan (header `X-API-Key`). Kejadian gerakan, gas, servo dicatat otomatis |
| `GET /api/latest` | Pembacaan terbaru |
| `GET /api/history?minutes=60&limit=500` | Riwayat |
| `GET /api/events?limit=40` | Log kejadian |
| `GET /api/stats?hours=24` | Ringkasan (rata-rata, min, maks, jumlah) |
| `GET /api/export.csv?hours=24` | Unduh CSV |
| `GET /health` | Cek server dan database |

Semua GET menerima `device_id` (default `esp32-01`).

Contoh kiriman manual:
```bash
curl -X POST http://localhost:5000/api/data -H "Content-Type: application/json" -H "X-API-Key: ganti-dengan-kunci-rahasia" \
  -d '{"device_id":"esp32-01","jarak_cm":15.2,"gerakan":true,"suhu_c":28.5,"kelembapan":61,"gas":false,"servo_deg":90}'
```

## Catatan
- `jarak_cm` bernilai `NULL` bila tidak ada pantulan ultrasonik. `gerakan` dan `gas` memakai latch sehingga pulsa singkat di antara dua pengiriman tidak terlewat. Pengiriman WiFi berjalan di tugas terpisah sehingga servo tetap responsif.
- Data tumbuh sekitar 86 ribu baris per hari per perangkat. Perintah hapus data lama ada di bagian bawah `schema.sql`.
- API GET dan dashboard tidak memakai login. Untuk jaringan terbuka, tambahkan autentikasi atau taruh di belakang reverse proxy.
- Jika `setup_db.py` gagal terhubung, cek bahwa layanan PostgreSQL berjalan dan user/password di `DATABASE_URL` benar.
