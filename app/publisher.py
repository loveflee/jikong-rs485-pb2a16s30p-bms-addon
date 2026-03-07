import json
import time
import yaml
import os
import logging
from typing import Dict, Any, Optional
import paho.mqtt.client as mqtt
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_publisher")

class MqttPublisher:
    """
    v2.0.9 MQTT 發布器：支援單機 LWT 與雙重狀態矩陣
    """

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

        broker = self.mqtt_cfg.get("host", "core-mosquitto")
        port = int(self.mqtt_cfg.get("port", 1883))
        username = self.mqtt_cfg.get("username")
        password = self.mqtt_cfg.get("password")

        self._connected = False
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311, clean_session=True)

        if username and password:
            self.client.username_pw_set(username=username, password=password)

        self.client.will_set(self.status_topic, payload="offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect_async(broker, port, keepalive=60)
            self.client.loop_start()
            logger.info(f"📡 MQTT 啟動: {broker}:{port}")
        except Exception as e:
            logger.error(f"❌ MQTT 啟動失敗: {e}")

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

    def _safe_publish(self, topic: str, payload: str, retain: bool = False):
        try:
            self.client.publish(topic, payload=payload, retain=retain, qos=0)
            return True
        except Exception as e:
            # 依賴底層例外捕捉，不吞掉斷線時的其他錯誤
            logger.debug(f"發布失敗 ({topic}): {e}")
            return False

    def _make_device_info(self, device_id: int) -> Dict[str, Any]:
        """
        更新設備製造商與型號資訊
        """
        return {
            "identifiers": [f"jk_bms_{device_id}"],
            "manufacturer": "JiKong (JK-BMS)",
            "model": "PB2A16S30P (RS485/Parallel)",
            "name": f"JK BMS {device_id if device_id != 0 else '0 (Master)'}",
        }

    # 🟢 [新增] 單機狀態發布
    def publish_device_status(self, device_id: int, status: str):
        """發布單一電池的在線/離線狀態"""
        topic = f"{self.topic_prefix}/{device_id}/status"
        self._safe_publish(topic, payload=status, retain=True)
        if status == "online":
            logger.info(f"🔄 設備上線: BMS {device_id}")

    def publish_discovery_for_packet_type(self, device_id: int, packet_type: int, data_map: Dict[int, Any]):
        """註冊 HA 實體"""
        key = (device_id, packet_type)
        if key in self._published_discovery: return

        # ⛔ 隱藏邏輯：如果是指令包 (0x10)，直接忽略，不註冊感測器
        if packet_type == 0x10:
            return

        self._published_discovery.add(key)
        device_info = self._make_device_info(device_id)
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        for offset, entry in data_map.items():
            name_cn = entry[0]
            unit = entry[1]
            ha_type = entry[4] if len(entry) > 4 else "sensor"
			# [修正 1] 抓取第 5 個位置的圖示設定
			icon = entry[5] if len(entry) > 5 else None
            key_en = entry[6] if len(entry) > 6 else f"reg_{packet_type}_{offset}"

            base_id = f"jk_bms_{device_id}_{key_en}"
            payload = {
                "name": name_cn,
                "unique_id": base_id,
                "object_id": base_id,
                "state_topic": state_topic,
                "device": device_info,
                # 🟢 [修改] 替換為雙重可用性矩陣 (閘道器存活 + 單機存活)
                "availability": [
                    {"topic": self.status_topic},
                    {"topic": f"{self.topic_prefix}/{device_id}/status"}
                ],
                "availability_mode": "all",
                "payload_available": "online",
                "payload_not_available": "offline",
#                "value_template": f"{{{{ value_json['{name_cn}'] }}}}"
		# [修改] 改去讀 MQTT 裡的英文 Key
		"value_template": f"{{{{ value_json['{key_en}'] }}}}"
            }

            # 🟢 [修正 2] 如果有定義圖示，就寫進 HA 的設定檔裡
            if icon:
                payload["icon"] = icon

			# 定義 binary_sensor 的 ON/OFF 映射
            if ha_type == "binary_sensor":
                payload["payload_on"] = "1"
                payload["payload_off"] = "0"

            if unit and unit not in ("Hex", "Bit", "Enum"):
                payload["unit_of_measurement"] = unit

            topic = f"{self.discovery_prefix}/{ha_type}/jk_bms_{device_id}/{key_en}/config"
            self._safe_publish(topic, json.dumps(payload), retain=True)

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        """發布數據至 MQTT"""

        # ⛔ 隱藏邏輯：如果是指令包 (0x10)，直接忽略，不發布數據
        if packet_type == 0x10:
            return

        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 60))
            if time.time() - self.settings_last_publish.get(device_id, 0) < interval:
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
