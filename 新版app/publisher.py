# =============================================================================
# publisher.py - V2.2.3 Production Final (Edge Node Hardened)
# 模組名稱：JK-BMS 數據發布模組
# 修正亮點：
#   - [Fix] 時鐘源統一：將 settings_last_publish 的時鐘源改為 time.monotonic()，防止 NTP 躍變觸發 MQTT 廣播風暴。
# =============================================================================

import json
import time
import yaml
import os
import logging
import paho.mqtt.client as mqtt
from typing import Dict, Any, Optional, Tuple, Set

from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_publisher")

class MqttPublisher:
    def __init__(self, config_path: str = "/data/config.yaml"):
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"找不到設定檔: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f)

        self.mqtt_cfg = full_cfg.get("mqtt", {})
        self.app_cfg = full_cfg.get("app", {})

        self.discovery_prefix = self.mqtt_cfg.get("discovery_prefix", "homeassistant")
        self.topic_prefix = self.mqtt_cfg.get("topic_prefix", "Jikong_BMS")
        self.client_id = self.mqtt_cfg.get("client_id", "jk_bms_monitor")
        self.status_topic = f"{self.topic_prefix}/status"

        self._last_state_publish: Dict[str, float] = {}
        self._state_min_interval = 0.2
        self._discovery_sent: Set[Tuple] = set()
        self._availability_cache: Dict[int, str] = {}
        self._availability_min_interval = 1.0
        self._last_availability_publish: Dict[int, float] = {}

        broker = self.mqtt_cfg.get("host", "core-mosquitto")
        port = int(self.mqtt_cfg.get("port", 1883))
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311, clean_session=True)

        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.max_inflight_messages_set(50)
        self.client.max_queued_messages_set(1000)

        if self.mqtt_cfg.get("username") and self.mqtt_cfg.get("password"):
            self.client.username_pw_set(self.mqtt_cfg["username"], self.mqtt_cfg["password"])

        self.client.will_set(self.status_topic, payload="offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect_async(broker, port, keepalive=60)
            self.client.loop_start()
            logger.info(f"📡 MQTT V2.2.3 最終版啟動: {broker}:{port}")
        except Exception as e:
            logger.error(f"❌ MQTT 啟動失敗: {e}")

        self.settings_last_publish: Dict[int, float] = {}

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("✅ MQTT 已連線")
            self._safe_publish(self.status_topic, payload="online", qos=1, retain=True)
        else:
            logger.warning(f"⚠️ MQTT 連線錯誤 rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning(f"⚠️ MQTT 非預期斷連 (rc={rc})，系統進入重連退避模式")

    def _safe_publish(self, topic: str, payload, retain: bool = False, qos: int = 0):
        try:
            if not self.client.is_connected():
                return False

            if isinstance(payload, (dict, list)):
                data = json.dumps(payload, allow_nan=False, separators=(",", ":"))
            else:
                data = payload

            info = self.client.publish(topic, payload=data, retain=retain, qos=qos)
            return info.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.debug(f"MQTT 發布失敗 ({topic}): {e}")
            return False

    def publish_device_status(self, device_id: int, status: str):
        now = time.monotonic()
        last_pub = self._last_availability_publish.get(device_id, 0)

        if status == self._availability_cache.get(device_id) and (now - last_pub < self._availability_min_interval):
            return

        topic = f"{self.topic_prefix}/{device_id}/status"
        if self._safe_publish(topic, payload=status, retain=True, qos=1):
            self._availability_cache[device_id] = status
            self._last_availability_publish[device_id] = now
            logger.info(f"🔄 狀態同步: BMS {device_id} -> {status}")

    def publish_discovery_for_packet_type(self, device_id: int, packet_type: int, data_map: Dict[int, Any]):
        if packet_type == 0x10: return

        key = (device_id, packet_type, tuple(data_map.keys()))
        if key in self._discovery_sent: return

        if len(self._discovery_sent) > 2000:
            self._discovery_sent.clear()
            logger.info("🧹 自動清理 Discovery 快取")

        self._discovery_sent.add(key)

        device_info = {
            "identifiers": [f"jk_bms_{device_id}"],
            "manufacturer": "JiKong (JK-BMS)",
            "model": "PB2A16S30P (Final)",
            "name": f"JK BMS {device_id if device_id != 0 else '0 (Master)'}",
        }

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        for offset, entry in data_map.items():
            key_en = entry[6] if len(entry) > 6 else f"reg_{packet_type}_{offset}"
            payload = {
                "name": entry[0],
                "unique_id": f"jk_bms_{device_id}_{key_en}",
                "state_topic": state_topic,
                "device": device_info,
                "availability": [
                    {"topic": self.status_topic},
                    {"topic": f"{self.topic_prefix}/{device_id}/status"}
                ],
                "availability_mode": "all",
                "value_template": f"{{{{ value_json['{key_en}'] }}}}"
            }

            if packet_type == 0x01:
                payload["entity_category"] = "diagnostic"

            if len(entry) > 5 and entry[5]: payload["icon"] = entry[5]
            if entry[1] and entry[1] not in ("Hex", "Bit", "Enum"):
                payload["unit_of_measurement"] = entry[1]

            disc_topic = f"{self.discovery_prefix}/{entry[4] if len(entry) > 4 else 'sensor'}/jk_bms_{device_id}/{key_en}/config"
            self._safe_publish(disc_topic, payload, retain=True, qos=1)

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        if packet_type == 0x10: return

        now = time.monotonic()
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        last_pub = self._last_state_publish.get(state_topic, 0)
        if now - last_pub < self._state_min_interval:
            return

        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 60))
            # 🚀 [V2.2.3] 改用單調時鐘
            if time.monotonic() - self.settings_last_publish.get(device_id, 0) < interval:
                return
            self.settings_last_publish[device_id] = time.monotonic()

        if self._safe_publish(state_topic, payload_dict, retain=False):
            self._last_state_publish[state_topic] = now
            if packet_type in BMS_MAP:
                self.publish_discovery_for_packet_type(device_id, packet_type, BMS_MAP[packet_type])

_publisher_instance = None
def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
