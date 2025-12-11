#!/usr/bin/env python3
"""
monitor.py
-----------
DASHBOARD MONITORING REAL-TIME
- Subscribe ke HiveMQ Cloud (topic "monitoring")
- Simpan data PC ke database MySQL (tabel pc_metrics)
- Simpan data HP (latest_info Firebase) ke database (tabel hp_metrics)
- Tampilkan grafik real-time di browser (Chart.js)
- Mode 1: Monitor PC
- Mode 2: Monitor HP + map lokasi + rute 5 jam terakhir (snap ke jalan via OSRM)

Jalankan:
    python monitor.py
Lalu buka browser ke: http://localhost:5000
"""

import ssl
import json
import threading
import datetime
from collections import deque

import pymysql
from pymysql.cursors import DictCursor
import paho.mqtt.client as mqtt
import requests
from flask import Flask, render_template, jsonify

from config import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_TOPIC,
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DB,
    FIREBASE_DB_URL,
    FIREBASE_LATEST_INFO_PATH,
)

# ============================================================
# MySQL HELPER
# ============================================================


def get_connection():
    """Buat koneksi baru ke MySQL."""
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        cursorclass=DictCursor,
        autocommit=False,
    )


def init_db():
    """
    Buat tabel pc_metrics & hp_metrics kalau belum ada.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # ---- Tabel PC ----
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pc_metrics (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    device_label    VARCHAR(64)  NOT NULL,
                    time_human      VARCHAR(32),
                    hostname        VARCHAR(255),
                    os              VARCHAR(255),
                    cpu_percent     FLOAT,
                    cpu_temp_c      FLOAT,
                    mem_percent     FLOAT,
                    disk_percent    FLOAT,
                    battery_percent FLOAT,
                    uptime_seconds  INT,
                    ip_addrs        VARCHAR(255),
                    wifi_ssid       VARCHAR(255),
                    raw_json        LONGTEXT,
                    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )

            # ---- Tabel HP ----
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS hp_metrics (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    updated_at_bigint BIGINT,
                    time_human        VARCHAR(32),
                    android_version   VARCHAR(16),
                    brand             VARCHAR(64),
                    model             VARCHAR(64),
                    product           VARCHAR(64),
                    chipset           VARCHAR(64),
                    sdk_level         INT,
                    battery_level     FLOAT,
                    device_temp_c     FLOAT,
                    ip_address        VARCHAR(64),
                    network_type      VARCHAR(64),
                    wifi_ssid         VARCHAR(64),
                    wifi_link_speed   VARCHAR(64),
                    available_ram_mb  INT,
                    total_ram_mb      INT,
                    avail_internal_storage_mb INT,
                    total_internal_storage_mb INT,
                    lat               DOUBLE,
                    lng               DOUBLE,
                    location_text     VARCHAR(255),
                    raw_json          LONGTEXT,
                    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )

        conn.commit()
        print("✅ Tabel pc_metrics & hp_metrics siap dipakai.")
    finally:
        conn.close()


def parse_metric_text(text: str, key: str):
    """
    Parse text seperti 'total=123 used=456 percent=12.3%'.
    Mengembalikan float atau None.
    """
    if not text:
        return None
    try:
        parts = text.split()
        for part in parts:
            if part.startswith(key + "="):
                value = part.split("=", 1)[1]
                if value.endswith("%"):
                    value = value[:-1]
                return float(value)
    except Exception:
        return None
    return None


def save_metric_to_db(payload: dict):
    """Simpan satu row metric ke tabel pc_metrics."""
    device_label = payload.get("device_label") or "unknown"
    mem_percent = parse_metric_text(payload.get("mem_usage"), "percent")
    disk_percent = parse_metric_text(payload.get("disk_root"), "percent")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pc_metrics (
                    device_label,
                    time_human,
                    hostname,
                    os,
                    cpu_percent,
                    cpu_temp_c,
                    mem_percent,
                    disk_percent,
                    battery_percent,
                    uptime_seconds,
                    ip_addrs,
                    wifi_ssid,
                    raw_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    device_label,
                    payload.get("time_human"),
                    payload.get("hostname"),
                    payload.get("os"),
                    payload.get("cpu_percent"),
                    payload.get("cpu_temp_c"),
                    mem_percent,
                    disk_percent,
                    payload.get("battery_percent"),
                    payload.get("uptime_seconds"),
                    payload.get("ip_addrs"),
                    payload.get("wifi_ssid"),
                    json.dumps(payload),
                ),
            )
        conn.commit()
    except Exception as e:
        print(f"❌ Gagal insert ke pc_metrics: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def load_recent_from_db(device_label: str, device_data, limit: int | None = None):
    """
    Load history terakhir dari DB ke memori (agar grafik punya data awal).
    Hanya ambil data 5 menit terakhir.
    """
    if limit is None:
        limit = device_data.max_points

    conn = get_connection()
    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    created_at,
                    cpu_percent,
                    mem_percent,
                    disk_percent,
                    cpu_temp_c,
                    battery_percent
                FROM pc_metrics
                WHERE device_label = %s
                  AND created_at >= NOW() - INTERVAL 5 MINUTE
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (device_label, limit),
            )
            rows = cur.fetchall()
    except Exception as e:
        print(f"⚠ Gagal load history dari DB untuk {device_label}: {e}")
    finally:
        conn.close()

    for row in rows:
        ts = row.get("created_at") or datetime.datetime.now()
        device_data.timestamps.append(ts)
        device_data.cpu_percent.append(row.get("cpu_percent"))
        device_data.memory_percent.append(row.get("mem_percent"))
        device_data.disk_percent.append(row.get("disk_percent"))
        device_data.cpu_temp.append(row.get("cpu_temp_c"))
        device_data.battery_percent.append(row.get("battery_percent"))

    if device_data.timestamps:
        device_data.last_update = device_data.timestamps[-1]


# ================= SIMPAN DATA HP KE DB =====================


def save_hp_to_db(info: dict):
    """
    Simpan data HP (latest_info) ke tabel hp_metrics.
    info di sini adalah dict yang dikembalikan fetch_latest_hp_info().
    """
    raw = info.get("raw") or {}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hp_metrics (
                    updated_at_bigint,
                    time_human,
                    android_version,
                    brand,
                    model,
                    product,
                    chipset,
                    sdk_level,
                    battery_level,
                    device_temp_c,
                    ip_address,
                    network_type,
                    wifi_ssid,
                    wifi_link_speed,
                    available_ram_mb,
                    total_ram_mb,
                    avail_internal_storage_mb,
                    total_internal_storage_mb,
                    lat,
                    lng,
                    location_text,
                    raw_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    raw.get("updated_at"),
                    info.get("time_human"),
                    raw.get("androidVersion"),
                    raw.get("brand"),
                    raw.get("model"),
                    raw.get("product"),
                    raw.get("chipset"),
                    raw.get("sdkLevel"),
                    raw.get("batteryLevel"),
                    raw.get("deviceTemperatureC"),
                    raw.get("ipAddress"),
                    raw.get("mobileNetworkType"),
                    raw.get("wifiSSID"),
                    raw.get("wifiLinkSpeed"),
                    raw.get("availableRamMb"),
                    raw.get("totalRamMb"),
                    raw.get("availableInternalStorageMb"),
                    raw.get("totalInternalStorageMb"),
                    raw.get("lat"),
                    raw.get("lng"),
                    raw.get("location_text"),
                    json.dumps(raw),
                ),
            )
        conn.commit()
    except Exception as e:
        print(f"❌ Gagal insert ke hp_metrics: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def get_hp_route(hours: int = 5, limit: int = 2000):
    """
    Ambil rute HP dari tabel hp_metrics untuk X jam terakhir.
    Hanya ambil row yang punya lat & lng, urut berdasarkan waktu.
    """
    conn = get_connection()
    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    lat,
                    lng,
                    time_human,
                    battery_level,
                    created_at
                FROM hp_metrics
                WHERE lat IS NOT NULL
                  AND lng IS NOT NULL
                  AND created_at >= NOW() - INTERVAL %s HOUR
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (hours, limit),
            )
            rows = cur.fetchall()
    except Exception as e:
        print(f"⚠ Gagal mengambil rute HP: {e}")
        return []
    finally:
        conn.close()

    route = []
    for row in rows:
        lat = row.get("lat")
        lng = row.get("lng")
        if lat is None or lng is None:
            continue

        time_human = row.get("time_human")
        if not time_human and row.get("created_at"):
            try:
                time_human = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                time_human = None

        route.append(
            {
                "id": row.get("id"),
                "lat": float(lat),
                "lng": float(lng),
                "time_human": time_human,
                "battery_level": row.get("battery_level"),
            }
        )
    return route


def snap_route_to_roads(route_points, sample_step: int = 5):
    """
    Gunakan OSRM 'route' API untuk membengkokkan rute ke jalan.
    - route_points: list dict {lat, lng, ...}
    - sample_step: ambil setiap N titik supaya request tidak terlalu panjang
    """
    if not route_points or len(route_points) < 2:
        return []

    # Ambil setiap sample_step titik + pastikan titik terakhir ikut
    sampled = route_points[::sample_step]
    if sampled[-1] is not route_points[-1]:
        sampled.append(route_points[-1])

    # OSRM butuh format: lon,lat;lon,lat;...
    coords_str = ";".join(f"{p['lng']},{p['lat']}" for p in sampled)

    url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}"
    params = {
        "overview": "full",
        "geometries": "geojson",
    }

    try:
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        routes = data.get("routes") or []
        if not routes:
            return []

        # GeoJSON coords: [lon, lat]
        coords = routes[0]["geometry"]["coordinates"]
        snapped = [{"lat": lat, "lng": lon} for (lon, lat) in coords]
        return snapped
    except Exception as e:
        print(f"⚠ Gagal snap rute ke jalan (OSRM): {e}")
        return []


# ============================================================
# IN-MEMORY STORAGE UNTUK PC
# ============================================================


class DeviceData:
    def __init__(self, max_points: int = 200):
        self.max_points = max_points
        self.timestamps = deque(maxlen=max_points)
        self.cpu_percent = deque(maxlen=max_points)
        self.memory_percent = deque(maxlen=max_points)
        self.disk_percent = deque(maxlen=max_points)
        self.cpu_temp = deque(maxlen=max_points)
        self.battery_percent = deque(maxlen=max_points)
        self.latest_data: dict = {}
        self.last_update: datetime.datetime | None = None


# Dict global: device_label -> DeviceData
devices: dict[str, DeviceData] = {}

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__, static_folder="static", template_folder="templates")

# ============================================================
# MQTT (PC METRICS)
# ============================================================


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ DASHBOARD: Terhubung ke broker MQTT")
        client.subscribe(MQTT_TOPIC)
        print(f"📡 Subscribe ke topic: {MQTT_TOPIC}")
    else:
        print(f"❌ DASHBOARD: Gagal koneksi MQTT. Kode: {rc}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception as e:
        print(f"❌ Gagal decode JSON MQTT: {e}")
        return

    device_label = payload.get("device_label") or "unknown"

    # Simpan ke DB
    save_metric_to_db(payload)

    # Siapkan object DeviceData untuk device ini
    if device_label not in devices:
        print(f"📥 Device baru terdeteksi: {device_label}")
        devices[device_label] = DeviceData(max_points=200)
        # Load history 5 menit terakhir dari DB supaya grafik ada data awal
        load_recent_from_db(device_label, devices[device_label], limit=200)

    device_data = devices[device_label]
    now = datetime.datetime.now()

    # Tambahkan ke history in-memory
    device_data.timestamps.append(now)
    device_data.cpu_percent.append(payload.get("cpu_percent"))
    mem_percent = parse_metric_text(payload.get("mem_usage"), "percent")
    device_data.memory_percent.append(mem_percent)
    disk_percent = parse_metric_text(payload.get("disk_root"), "percent")
    device_data.disk_percent.append(disk_percent)
    device_data.cpu_temp.append(payload.get("cpu_temp_c"))
    device_data.battery_percent.append(payload.get("battery_percent"))

    # Simpan data terbaru & waktu update
    device_data.latest_data = payload
    device_data.last_update = now

    print(
        f"📊 MQTT [{device_label}] "
        f"CPU={payload.get('cpu_percent')}% "
        f"MEM={mem_percent}% DISK={disk_percent}% "
        f"TEMP={payload.get('cpu_temp_c')}°C"
    )


def start_mqtt_client():
    """Jalankan MQTT client di background thread."""
    client = mqtt.Client(client_id="dashboard-monitor")
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_forever()
    except Exception as e:
        print(f"❌ MQTT connection failed: {e}")


# ============================================================
# FIREBASE HELPER (HP)
# ============================================================


def fetch_latest_hp_info():
    """
    Ambil latest_info HP dari Firebase Realtime Database via REST API.
    Struktur JSON contoh sudah sesuai yang kamu kirim.
    """
    base = FIREBASE_DB_URL.rstrip("/")
    path = FIREBASE_LATEST_INFO_PATH
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base}{path}.json"

    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        raw = resp.json() or {}
    except Exception as e:
        return {"ok": False, "error": f"Request error: {e}", "raw": None}

    if not raw:
        return {
            "ok": False,
            "error": "Node latest_info kosong atau belum ada data",
            "raw": raw,
        }

    lat = raw.get("lat")
    lng = raw.get("lng")
    battery = raw.get("batteryLevel")
    wifi_ssid = raw.get("wifiSSID")
    network_type = raw.get("mobileNetworkType")
    device_temp_c = raw.get("deviceTemperatureC")
    updated_at = raw.get("updated_at")
    ip_addr = raw.get("ipAddress")

    # Konversi updated_at -> time_human
    time_human = None    # noqa: N806
    if isinstance(updated_at, (int, float)):
        try:
            if updated_at > 1e12:  # anggap milidetik
                dt = datetime.datetime.fromtimestamp(updated_at / 1000.0)
            else:
                dt = datetime.datetime.fromtimestamp(updated_at)
            time_human = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_human = None

    return {
        "ok": True,
        "lat": lat,
        "lng": lng,
        "accuracy": None,  # belum ada di JSON HP
        "battery_percent": battery,
        "is_charging": None,
        "wifi_ssid": wifi_ssid,
        "network_type": network_type,
        "timestamp": updated_at,
        "time_human": time_human,
        "device_temp_c": device_temp_c,
        "ip_address": ip_addr,
        "android_version": raw.get("androidVersion"),
        "brand": raw.get("brand"),
        "model": raw.get("model"),
        "product": raw.get("product"),
        "chipset": raw.get("chipset"),
        "sdk_level": raw.get("sdkLevel"),
        "available_ram_mb": raw.get("availableRamMb"),
        "total_ram_mb": raw.get("totalRamMb"),
        "avail_internal_storage_mb": raw.get("availableInternalStorageMb"),
        "total_internal_storage_mb": raw.get("totalInternalStorageMb"),
        "wifi_link_speed": raw.get("wifiLinkSpeed"),
        "location_text": raw.get("location_text"),
        "raw": raw,
    }


# ============================================================
# FLASK ROUTES
# ============================================================


@app.route("/")
def index():
    """Halaman utama dashboard dengan 2 mode (PC & HP)."""
    return render_template("dashboard.html")


@app.route("/api/data")
def api_pc_data():
    """
    API untuk data monitoring PC (semua device).
    Hanya kirim history 5 menit terakhir.
    """
    now = datetime.datetime.now()
    window_secs = 5 * 60  # 5 menit

    result: dict[str, dict] = {}

    for device_name, device_data in devices.items():
        if not device_data.last_update:
            continue

        time_since_update = (now - device_data.last_update).total_seconds()
        is_online = time_since_update < 30

        ts_filtered = []
        cpu_filtered = []
        mem_filtered = []
        disk_filtered = []
        temp_filtered = []
        batt_filtered = []

        for ts, cpu, mem, disk, temp, batt in zip(
            device_data.timestamps,
            device_data.cpu_percent,
            device_data.memory_percent,
            device_data.disk_percent,
            device_data.cpu_temp,
            device_data.battery_percent,
        ):
            age = (now - ts).total_seconds()
            if age <= window_secs:
                ts_filtered.append(ts)
                cpu_filtered.append(cpu)
                mem_filtered.append(mem)
                disk_filtered.append(disk)
                temp_filtered.append(temp)
                batt_filtered.append(batt)

        result[device_name] = {
            "online": is_online,
            "last_update": device_data.last_update.strftime("%Y-%m-%d %H:%M:%S"),
            "time_since_update": int(time_since_update),
            "latest": device_data.latest_data,
            "history": {
                "timestamps": [t.strftime("%H:%M:%S") for t in ts_filtered],
                "cpu_percent": cpu_filtered,
                "memory_percent": mem_filtered,
                "disk_percent": disk_filtered,
                "cpu_temp": temp_filtered,
                "battery_percent": batt_filtered,
            },
        }

    return jsonify(result)


@app.route("/api/status")
def api_status():
    """API status sederhana untuk setiap device PC."""
    now = datetime.datetime.now()
    status_info = {}
    for device_name, device_data in devices.items():
        if device_data.last_update:
            time_since_update = (now - device_data.last_update).total_seconds()
            status_info[device_name] = {
                "online": time_since_update < 30,
                "last_seen": device_data.last_update.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
    return jsonify(status_info)


@app.route("/api/hp-latest")
def api_hp_latest():
    """
    API untuk monitoring HP dari Firebase (latest_info)
    + rute 5 jam terakhir dari database hp_metrics.
    Rute disnap ke jalan menggunakan OSRM.
    """
    info = fetch_latest_hp_info()

    # kalau sukses ambil dari Firebase → simpan ke DB
    if info.get("ok"):
        save_hp_to_db(info)

    # Ambil rute 5 jam terakhir dari DB
    route_points = get_hp_route(hours=5)
    snapped_route = snap_route_to_roads(route_points, sample_step=5)

    info["route"] = route_points                # titik mentah dari DB
    info["snapped_route"] = snapped_route       # titik yang sudah ngikutin jalan
    info["route_hours"] = 5
    info["route_point_count"] = len(route_points)
    info["snapped_point_count"] = len(snapped_route)

    # selalu 200 supaya fetch() di JS tidak meledak
    return jsonify(info), 200


# ============================================================
# ENTRY POINT
# ============================================================


def main():
    print("🔧 Inisialisasi database...")
    init_db()

    print("🚀 Menjalankan MQTT client di background thread...")
    mqtt_thread = threading.Thread(target=start_mqtt_client, daemon=True)
    mqtt_thread.start()

    print("🌐 Menjalankan Flask server di http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
