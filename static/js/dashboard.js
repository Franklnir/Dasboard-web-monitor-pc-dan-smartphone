// ============= GLOBAL STATE =============
let currentMode = "pc";
const charts = {}; // key: chartId => Chart instance

let hpMap = null;
let hpMarker = null;
let hpLastLat = null;
let hpLastLng = null;
let hpRouteLine = null; // polyline untuk rute 5 jam terakhir

// ~ 5 menit history PC (agent kirim tiap 3 detik -> 5 menit ≈ 100 titik)
const MAX_HISTORY_POINTS = 100;

const deviceColors = {
  "laptop-kampus": ["#3B82F6", "#60A5FA", "#93C5FD", "#1D4ED8"],
  "laptop-rumah": ["#10B981", "#34D399", "#6EE7B7", "#047857"],
};

function safeId(name) {
  return String(name || "device")
    .toLowerCase()
    .replace(/[^a-z0-9_-]/gi, "-");
}

// Helper untuk ambil N titik terakhir (5 menit terakhir)
function sliceLastN(labels, ...series) {
  if (!labels || !labels.length) {
    return { labels: [], series: series.map(() => []) };
  }
  if (labels.length <= MAX_HISTORY_POINTS) {
    return { labels, series };
  }
  const start = labels.length - MAX_HISTORY_POINTS;
  return {
    labels: labels.slice(start),
    series: series.map((arr) => (arr || []).slice(start)),
  };
}

// ============= MODE SWITCH =============

function switchMode(mode) {
  currentMode = mode;

  const btnPc = document.getElementById("btnModePc");
  const btnHp = document.getElementById("btnModeHp");
  const pcSection = document.getElementById("pc-section");
  const hpSection = document.getElementById("hp-section");

  if (!btnPc || !btnHp || !pcSection || !hpSection) return;

  if (mode === "pc") {
    btnPc.classList.add("active");
    btnHp.classList.remove("active");
    pcSection.style.display = "block";
    hpSection.style.display = "none";
  } else {
    btnPc.classList.remove("active");
    btnHp.classList.add("active");
    pcSection.style.display = "none";
    hpSection.style.display = "block";

    // Leaflet harus di-resize setelah section muncul
    setTimeout(() => {
      if (hpMap) {
        hpMap.invalidateSize();
        if (hpLastLat != null && hpLastLng != null) {
          hpMap.setView([hpLastLat, hpLastLng], hpMap.getZoom());
        }
      }
    }, 200);
  }
}

// ============= PC MONITORING =============

async function updatePcData() {
  try {
    const res = await fetch("/api/data");
    const data = await res.json();

    const now = new Date().toLocaleTimeString();
    const lastUpdateEl = document.getElementById("lastUpdateTime");
    if (lastUpdateEl) {
      lastUpdateEl.textContent = now;
    }

    renderDevices(data);
  } catch (err) {
    console.error("Gagal mengambil /api/data:", err);
  } finally {
    // Loop setiap 2 detik
    setTimeout(updatePcData, 2000);
  }
}

function renderDevices(devicesData) {
  const container = document.getElementById("devicesContainer");
  if (!container) return;

  // setiap kali akan render ulang, hancurkan dulu semua chart lama
  for (const key in charts) {
    const ch = charts[key];
    if (ch && typeof ch.destroy === "function") {
      try {
        ch.destroy();
      } catch (e) {
        console.warn("Gagal destroy chart", key, e);
      }
    }
    delete charts[key];
  }

  container.innerHTML = "";

  const deviceEntries = Object.entries(devicesData || {});
  if (deviceEntries.length === 0) {
    container.innerHTML = `
      <div class="no-data">
        <h3>⏳ Menunggu data dari device...</h3>
        <p>Pastikan <strong>agent.py</strong> sedang berjalan di laptop yang ingin kamu monitor.</p>
        <p style="margin-top:8px;font-size:0.9rem;opacity:0.8;">
          Data yang diterima akan otomatis muncul di sini (berdasarkan <code>device_label</code>).
        </p>
      </div>
    `;
    return;
  }

  for (const [deviceName, deviceData] of deviceEntries) {
    const card = createDeviceCard(deviceName, deviceData);
    container.appendChild(card);
  }
}

