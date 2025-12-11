# Dashboard Monitoring PC & HP (MQTT + MySQL + Firebase)

Project ini berisi:

- `agent.py` → jalan di laptop Windows, kirim data ke MQTT HiveMQ Cloud.
- `monitor.py` → server Flask:
  - Subscribe topic MQTT `monitoring`.
  - Simpan data PC ke MySQL (tabel `pc_metrics`).
  - Tampilkan dashboard PC (Chart.js).
  - Tampilkan monitoring HP dari Firebase Realtime Database (`latest_info`) + maps (Leaflet).

## 1. Persiapan Python

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Konfigurasi MySQL

Masuk ke MySQL / MariaDB, buat database:

```sql
CREATE DATABASE monitoring_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

Kalau username / password / nama DB kamu beda, edit di `config.py`:

```python
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = ""
MYSQL_DB = "monitoring_db"
```

`monitor.py` akan otomatis membuat tabel `pc_metrics` saat pertama kali dijalankan.

## 3. Konfigurasi MQTT & Firebase

Di `config.py` sudah diisi:

- HiveMQ Cloud:
  - `MQTT_BROKER`
  - `MQTT_PORT`
  - `MQTT_USERNAME`
  - `MQTT_PASSWORD`
  - `MQTT_TOPIC = "monitoring"`

- Firebase Realtime Database:
  - `FIREBASE_DB_URL`
  - `FIREBASE_USER_ID`
  - `FIREBASE_LATEST_INFO_PATH = "/users/<UID>/latest_info"`

Kalau UID atau URL berubah, tinggal edit di `config.py`.

Struktur JSON `latest_info` di Firebase kamu bebas,
tapi script mengharapkan kira-kira seperti ini:

```json
{
  "time_human": "2025-12-02 10:15:00",
  "timestamp": 1701500100,
  "battery": 87,
  "is_charging": true,
  "wifi_ssid": "WIFI-RUMAH",
  "network_type": "WIFI",
  "location": {
    "lat": -6.30,
    "lng": 107.17,
    "accuracy": 12.5
  }
}
```

Key bisa kamu sesuaikan (script sudah mencoba beberapa variasi nama).

## 4. Menjalankan Dashboard (monitor.py)

```bash
python monitor.py
```

Output:

- MQTT client jalan di background thread.
- Flask jalan di `http://localhost:5000`.

Buka browser ke `http://localhost:5000`:

- Mode `Monitor PC` → grafik CPU, RAM, Disk, Temp, Battery per laptop (berdasarkan `device_label`).
- Mode `Monitor HP` → baca `latest_info` dari Firebase + tampilkan lokasi di maps.

## 5. Menjalankan Agent di Laptop (agent.py)

Di laptop Windows yang ingin dimonitor:

```bash
python agent.py
```

Pastikan:

- `device_label` di `agent.py` kamu set misalnya `"laptop-rumah"` atau `"laptop-kampus"`.
- Agent bisa konek ke HiveMQ Cloud (cek username/password broker).

Tiap 3 detik, agent akan:

- Kumpulkan metrics via `psutil` (CPU, RAM, Disk, Battery, Network, Uptime).
- Coba baca suhu CPU (WMI / PowerShell / estimasi).
- Kirim JSON ke topic MQTT `monitoring`.

Dashboard (`monitor.py`) akan:

- Menerima pesan MQTT.
- Simpan ke MySQL (tabel `pc_metrics`).
- Update grafik & card di halaman Monitor PC.

## 6. Mode Monitor HP

Di mode `Monitor HP`:

- Frontend akan call `/api/hp-latest` setiap 5 detik.
- `monitor.py` akan GET data dari Firebase:
  - URL: `FIREBASE_DB_URL + FIREBASE_LATEST_INFO_PATH + ".json"`
- Jika ada field `location.lat` dan `location.lng`, maps akan fokus ke koordinat tersebut.

## 7. Kustomisasi

- Tambah device lain: cukup jalankan `agent.py` di laptop lain dan ganti `DEVICE_LABEL`.
- Ubah tampilan UI: edit `static/css/style.css` atau `static/js/dashboard.js`.
- Tambah grafik / metrik lain: lengkapi di `agent.py` + proses di `monitor.py` + render di frontend.

---

Folder ini sudah siap kamu download & jalankan:
- `monitor.py` → server dashboard
- `agent.py` → agent di laptop
- `templates/dashboard.html`
- `static/css/style.css`
- `static/js/dashboard.js`
- `config.py`, `requirements.txt`, `README.md`
