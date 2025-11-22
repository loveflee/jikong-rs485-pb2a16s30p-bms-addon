#!/usr/bin/env bash
set -euo pipefail

echo "📦 JK BMS TCP Monitor Add-on starting..."

# 必要工具檢查 (jq)
if ! command -v jq >/dev/null 2>&1; then
  echo "❌ jq not found"
  exit 1
fi

OPTIONS_FILE="/data/options.json"
OUT_CONFIG="/data/config.yaml"

if [ ! -f "${OPTIONS_FILE}" ]; then
  echo "❌ options.json not found at ${OPTIONS_FILE}"
  ls -l /data || true
  exit 1
fi

# 產生 /data/config.yaml 給應用程式使用
cat > "${OUT_CONFIG}" <<EOF
tcp:
  host: "$(jq -r '.modbus_host // empty' ${OPTIONS_FILE})"
  port: $(jq -r '.modbus_port // 502' ${OPTIONS_FILE})
  timeout: $(jq -r '.modbus_timeout // 10' ${OPTIONS_FILE})
  buffer_size: $(jq -r '.modbus_buffer_size // 4096' ${OPTIONS_FILE})

mqtt:
  broker: "$(jq -r '.mqtt_host // empty' ${OPTIONS_FILE})"
  port: $(jq -r '.mqtt_port // 1883' ${OPTIONS_FILE})
  username: "$(jq -r '.mqtt_username // empty' ${OPTIONS_FILE})"
  password: "$(jq -r '.mqtt_password // empty' ${OPTIONS_FILE})"
  discovery_prefix: "$(jq -r '.mqtt_discovery_prefix // "homeassistant"' ${OPTIONS_FILE})"
  topic_prefix: "$(jq -r '.mqtt_topic_prefix // "bms"' ${OPTIONS_FILE})"
  client_id: "$(jq -r '.mqtt_client_id // "jk_bms_monitor"' ${OPTIONS_FILE})"

app:
  packet_expire_time: $(jq -r '.packet_expire_time // 0.4' ${OPTIONS_FILE})
  settings_publish_interval: $(jq -r '.settings_publish_interval // 1800' ${OPTIONS_FILE})
EOF

echo "✅ Generated ${OUT_CONFIG}:"
cat "${OUT_CONFIG}"

# 啟動應用
exec python /app/main.py
