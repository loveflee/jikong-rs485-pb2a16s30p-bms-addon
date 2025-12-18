import json
import time
import yaml
import os
import logging
from typing import Dict, Any
import paho.mqtt.client as mqtt
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_publisher")

class MqttPublisher:
    """
    Python 版 MQTT 發布器 (新版 UI 對齊 + Master 指令支援)
    """
    
    def __init__(self, config_path: str = "/data/config.yaml"):
        if not os.path.exists(config_path):
            raise FileNotFoundError(config_path)

        with open(config_path, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f)

        # 🟢 修正：對齊新版 main.py 產出的階層式結構
        self.mqtt_cfg = full_cfg.get("mqtt", {})
        self.app_cfg = full_cfg.get("app", {})
        
        self.discovery_prefix = self.mqtt_cfg.get("discovery_prefix", "homeassistant")
        self.topic_prefix = self.mqtt_cfg.get("topic_prefix", "Jikong_BMS")
        self.client_id = self.mqtt_cfg.get("client_id", "jk_bms_monitor")

        self.status_topic = f"{self.topic_prefix}/status"

        # 🟢 修正：對齊新版 config 欄位名稱 (使用 host 而非 broker)
        broker = self.mqtt_cfg.get("host", "core-mosquitto")
        port = int(self.mqtt_cfg.get("port", 1883))
        username = self.mqtt_cfg.get("username")
        password = self.mqtt_cfg.get("password")

        self._connected = False
        self._broker = broker
        self._port = port

        # 初始化 MQTT 客戶端
        self.client = mqtt.Client(
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        if username and password:
            self.client.username_pw_set(username=username, password=password)

        # 設定遺囑 (LWT)
        self.client.will_set(self.status_topic, payload="offline", qos=1, retain=True)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect_async(self._broker, self._port, keepalive=60)
            self.client.loop_start() 
            logger.info(f"📡 MQTT 啟動連線至 {broker}:{port}")
        except Exception as e:
            logger.error(f"❌ MQTT 連線失敗: {e}")

        self.settings_last_publish: Dict[int, float] = {}
        self._published_discovery = set()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            logger.info("✅ MQTT 已連線")
            client.publish(self.status_topic, payload="online", qos=1, retain=True)
        else:
            logger.warning(f"⚠️ MQTT 連線錯誤 rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            logger.warning(f"⚠️ MQTT 非預期中斷")

    def _safe_publish(self, topic: str, payload: str, retain: bool = False):
        if not self._connected:
            return False
        try:
            self.client.publish(topic, payload=payload, retain=retain)
            return True
        except Exception:
            return False

    def _make_device_info(self, device_id: int) -> Dict[str, Any]:
        return {
            "identifiers": [f"jk_bms_{device_id}"],
            "manufacturer": "JiKong",
            "model": "PB2A16S30P",
            "name": f"JK BMS {device_id}", 
        }

    def publish_discovery_for_packet_type(self, device_id: int, packet_type: int, data_map: Dict[int, Any]):
        key = (device_id, packet_type)
        if key in self._published_discovery:
            return
        
        # 🟢 修正：若是 Master 指令 (0x10)，手動建立一個簡單的 Discovery 欄位
        if packet_type == 0x10:
            self._publish_master_command_discovery(device_id)
            self._published_discovery.add(key)
            return

        self._published_discovery.add(key)
        device_info = self._make_device_info(device_id)
        
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        for offset, entry in data_map.items():
            name_cn = entry[0]
            unit = entry[1]
            ha_type = entry[4] if len(entry) > 4 else "sensor"
            key_en = entry[6] if len(entry) > 6 else f"reg_{packet_type}_{offset}"

            base_id = f"jk_bms_{device_id}_{key_en}"
            payload = {
                "name": name_cn, 
                "unique_id": base_id,
                "object_id": base_id,
                "state_topic": state_topic,
                "device": device_info,
                "availability_topic": self.status_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
                "value_template": f"{{{{ value_json['{name_cn}'] }}}}"
            }
            
            if unit and unit not in ("Hex", "Bit", "Enum"):
                payload["unit_of_measurement"] = unit

            topic = f"{self.discovery_prefix}/{ha_type}/jk_bms_{device_id}/{key_en}/config"
            self._safe_publish(topic, json.dumps(payload), retain=True)

    def _publish_master_command_discovery(self, device_id: int):
        """為 Master 指令建立專屬的 HA Sensor"""
        device_info = self._make_device_info(device_id)
        base_id = f"jk_bms_{device_id}_master_cmd"
        
        payload = {
            "name": "最後控制指令",
            "unique_id": base_id,
            "object_id": base_id,
            "state_topic": f"{self.topic_prefix}/{device_id}/command",
            "device": device_info,
            "availability_topic": self.status_topic,
            "value_template": "{{ value_json.register }} -> {{ value_json.value }}",
            "icon": "mdi:remote"
        }
        topic = f"{self.discovery_prefix}/sensor/jk_bms_{device_id}/master_cmd/config"
        self._safe_publish(topic, json.dumps(payload), retain=True)

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        # 🟢 修正：Master 指令 (0x10) 的發布路徑
        if packet_type == 0x10:
            state_topic = f"{self.topic_prefix}/{device_id}/command"
            self._safe_publish(state_topic, json.dumps(payload_dict), retain=False)
            self.publish_discovery_for_packet_type(device_id, 0x10, {})
            return

        # Settings (0x01) 節流
        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 60))
            last_time = self.settings_last_publish.get(device_id, 0)
            if time.time() - last_time < interval:
                return
            self.settings_last_publish[device_id] = time.time()

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"
        
        self._safe_publish(state_topic, json.dumps(payload_dict), retain=False)

        if packet_type in BMS_MAP:
            self.publish_discovery_for_packet_type(device_id, packet_type, BMS_MAP[packet_type])

_publisher_instance = None
def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