function createDeviceCard(deviceName, deviceData) {
  const card = document.createElement("div");
  card.className = "device-card";

  const colors = deviceColors[deviceName] || [
    "#6B7280",
    "#9CA3AF",
    "#D1D5DB",
    "#4B5563",
  ];

  const latest = deviceData.latest || {};
  const history = deviceData.history || {};

  const cpuArr = history.cpu_percent || [];
  const memArr = history.memory_percent || [];
  const diskArr = history.disk_percent || [];
  const tempArr = history.cpu_temp || [];
  const battArr = history.battery_percent || [];

  const lastCpu = cpuArr.length ? cpuArr[cpuArr.length - 1] : latest.cpu_percent;
  const lastMem =
    memArr.length && memArr[memArr.length - 1] != null
      ? memArr[memArr.length - 1]
      : parseMetric(latest.mem_usage, "percent");
  const lastDisk =
    diskArr.length && diskArr[diskArr.length - 1] != null
      ? diskArr[diskArr.length - 1]
      : parseMetric(latest.disk_root, "percent");
  const lastTemp =
    tempArr.length && tempArr[tempArr.length - 1] != null
      ? tempArr[tempArr.length - 1]
      : latest.cpu_temp_c;
  const lastBatt =
    battArr.length && battArr[battArr.length - 1] != null
      ? battArr[battArr.length - 1]
      : latest.battery_percent;

  const cpuDisplay =
    typeof lastCpu === "number" ? `${lastCpu.toFixed(1)}%` : "N/A";
  const memDisplay =
    typeof lastMem === "number" ? `${lastMem.toFixed(1)}%` : "N/A";
  const diskDisplay =
    typeof lastDisk === "number" ? `${lastDisk.toFixed(1)}%` : "N/A";
  const freqDisplay =
    typeof latest.cpu_freq_current === "number"
      ? `${(latest.cpu_freq_current / 1000).toFixed(2)} GHz`
      : "N/A";

  let tempDisplay = "N/A";
  let tempWarningClass = "";
  if (typeof lastTemp === "number") {
    if (lastTemp > 80) {
      tempDisplay = `🔥 ${lastTemp.toFixed(1)}°C`;
      tempWarningClass = "temperature-warning";
    } else {
      tempDisplay = `${lastTemp.toFixed(1)}°C`;
    }
  }

  let batteryDisplay = "N/A";
  let batteryClass = "";
  if (typeof lastBatt === "number") {
    const state = latest.battery_state || "";
    if (state === "charging") {
      batteryDisplay = `⚡ ${lastBatt.toFixed(0)}%`;
      batteryClass = "battery-charging";
    } else {
      batteryDisplay = `${lastBatt.toFixed(0)}%`;
      if (lastBatt <= 20) batteryClass = "battery-low";
    }
  }

  const safe = safeId(deviceName);
  const online = !!deviceData.online;
  const since = deviceData.time_since_update ?? null;

  const hostname = latest.hostname || "N/A";
  const osName = latest.os || "N/A";
  const uptime = latest.uptime || "N/A";
  const lastUpdateStr = deviceData.last_update || "-";

  const cpuMaxFreq =
    typeof latest.cpu_freq_max === "number"
      ? `${(latest.cpu_freq_max / 1000).toFixed(2)} GHz`
      : "N/A";
  const memTotal = formatBytes(parseMetric(latest.mem_usage, "total"));
  const diskTotal = formatBytes(parseMetric(latest.disk_root, "total"));

  const wifi = latest.wifi_ssid || "-";
  const ips = latest.ip_addrs || "-";
  const label = latest.device_label || deviceName;

  card.innerHTML = `
    <div class="device-header">
      <div class="device-name">
        <span class="emoji">💻</span>
        <span>${deviceName.toUpperCase()}</span>
      </div>
      <div class="${online ? "status-online" : "status-offline"}">
        <span>${online ? "🟢 ONLINE" : "🔴 OFFLINE"}</span>
        ${
          online && since != null
            ? `<span style="opacity:0.8;">(${since}s ago)</span>`
            : ""
        }
      </div>
    </div>

    <div class="metrics-grid">
      <div class="metric-card cpu">
        <div class="metric-label">CPU Usage</div>
        <div class="metric-value">${cpuDisplay}</div>
        <div class="metric-sub">Total core usage</div>
      </div>
      <div class="metric-card memory">
        <div class="metric-label">Memory Usage</div>
        <div class="metric-value">${memDisplay}</div>
        <div class="metric-sub">RAM in use</div>
      </div>
      <div class="metric-card disk">
        <div class="metric-label">Disk Usage</div>
        <div class="metric-value">${diskDisplay}</div>
        <div class="metric-sub">Root partition</div>
      </div>
      <div class="metric-card temp ${tempWarningClass}">
        <div class="metric-label">CPU Temperature</div>
        <div class="metric-value">${tempDisplay}</div>
        <div class="metric-sub">${
          tempWarningClass ? "⚠ Suhu tinggi!" : "Normal range"
        }</div>
      </div>
      <div class="metric-card frequency">
        <div class="metric-label">CPU Frequency</div>
        <div class="metric-value">${freqDisplay}</div>
        <div class="metric-sub">Current clock</div>
      </div>
      <div class="metric-card battery ${batteryClass}">
        <div class="metric-label">Battery</div>
        <div class="metric-value">${batteryDisplay}</div>
        <div class="metric-sub">${
          latest.battery_state
            ? latest.battery_state.toUpperCase()
            : "Unknown"
        }</div>
      </div>
    </div>

    <div class="charts-container">
      <div class="chart-wrapper">
        <div class="chart-title">📈 CPU Usage (5 menit terakhir)</div>
        <div class="chart-container">
          <canvas id="cpuChart-${safe}"></canvas>
        </div>
      </div>
      <div class="chart-wrapper">
        <div class="chart-title">💾 Memory Usage (5 menit terakhir)</div>
        <div class="chart-container">
          <canvas id="memChart-${safe}"></canvas>
        </div>
      </div>
      <div class="chart-wrapper">
        <div class="chart-title">🌡 CPU Temperature History</div>
        <div class="chart-container">
          <canvas id="tempChart-${safe}"></canvas>
        </div>
      </div>
      <div class="chart-wrapper">
        <div class="chart-title">🔋 Battery Level History</div>
        <div class="chart-container">
          <canvas id="battChart-${safe}"></canvas>
        </div>
      </div>
    </div>

    <div class="info-grid">
      <div class="info-section">
        <div class="section-title">🖥 System Information</div>
        <div class="info-row">
          <span class="info-label">Hostname</span>
          <span class="info-value">${hostname}</span>
        </div>
        <div class="info-row">
          <span class="info-label">OS</span>
          <span class="info-value">${osName}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Uptime</span>
          <span class="info-value">${uptime}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Last Update</span>
          <span class="info-value">${lastUpdateStr}</span>
        </div>
      </div>

      <div class="info-section">
        <div class="section-title">📊 Hardware Details</div>
        <div class="info-row">
          <span class="info-label">CPU Max Freq</span>
          <span class="info-value">${cpuMaxFreq}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Memory Total</span>
          <span class="info-value">${memTotal}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Disk Total</span>
          <span class="info-value">${diskTotal}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Disk Detail</span>
          <span class="info-value">${
            (latest.disk_root || "").split("percent=")[0] || "-"
          }</span>
        </div>
      </div>

      <div class="info-section">
        <div class="section-title">🌐 Network Information</div>
        <div class="info-row">
          <span class="info-label">IP Address</span>
          <span class="info-value">${ips}</span>
        </div>
        <div class="info-row">
          <span class="info-label">WiFi SSID</span>
          <span class="info-value">${
            wifi && wifi !== "-" ? `📶 ${wifi}` : "-"
          }</span>
        </div>
        <div class="info-row">
          <span class="info-label">Device Label</span>
          <span class="info-value">${label}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Online State</span>
          <span class="info-value">${
            online && since != null ? `Online · ${since}s ago` : "Offline"
          }</span>
        </div>
      </div>
    </div>
  `;

  setTimeout(() => {
    renderDeviceCharts(safe, history, colors);
  }, 50);

  return card;
}

