#!/usr/bin/env bash
set -euo pipefail

echo "📦 JiKong RS485 PB2A16S30P BMS Add-on starting..."

OPTIONS_FILE="/data/options.json"
OUT_CONFIG="/data/config.yaml"

if [ ! -f "${OPTIONS_FILE}" ]; then
  echo "❌ options.json 不存在: ${OPTIONS_FILE}"
  exit 1
fi

# 讀 options.json，轉成內部 config.yaml
echo "📝 產生 /data/config.yaml ..."

cat > "${OUT_CONFIG}" <<EOF
tcp:
  host: $(jq -r '.modbus_host' "${OPTIONS_FILE}")
  port: $(jq -r '.modbus_port' "${OPTIONS_FILE}")
  timeout: $(jq -r '.modbus_timeout' "${OPTIONS_FILE}")
  buffer_size: $(jq -r '.modbus_buffer_size' "${OPTIONS_FILE}")

mqtt:
  broker: $(jq -r '.mqtt_host' "${OPTIONS_FILE}")
  port: $(jq -r '.mqtt_port' "${OPTIONS_FILE}")
  username: $(jq -r '.mqtt_username' "${OPTIONS_FILE}")
  password: $(jq -r '.mqtt_password' "${OPTIONS_FILE}")
  discovery_prefix: $(jq -r '.mqtt_discovery_prefix' "${OPTIONS_FILE}")
  topic_prefix: $(jq -r '.mqtt_topic_prefix' "${OPTIONS_FILE}")
  client_id: $(jq -r '.mqtt_client_id' "${OPTIONS_FILE}")

serial:
  device: "/dev/ttyUSB0"     # 你可以之後在 options.json 補這一項
  baudrate: 9600             # 視 BMS / Gateway 設定調整
  timeout: 1.0

app:
  packet_expire_time: $(jq -r '.packet_expire_time' "${OPTIONS_FILE}")
  settings_publish_interval: $(jq -r '.settings_publish_interval' "${OPTIONS_FILE}")

  # 以下三個先給預設值，之後你可在 Add-on options/schema 補上
  use_modbus_gateway: true     # true: 走 TCP Modbus Gateway
  use_rs485_usb: false         # true: 直接讀 RS485 USB
  debug_raw_log: false         # true: 啟用 raw hexdump 除錯
EOF

echo "✅ /data/config.yaml 產生完成："
cat "${OUT_CONFIG}"

echo "🚀 啟動主程式 main.py ..."
exec python3 /app/main.py
