#!/usr/bin/env python3
"""
agent.py
--------
AGENT MONITORING (Windows) - KIRIM METRIK KE MQTT (HiveMQ Cloud)

- Jalan di background di laptop yang ingin dimonitor.
- Mengirim data CPU, Memory, Disk, Battery, Network, dan estimasi suhu CPU.

Jalankan:
    python agent.py
"""

import json
import logging
import os
import sys
import time
import socket
import platform
import subprocess

import psutil
import paho.mqtt.client as mqtt
import ssl

from config import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_TOPIC,
)

# === Label perangkat (ganti sesuai kebutuhan) ===
DEVICE_LABEL = "laptop-rumah"

# === Konfigurasi logging sederhana ===
log_path = os.path.expanduser("~/mqtt_agent_windows.log")
logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ================== Helper Functions ==================

def format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"


def get_wifi_ssid():
    """Ambil nama SSID WiFi (khusus Windows)."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.check_output(
            ["netsh", "wlan", "show", "interfaces"],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        for line in out.splitlines():
            line = line.strip()
            lower = line.lower()
            if lower.startswith("ssid") and "bssid" not in lower:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    ssid = parts[1].strip()
                    if ssid:
                        return ssid
        return None
    except Exception:
        return None


def get_cpu_temperature_windows():
    """Coba beberapa metode untuk membaca suhu CPU di Windows.

    Kalau semua gagal, akan mengembalikan None.
    """
    temp = None

    # Method 1: psutil.sensors_temperatures (sering tidak tersedia di Windows)
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                for entry in entries:
                    if hasattr(entry, "current") and entry.current is not None:
                        t = float(entry.current)
                        if 10 < t < 110:
                            temp = t
                            print(f"✅ CPU Temp dari psutil ({name}): {t:.1f}°C")
                            break
                if temp is not None:
                    break
    except Exception as e:
        print(f"❌ psutil.sensors_temperatures gagal: {e}")

    # Method 2: WMI MSAcpi_ThermalZoneTemperature
    if temp is None and os.name == "nt":
        try:
            import wmi  # type: ignore

            w = wmi.WMI(namespace="root\wmi")
            sensors = w.MSAcpi_ThermalZoneTemperature()
            if sensors:
                t_kelvin = float(sensors[0].CurrentTemperature)
                t_c = t_kelvin / 10.0 - 273.15
                if 10 < t_c < 110:
                    temp = t_c
                    print(f"✅ CPU Temp dari WMI: {t_c:.1f}°C")
        except Exception as e:
            print(f"❌ WMI suhu CPU gagal: {e}")

    # Method 3: PowerShell
    if temp is None and os.name == "nt":
        try:
            ps_command = (
                'Get-WmiObject -Namespace "root/wmi" '
                '-Class MSAcpi_ThermalZoneTemperature | '
                'ForEach-Object { ($_.CurrentTemperature / 10) - 273.15 }'
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_command],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                line = result.stdout.strip().split("\n")[0]
                if line:
                    t_c = float(line)
                    if 10 < t_c < 110:
                        temp = t_c
                        print(f"✅ CPU Temp dari PowerShell: {t_c:.1f}°C")
        except Exception as e:
            print(f"❌ PowerShell suhu CPU gagal: {e}")

    return temp


def get_cpu_temperature_estimated():
    """Estimasi suhu CPU jika pembacaan langsung tidak tersedia."""
    real_temp = get_cpu_temperature_windows()
    if real_temp is not None:
        return real_temp

    # Fallback: estimasi berdasarkan usage & frekuensi
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        freq = psutil.cpu_freq()
        if freq:
            current = freq.current or 0
            max_f = freq.max or 3000.0

            base = 30.0
            freq_factor = (current / max_f) * 20.0
            usage_factor = (cpu_percent / 100.0) * 25.0
            estimated = base + freq_factor + usage_factor
            print(
                f"📊 Estimasi suhu CPU: {estimated:.1f}°C "
                f"(usage={cpu_percent:.1f}%, freq={current:.0f}MHz)"
            )
            return estimated
    except Exception as e:
        print(f"❌ Estimasi suhu CPU gagal: {e}")

    return None


def collect_metrics():
    hostname = socket.gethostname()
    now_human = time.strftime("%Y-%m-%d %H:%M:%S")

    # CPU usage
    cpu_percent = psutil.cpu_percent(interval=0.5)

    # CPU frequency
    cpu_freq_current = None
    cpu_freq_max = None
    try:
        freq_info = psutil.cpu_freq()
        if freq_info:
            cpu_freq_current = getattr(freq_info, "current", None)
            cpu_freq_max = getattr(freq_info, "max", None)
    except Exception as e:
        print(f"❌ psutil.cpu_freq gagal: {e}")

    # CPU temperature
    cpu_temp_c = get_cpu_temperature_estimated()

    # Memory
    mem = psutil.virtual_memory()

    # Disk (root)
    if os.name == "nt":
        root_path = os.getenv("SystemDrive", "C:") + "\\"
    else:
        root_path = "/"
    disk_root = psutil.disk_usage(root_path)

    # Semua partisi
    disk_all_lines = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disk_all_lines.append(
                f"{part.device} ({part.mountpoint}) "
                f"total={usage.total} used={usage.used} "
                f"free={usage.free} percent={usage.percent}%"
            )
        except PermissionError:
            continue
    disk_all_str = "\n".join(disk_all_lines)

    # Uptime
    boot_ts = psutil.boot_time()
    uptime_seconds = time.time() - boot_ts
    uptime_human = format_uptime(uptime_seconds)

    # Battery
    try:
        bat = psutil.sensors_battery()
    except Exception:
        bat = None

    if bat:
        battery_percent = bat.percent
        battery_state = "charging" if bat.power_plugged else "discharging"
    else:
        battery_percent = None
        battery_state = None

    # Network (IP addresses)
    ip_addrs = []
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith(
                "127."
            ):
                ip_addrs.append(addr.address)
    ip_addrs_str = " ".join(ip_addrs) if ip_addrs else None

    wifi_ssid = get_wifi_ssid()

    mem_text = (
        f"total={mem.total} used={mem.used} free={mem.free} "
        f"available={mem.available} percent={mem.percent}%"
    )
    disk_root_text = (
        f"path={root_path} total={disk_root.total} used={disk_root.used} "
        f"free={disk_root.free} percent={disk_root.percent}%"
    )

    metrics = {
        "device_label": DEVICE_LABEL,
        "hostname": hostname,
        "os": platform.platform(),
        "time_human": now_human,
        # CPU
        "cpu_percent": cpu_percent,
        "cpu_temp_c": cpu_temp_c,
        "cpu_freq_current": cpu_freq_current,
        "cpu_freq_max": cpu_freq_max,
        # Uptime
        "uptime": uptime_human,
        "uptime_seconds": int(uptime_seconds),
        # Memory & Disk
        "mem_usage": mem_text,
        "disk_root": disk_root_text,
        "disk_all": disk_all_str,
        # Battery
        "battery_state": battery_state,
        "battery_percent": battery_percent,
        # Network
        "ip_addrs": ip_addrs_str,
        "wifi_ssid": wifi_ssid,
    }
    return metrics


# ================== MQTT Callbacks ==================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        msg = "✅ AGENT WINDOWS: Terhubung ke broker MQTT."
        print(msg)
        logging.info(msg)
    else:
        msg = f"❌ AGENT WINDOWS: Gagal koneksi. Kode: {rc}"
        print(msg)
        logging.error(msg)


def on_disconnect(client, userdata, rc):
    msg = f"🔴 AGENT WINDOWS: Terputus dari broker. Kode: {rc}"
    print(msg)
    logging.warning(msg)


# ================== Main ==================

def main():
    # Pastikan modul wmi terinstall (opsional untuk baca suhu)
    if os.name == "nt":
        try:
            import wmi  # type: ignore  # noqa
        except ImportError:
            print("📦 Menginstall paket tambahan untuk WMI (wmi, pywin32)...")
            os.system("pip install wmi pywin32")

    hostname = socket.gethostname()
    client_id = f"windows-agent-{DEVICE_LABEL or hostname}"

    print(f"🔌 MQTT Client ID = {client_id}")
    print("🌡  Test pembacaan suhu CPU...")
    temp_test = get_cpu_temperature_estimated()
    if temp_test is not None:
        print(f"✅ Suhu CPU tersedia: {temp_test:.1f}°C")
    else:
        print("❌ Suhu CPU tidak bisa dibaca, akan coba estimasi jika perlu.")

    client = mqtt.Client(client_id=client_id)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    print(f"🔌 Mencoba koneksi ke broker MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as e:
        print(f"❌ Gagal koneksi awal ke MQTT: {e}")
        logging.error(f"Initial connect failed: {e}")
        sys.exit(1)

    client.loop_start()
    print(f"📡 Mulai kirim data monitoring tiap 3 detik ke topic '{MQTT_TOPIC}'...")

    try:
        while True:
            metrics = collect_metrics()
            payload = json.dumps(metrics)
            result = client.publish(MQTT_TOPIC, payload, qos=0)
            if result.rc == 0:
                print(
                    f"📤 Metrics terkirim @ {metrics['time_human']} - "
                    f"CPU: {metrics['cpu_percent']:.1f}% - "
                    f"Temp: {metrics['cpu_temp_c'] if metrics['cpu_temp_c'] is not None else 'N/A'}°C"
                )
            else:
                print(f"⚠ Gagal publish metrics, rc={result.rc}")
            time.sleep(3)
    except KeyboardInterrupt:
        print("🛑 Dihentikan oleh pengguna (Ctrl+C).")
    finally:
        client.loop_stop()
        client.disconnect()
        print("🔒 Koneksi MQTT ditutup.")


if __name__ == "__main__":
    main()
