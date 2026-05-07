# =============================================================================
# publisher.py - V2.3.3 Production Final (Edge Node Hardened)
# 模組名稱：JK-BMS 數據發布模組
# 升級亮點：
#   - [Fix V2.3.3] 補回 Tuple 尾端字典 (ha_conf) 提取邏輯，完美修復強制覆寫 (Override) 四象限失效的問題。
#   - [Fix V2.3.2] 強制補齊 availability payload，解決 HA 實體顯示「不可用」的死穴。
#   - [Fix V2.3.2] 嚴格遵守 State 無 Retain 原則，確保數據 100% 來自真實輪詢。
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
            logger.info(f"📡 MQTT V2.3.3 (強制覆寫修復版) 啟動: {broker}:{port}")
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

    # =========================================================================
    # HA Payload Builder 區塊
    # =========================================================================
    def _get_base_payload(self, device_id: int, packet_type: int, name: str, key_en: str) -> dict:
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"
        
        return {
            "name": name,
            "unique_id": f"jk_bms_{device_id}_{key_en}",
            "object_id": f"jk_bms_{device_id}_{key_en}",
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json['{key_en}'] }}}}",
            "device": {
                "identifiers": [f"jk_bms_{device_id}"],
                "manufacturer": "JiKong (JK-BMS)",
                "model": "PB2A16S30P (Edge Hardened)",
                "name": f"JK BMS {device_id if device_id != 0 else '0 (Master)'}",
            },
            "availability": [
                {"topic": self.status_topic, "payload_available": "online", "payload_not_available": "offline"},
                {"topic": f"{self.topic_prefix}/{device_id}/status", "payload_available": "online", "payload_not_available": "offline"}
            ],
            "availability_mode": "all",
        }

    def _apply_common(self, payload: dict, unit: Optional[str], icon: Optional[str], packet_type: int, domain: str, key_en: str, ha_conf: dict) -> dict:
        if unit and "unit_of_measurement" not in payload:
            payload["unit_of_measurement"] = unit
        if icon and "icon" not in payload:
            payload["icon"] = icon

        # 1. 預設自動分流
        if domain in ("switch", "number", "select", "button", "text"):
            payload["entity_category"] = "config"
        elif packet_type == 0x01:
            payload["entity_category"] = "diagnostic"
            
        if domain == "binary_sensor" and ("switch" in key_en or "mos" in key_en):
            payload["entity_category"] = "config"

        # 2. 強制覆寫 (Override) - 以 Tuple 末端的字典為最高優先級
        if "entity_category" in ha_conf:
            if ha_conf["entity_category"] is None:
                payload.pop("entity_category", None) # 設為 None 時拉回主面板
            else:
                payload["entity_category"] = ha_conf["entity_category"]

        # 3. 寫入字典內的其他自訂屬性 (如 min, max 等)
        for k, v in ha_conf.items():
            if k != "entity_category":
                payload[k] = v

        return payload

    def _build_sensor_payload(self, base: dict) -> dict:
        return base

    def _build_binary_sensor_payload(self, base: dict, ha_conf: dict) -> dict:
        # 相容自訂 payload_on/off
        base["payload_on"]  = ha_conf.get("payload_on", "ON")
        base["payload_off"] = ha_conf.get("payload_off", "OFF")
        return base

    # =========================================================================
    # 發布核心邏輯
    # =========================================================================
    def publish_discovery_for_packet_type(self, device_id: int, packet_type: int, data_map: Dict[int, Any]):
        if packet_type == 0x10: return

        key = (device_id, packet_type, tuple(data_map.keys()))
        if key in self._discovery_sent: return

        if len(self._discovery_sent) > 2000:
            self._discovery_sent.clear()
            logger.info("🧹 自動清理 Discovery 快取")

        self._discovery_sent.add(key)

        for offset, entry in data_map.items():
            name = entry[0]
            unit = entry[1] if entry[1] not in ("Hex", "Bit", "Enum", None, "") else None
            domain = entry[4] if len(entry) > 4 and isinstance(entry[4], str) else "sensor"
            icon = entry[5] if len(entry) > 5 and isinstance(entry[5], str) else None
            key_en = entry[6] if len(entry) > 6 and isinstance(entry[6], str) else f"reg_{packet_type}_{offset}"
            
            # 🟢 [V2.3.3 修復] 精準提取 Tuple 尾端的設定字典 (ha_conf)
            ha_conf = {}
            for item in reversed(entry):
                if isinstance(item, dict):
                    ha_conf = item
                    break

            base_payload = self._get_base_payload(device_id, packet_type, name, key_en)
            
            if domain == "binary_sensor":
                payload = self._build_binary_sensor_payload(base_payload, ha_conf)
            else:
                payload = self._build_sensor_payload(base_payload)

            # 將 ha_conf 傳入，套用強制覆寫
            payload = self._apply_common(payload, unit, icon, packet_type, domain, key_en, ha_conf)

            disc_topic = f"{self.discovery_prefix}/{domain}/jk_bms_{device_id}/{key_en}/config"
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
            if time.monotonic() - self.settings_last_publish.get(device_id, 0) < interval:
                return

        if self._safe_publish(state_topic, payload_dict, retain=False):
            self._last_state_publish[state_topic] = now
            if packet_type == 0x01:                                      # ← 移至成功後
                self.settings_last_publish[device_id] = time.monotonic()
            if packet_type in BMS_MAP:
                self.publish_discovery_for_packet_type(device_id, packet_type, BMS_MAP[packet_type])

_publisher_instance = None
def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
