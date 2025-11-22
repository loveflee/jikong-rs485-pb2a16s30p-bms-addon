#!/usr/bin/env bash
set -e

echo "📦 JK BMS TCP Monitor Add-on starting..."

# 讀取 /data/options.json，轉成 app 用的 config.yaml
# 這裡用 jq 把 HA options 填入你的原本 config 風格

cat > /data/config.yaml <<EOF
tcp:
  host: "$(jq -r '.tcp_host' /data/options.json)"
  port: $(jq -r '.tcp_port' /data/options.json)
  timeout: $(jq -r '.tcp_timeout' /data/options.json)
  buffer_size: $(jq -r '.tcp_buffer_size' /data/options.json)

mqtt:
  broker: "$(jq -r '.mqtt_broker' /data/options.json)"
  port: $(jq -r '.mqtt_port' /data/options.json)
  username: "$(jq -r '.mqtt_username' /data/options.json)"
  password: "$(jq -r '.mqtt_password' /data/options.json)"
  discovery_prefix: "$(jq -r '.mqtt_discovery_prefix' /data/options.json)"
  topic_prefix: "$(jq -r '.mqtt_topic_prefix' /data/options.json)"

app:
  packet_expire_time: $(jq -r '.packet_expire_time' /data/options.json)
  settings_publish_interval: $(jq -r '.settings_publish_interval' /data/options.json)
EOF

echo "✅ Generated /data/config.yaml for app:"
cat /data/config.yaml

# 執行 app（確保 main.py 有改成讀 /data/config.yaml）
exec python /app/main.py
