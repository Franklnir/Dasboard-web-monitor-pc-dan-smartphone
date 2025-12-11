#!/usr/bin/env python3
"""config.py - Konfigurasi untuk dashboard monitoring PC & HP"""

# === Konfigurasi MQTT / HiveMQ Cloud ===
MQTT_BROKER = "9adb3bc2c82d40999a17052a760de55c.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USERNAME = "irsyad"
MQTT_PASSWORD = "Irsyad031226"
MQTT_TOPIC = "monitoring"

# === Konfigurasi MySQL / MariaDB ===
# Pastikan sudah membuat database terlebih dahulu, misalnya:
#   CREATE DATABASE monitoring_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = ""  # ganti sesuai password MySQL kamu
MYSQL_DB = "monitoring_db"

# === Konfigurasi Firebase Realtime Database untuk monitoring HP ===
FIREBASE_API_KEY = "AIzaSyC7tNFHsY7cvOotC9ool6Lwc1_guE1S5t4"
FIREBASE_DB_URL = "https://projectrumah-6e924-default-rtdb.firebaseio.com"
FIREBASE_USER_ID = "1lz5f063iBR4y73dpjcflFafDP92"
# Node latest_info sesuai struktur di Realtime Database
FIREBASE_LATEST_INFO_PATH = f"/users/{FIREBASE_USER_ID}/latest_info"
