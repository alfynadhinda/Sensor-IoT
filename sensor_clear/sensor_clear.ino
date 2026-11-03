/*
 * sensor_clear.ino  (versi IoT)
 * ESP32 membaca HC-SR04, PIR, DHT11, MQ135 (DO) dan menggerakkan servo,
 * lalu mengirim hasilnya ke server Flask (disimpan di PostgreSQL) lewat WiFi.
 *
 * Library yang dibutuhkan (Library Manager):
 *   - ESP32Servo
 *   - DHT sensor library (Adafruit) + Adafruit Unified Sensor
 * WiFi.h dan HTTPClient.h sudah ikut paket board ESP32.
 */
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>
#include <DHT.h>

// ====== KONFIGURASI: ubah sesuai jaringan Anda ======
const char* WIFI_SSID   = "HOMESPACE";
const char* WIFI_PASS   = "1sampai9";
// IP komputer yang menjalankan Flask (cek dengan ipconfig / ip a), bukan "localhost"
const char* SERVER_URL  = "http://192.168.1.5:5000/api/data";
const char* API_KEY     = "rahasia";   // harus sama dengan API_KEY di .env
const char* DEVICE_ID   = "esp32-01";
const unsigned long INTERVAL_KIRIM_MS = 1000;             // kirim data tiap 1 detik

// ====== PIN ======
#define TRIG 5
#define ECHO 18
#define SERVO_PIN 25
#define PIR 19
#define DHTPIN 4
#define DHTTYPE DHT11   // ganti DHT22 jika sensor Anda DHT22
#define MQ135_DO 34

Servo servo;
DHT dht(DHTPIN, DHTTYPE);

// ====== DATA BERSAMA (diisi loop(), dibaca tugas pengirim) ======
struct Data {
  float jarak = NAN;      // cm, NAN jika tidak ada pantulan
  float suhu = NAN;       // C
  float lembap = NAN;     // %
  bool  gerakan = false;  // latch: true jika PIR sempat HIGH sejak pengiriman terakhir
  bool  gas = false;      // latch: true jika MQ135 sempat mendeteksi gas
  int   servoDeg = 0;
};
Data data;
SemaphoreHandle_t mutexData;

unsigned long terakhirDHT = 0;

// ---------- WiFi ----------
void sambungWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("Menyambung WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long mulai = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - mulai < 15000) {
    vTaskDelay(pdMS_TO_TICKS(500));
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("\nWiFi tersambung, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi gagal, coba lagi nanti");
  }
}

// Angka desimal, atau "null" jika NAN (agar JSON tetap valid)
void tulisFloat(char* buf, size_t n, float v) {
  if (isnan(v)) snprintf(buf, n, "null");
  else snprintf(buf, n, "%.2f", v);
}

// ---------- Tugas pengirim (berjalan terpisah agar servo tidak ikut tertahan) ----------
void kirimTask(void* param) {
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(INTERVAL_KIRIM_MS));

    Data d;
    xSemaphoreTake(mutexData, portMAX_DELAY);
    d = data;
    data.gerakan = false;   // reset latch setelah disalin
    data.gas = false;
    xSemaphoreGive(mutexData);

    sambungWiFi();
    if (WiFi.status() != WL_CONNECTED) continue;

    char jarak[16], suhu[16], lembap[16];
    tulisFloat(jarak, sizeof(jarak), d.jarak);
    tulisFloat(suhu, sizeof(suhu), d.suhu);
    tulisFloat(lembap, sizeof(lembap), d.lembap);

    char body[256];
    snprintf(body, sizeof(body),
             "{\"device_id\":\"%s\",\"jarak_cm\":%s,\"gerakan\":%s,\"suhu_c\":%s,"
             "\"kelembapan\":%s,\"gas\":%s,\"servo_deg\":%d}",
             DEVICE_ID, jarak, d.gerakan ? "true" : "false", suhu, lembap,
             d.gas ? "true" : "false", d.servoDeg);

    HTTPClient http;
    http.setTimeout(3000);
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-API-Key", API_KEY);
    int kode = http.POST(body);
    if (kode == 201 || kode == 200) {
      Serial.printf("[kirim] OK (%d)\n", kode);
    } else {
      Serial.printf("[kirim] GAGAL kode=%d\n", kode);
    }
    http.end();
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG, OUTPUT);
  pinMode(ECHO, INPUT);
  pinMode(PIR, INPUT);
  pinMode(MQ135_DO, INPUT);
  dht.begin();
  servo.setPeriodHertz(50);
  servo.attach(SERVO_PIN, 500, 2400);
  servo.write(0);

  mutexData = xSemaphoreCreateMutex();
  sambungWiFi();
  xTaskCreatePinnedToCore(kirimTask, "kirim", 8192, NULL, 1, NULL, 0);
}

void loop() {
  // --- HC-SR04 ---
  digitalWrite(TRIG, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG, LOW);

  long durasi = pulseIn(ECHO, HIGH, 30000);
  float jarak = (durasi > 0) ? durasi * 0.0343 / 2 : NAN;   // cm

  // --- Servo bergerak berdasarkan jarak ---
  int sudut = (!isnan(jarak) && jarak < 20) ? 90 : 0;
  servo.write(sudut);

  // --- DHT dibaca tiap 2 detik ---
  float t = NAN, h = NAN;
  bool dhtBaru = false;
  if (millis() - terakhirDHT >= 2000) {
    terakhirDHT = millis();
    t = dht.readTemperature();
    h = dht.readHumidity();
    if (!isnan(t) && !isnan(h)) dhtBaru = true;
    else Serial.println("Gagal membaca DHT");
  }

  bool pir = digitalRead(PIR) == HIGH;
  bool gas = digitalRead(MQ135_DO) == LOW;   // LOW = gas terdeteksi (tukar jika modul Anda kebalikannya)

  // --- Simpan ke data bersama ---
  xSemaphoreTake(mutexData, portMAX_DELAY);
  data.jarak = jarak;
  data.servoDeg = sudut;
  if (dhtBaru) { data.suhu = t; data.lembap = h; }
  data.gerakan = data.gerakan || pir;
  data.gas = data.gas || gas;
  xSemaphoreGive(mutexData);

  // --- Tampilkan ke Serial Monitor (format sama seperti kode asli) ---
  Serial.print("Jarak: ");
  Serial.print(isnan(jarak) ? 0.0 : jarak);
  Serial.print(" cm | ");
  Serial.print(pir ? "Ada gerakan" : "Tidak ada gerakan");
  Serial.print(" | Suhu: ");
  Serial.print(data.suhu);
  Serial.print(" C | Kelembapan: ");
  Serial.print(data.lembap);
  Serial.print(" % | ");
  Serial.println(gas ? "Gas terdeteksi" : "Udara normal");

  delay(100);
}