function renderDeviceCharts(safe, history, colors) {
  let labels = history.timestamps || [];
  let cpuArr = history.cpu_percent || [];
  let memArr = history.memory_percent || [];
  let tempArr = history.cpu_temp || [];
  let battArr = history.battery_percent || [];

  const sliced = sliceLastN(labels, cpuArr, memArr, tempArr, battArr);
  labels = sliced.labels;
  [cpuArr, memArr, tempArr, battArr] = sliced.series;

  if (!labels.length) return;

  const baseOptions = {
    responsive: true,
    maintainAspectRatio: false,
    animation: {
      duration: 500,
      easing: "easeOutCubic",
    },
    plugins: {
      legend: { display: false },
    },
    scales: {
      x: {
        ticks: { maxTicksLimit: 8, font: { size: 10 } },
      },
      y: {
        beginAtZero: true,
      },
    },
    elements: {
      point: { radius: 0, hoverRadius: 3 },
      line: { tension: 0.3 },
    },
  };

  // CPU: bar chart
  const cpuCanvas = document.getElementById(`cpuChart-${safe}`);
  if (cpuCanvas && cpuArr.length) {
    const key = `cpu-${safe}`;
    if (!charts[key]) {
      charts[key] = new Chart(cpuCanvas.getContext("2d"), {
        type: "bar",
        data: {
          labels,
          datasets: [
            {
              label: "CPU Usage %",
              data: cpuArr,
              borderColor: colors[0],
              backgroundColor: hexToRgba(colors[0], 0.6),
              borderWidth: 1,
              barPercentage: 0.9,
              categoryPercentage: 0.9,
            },
          ],
        },
        options: {
          ...baseOptions,
          scales: {
            ...baseOptions.scales,
            y: { ...baseOptions.scales.y, max: 100 },
          },
        },
      });
    } else {
      const ch = charts[key];
      ch.data.labels = labels;
      ch.data.datasets[0].data = cpuArr;
      ch.update();
    }
  }

  // Memory: line chart
  const memCanvas = document.getElementById(`memChart-${safe}`);
  if (memCanvas && memArr.length) {
    const key = `mem-${safe}`;
    if (!charts[key]) {
      charts[key] = new Chart(memCanvas.getContext("2d"), {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "Memory Usage %",
              data: memArr,
              borderColor: colors[1],
              backgroundColor: hexToRgba(colors[1], 0.18),
              fill: true,
            },
          ],
        },
        options: {
          ...baseOptions,
          scales: {
            ...baseOptions.scales,
            y: { ...baseOptions.scales.y, max: 100 },
          },
        },
      });
    } else {
      const ch = charts[key];
      ch.data.labels = labels;
      ch.data.datasets[0].data = memArr;
      ch.update();
    }
  }

  // Temperature
  const tempCanvas = document.getElementById(`tempChart-${safe}`);
  if (tempCanvas && tempArr.length) {
    const key = `temp-${safe}`;
    if (!charts[key]) {
      charts[key] = new Chart(tempCanvas.getContext("2d"), {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "CPU Temp °C",
              data: tempArr,
              borderColor: "#EF4444",
              backgroundColor: "rgba(239,68,68,0.2)",
              fill: true,
            },
          ],
        },
        options: baseOptions,
      });
    } else {
      const ch = charts[key];
      ch.data.labels = labels;
      ch.data.datasets[0].data = tempArr;
      ch.update();
    }
  }

  // Battery
  const battCanvas = document.getElementById(`battChart-${safe}`);
  if (battCanvas && battArr.length) {
    const key = `batt-${safe}`;
    if (!charts[key]) {
      charts[key] = new Chart(battCanvas.getContext("2d"), {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "Battery Level %",
              data: battArr,
              borderColor: colors[3],
              backgroundColor: hexToRgba(colors[3], 0.18),
              fill: true,
            },
          ],
        },
        options: {
          ...baseOptions,
          scales: {
            ...baseOptions.scales,
            y: { ...baseOptions.scales.y, max: 100 },
          },
        },
      });
    } else {
      const ch = charts[key];
      ch.data.labels = labels;
      ch.data.datasets[0].data = battArr;
      ch.update();
    }
  }
}

// Parse helper "key=value"
function parseMetric(text, key) {
  if (!text) return null;
  try {
    const parts = text.split(" ");
    for (const part of parts) {
      if (part.startsWith(key + "=")) {
        let value = part.split("=", 2)[1];
        if (value.endsWith("%")) value = value.slice(0, -1);
        const n = parseFloat(value);
        if (!Number.isNaN(n)) return n;
      }
    }
  } catch (e) {
    console.error("Error parsing metric:", e);
  }
  return null;
}

function formatBytes(bytes) {
  if (!bytes || Number.isNaN(bytes)) return "N/A";
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  if (bytes === 0) return "0 Bytes";
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val.toFixed(2)} ${sizes[i]}`;
}

function hexToRgba(hex, alpha) {
  let c = hex.replace("#", "");
  if (c.length === 3) {
    c = c
      .split("")
      .map((x) => x + x)
      .join("");
  }
  const num = parseInt(c, 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

// ============= HP MONITORING =============

async function updateHpData() {
  try {
    const res = await fetch("/api/hp-latest");
    const data = await res.json();

    renderHp(data);
  } catch (err) {
    console.error("Gagal mengambil /api/hp-latest:", err);
    showHpError("Gagal mengambil data dari server.");
  } finally {
    setTimeout(updateHpData, 5000);
  }
}

function renderHp(info) {
  const ok = info && info.ok;
  const raw = info ? info.raw : null;

  const rawRoute = Array.isArray(info && info.route) ? info.route : [];
  const snappedRoute = Array.isArray(info && info.snapped_route)
    ? info.snapped_route
    : [];

  // kalau snapped_route ada → pakai itu (ngikutin jalan)
  const routePoints = snappedRoute.length ? snappedRoute : rawRoute;

  const elLastTime = document.getElementById("hp-last-time");
  const elLastTs = document.getElementById("hp-last-timestamp");
  const elBatt = document.getElementById("hp-battery");
  const elCharging = document.getElementById("hp-charging");
  const elWifi = document.getElementById("hp-wifi");
  const elNetwork = document.getElementById("hp-network");
  const elLocText = document.getElementById("hp-location-text");
  const elAccuracy = document.getElementById("hp-accuracy");
  const elPill = document.getElementById("hp-online-pill");

  // elemen tambahan
  const elDevTemp = document.getElementById("hp-device-temp");
  const elWifiSpeed = document.getElementById("hp-wifi-speed");
  const elIp = document.getElementById("hp-ip");
  const elAndroid = document.getElementById("hp-android");
  const elBrandModel = document.getElementById("hp-brand-model");
  const elChipset = document.getElementById("hp-chipset");
  const elRam = document.getElementById("hp-ram");
  const elStorage = document.getElementById("hp-storage");

  if (!ok && !routePoints.length) {
    showHpError(info && info.error ? info.error : "Tidak ada data.");
    if (elPill) {
      elPill.classList.remove("online");
      elPill.classList.add("offline");
      elPill.textContent = "🔴 Tidak ada data HP";
    }
    return;
  }

  hideHpError();

  const timeHuman = info.time_human || "-";
  const ts =
    info.timestamp ||
    (raw && (raw.updated_at || raw.timestamp)) ||
    "-";
  const battery = info.battery_percent;
  const isCharging = info.is_charging;
  const wifi = info.wifi_ssid || (raw && raw.wifiSSID) || "-";
  const net =
    info.network_type || (raw && raw.mobileNetworkType) || "-";

  const acc = info.accuracy;

  const devTemp = info.device_temp_c || (raw && raw.deviceTemperatureC);
  const wifiSpeed = info.wifi_link_speed || (raw && raw.wifiLinkSpeed);
  const ipAddress = info.ip_address || (raw && raw.ipAddress);
  const androidVersion =
    info.android_version || (raw && raw.androidVersion) || "-";
  const brand = info.brand || (raw && raw.brand) || "-";
  const model = info.model || (raw && raw.model) || "-";
  const chipset = info.chipset || (raw && raw.chipset) || "-";
  const sdkLevel = info.sdk_level || (raw && raw.sdkLevel);
  const availRamMb =
    info.available_ram_mb || (raw && raw.availableRamMb);
  const totalRamMb =
    info.total_ram_mb || (raw && raw.totalRamMb);
  const availStorageMb =
    info.avail_internal_storage_mb ||
    (raw && raw.availableInternalStorageMb);
  const totalStorageMb =
    info.total_internal_storage_mb ||
    (raw && raw.totalInternalStorageMb);
  const locText =
    info.location_text || (raw && raw.location_text) || null;

  if (elLastTime) elLastTime.textContent = `Last update: ${timeHuman}`;
  if (elLastTs) elLastTs.textContent = ts ? `timestamp: ${ts}` : "";

  if (elBatt) {
    if (typeof battery === "number") {
      elBatt.textContent = `${battery.toFixed(0)}%`;
    } else {
      elBatt.textContent = "-";
    }
  }

  if (elCharging) {
    if (battery == null) {
      elCharging.textContent = "-";
    } else if (isCharging) {
      elCharging.textContent = "⚡ Charging";
    } else {
      elCharging.textContent = "🔋 Discharging";
    }
  }

  if (elWifi) elWifi.textContent = wifi;
  if (elNetwork) elNetwork.textContent = net;

  if (elDevTemp) {
    if (typeof devTemp === "number") {
      elDevTemp.textContent = `${devTemp.toFixed(1)}°C`;
    } else {
      elDevTemp.textContent = "-";
    }
  }
  if (elWifiSpeed) elWifiSpeed.textContent = wifiSpeed || "-";
  if (elIp) elIp.textContent = ipAddress || "-";

  // Tentukan koordinat titik terakhir:
  // 1) pakai info.lat/lng jika ada
  // 2) kalau tidak ada, pakai titik terakhir dari routePoints
  let lat = info.lat;
  let lng = info.lng;

  if ((lat == null || lng == null) && routePoints.length) {
    const lastPoint = routePoints[routePoints.length - 1];
    if (
      lastPoint &&
      typeof lastPoint.lat === "number" &&
      typeof lastPoint.lng === "number"
    ) {
      lat = lastPoint.lat;
      lng = lastPoint.lng;
    }
  }

  if (lat != null && lng != null) {
    hpLastLat = lat;
    hpLastLng = lng;

    if (elLocText) {
      const latStr = typeof lat === "number" ? lat.toFixed(5) : `${lat}`;
      const lngStr = typeof lng === "number" ? lng.toFixed(5) : `${lng}`;
      elLocText.textContent = `${latStr}, ${lngStr}`;
    }

    updateHpMap(lat, lng, routePoints);
  } else {
    if (elLocText) {
      elLocText.textContent =
        locText ||
        (routePoints.length
          ? "Rute tersedia, tapi koordinat terakhir tidak terbaca."
          : "Belum ada data koordinat (lat/lng) di Firebase.");
    }
    if (routePoints.length) {
      updateHpRouteLine(routePoints);
    }
  }

  if (elAccuracy) {
    if (typeof acc === "number") {
      elAccuracy.textContent = `${acc.toFixed(1)} m`;
    } else {
      elAccuracy.textContent = "-";
    }
  }

  // detail device
  if (elAndroid) {
    elAndroid.textContent =
      sdkLevel != null
        ? `Android ${androidVersion} (SDK ${sdkLevel})`
        : `Android ${androidVersion}`;
  }
  if (elBrandModel) {
    elBrandModel.textContent = `${brand} ${model}`;
  }
  if (elChipset) {
    elChipset.textContent = chipset || "-";
  }
  if (elRam) {
    if (totalRamMb) {
      const totalGb = (totalRamMb / 1024).toFixed(2);
      if (availRamMb != null) {
        const usedGb = (totalRamMb - availRamMb) / 1024;
        elRam.textContent = `${usedGb.toFixed(
          2
        )} / ${totalGb} GB terpakai`;
      } else {
        elRam.textContent = `${totalGb} GB`;
      }
    } else {
      elRam.textContent = "-";
    }
  }
  if (elStorage) {
    if (totalStorageMb) {
      const totalGb = (totalStorageMb / 1024).toFixed(2);
      if (availStorageMb != null) {
        const usedGb =
          (totalStorageMb - availStorageMb) / 1024;
        elStorage.textContent = `${usedGb.toFixed(
          2
        )} / ${totalGb} GB terpakai`;
      } else {
        elStorage.textContent = `${totalGb} GB`;
      }
    } else {
      elStorage.textContent = "-";
    }
  }

  if (elPill) {
    elPill.classList.remove("offline");
    elPill.classList.add("online");
    elPill.textContent = "🟢 Data HP tersedia";
  }
}

function showHpError(msg) {
  const el = document.getElementById("hp-error");
  if (!el) return;
  el.style.display = "block";
  el.textContent = `⚠ ${msg}`;
}

function hideHpError() {
  const el = document.getElementById("hp-error");
  if (!el) return;
  el.style.display = "none";
  el.textContent = "";
}

function initHpMap(lat, lng) {
  const mapEl = document.getElementById("hp-map");
  if (!mapEl) return;

  const defaultLat = lat != null ? lat : -6.3078; // sekitar Cikarang
  const defaultLng = lng != null ? lng : 107.1721;

  hpMap = L.map("hp-map").setView([defaultLat, defaultLng], 15);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(hpMap);

  hpMarker = L.marker([defaultLat, defaultLng]).addTo(hpMap);
  hpMarker.bindPopup("Lokasi HP (titik terakhir)").openPopup();

  setTimeout(() => {
    hpMap.invalidateSize();
  }, 200);
}

function updateHpMap(lat, lng, routePoints) {
  if (lat == null || lng == null) return;

  hpLastLat = lat;
  hpLastLng = lng;

  if (!hpMap || !hpMarker) {
    initHpMap(lat, lng);
  }

  const newLatLng = [lat, lng];
  hpMarker.setLatLng(newLatLng);

  if (Array.isArray(routePoints) && routePoints.length) {
    updateHpRouteLine(routePoints);
  } else {
    hpMap.setView(newLatLng, hpMap.getZoom());
  }
}

function updateHpRouteLine(routePoints) {
  if (!Array.isArray(routePoints) || !routePoints.length) {
    if (hpRouteLine && hpMap) {
      hpMap.removeLayer(hpRouteLine);
      hpRouteLine = null;
    }
    return;
  }

  const latLngs = routePoints
    .filter(
      (p) =>
        p &&
        typeof p.lat === "number" &&
        typeof p.lng === "number"
    )
    .map((p) => [p.lat, p.lng]);

  if (!latLngs.length) return;

  if (!hpMap) {
    const [lastLat, lastLng] = latLngs[latLngs.length - 1];
    initHpMap(lastLat, lastLng);
  }

  if (!hpRouteLine) {
    hpRouteLine = L.polyline(latLngs, {
      color: "#2563EB",
      weight: 4,
      opacity: 0.8,
    }).addTo(hpMap);
  } else {
    hpRouteLine.setLatLngs(latLngs);
  }

  const [lastLat, lastLng] = latLngs[latLngs.length - 1];
  if (!hpMarker) {
    hpMarker = L.marker([lastLat, lastLng]).addTo(hpMap);
  } else {
    hpMarker.setLatLng([lastLat, lastLng]);
  }

  const bounds = hpRouteLine.getBounds();
  hpMap.fitBounds(bounds, { padding: [20, 20] });
}

// ============= INIT =============

document.addEventListener("DOMContentLoaded", () => {
  const btnPc = document.getElementById("btnModePc");
  const btnHp = document.getElementById("btnModeHp");

  if (btnPc) {
    btnPc.addEventListener("click", () => switchMode("pc"));
  }
  if (btnHp) {
    btnHp.addEventListener("click", () => switchMode("hp"));
  }

  // Mulai dalam mode PC
  switchMode("pc");

  // Mulai loop update PC & HP
  updatePcData();
  updateHpData();
});
