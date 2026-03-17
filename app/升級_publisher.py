import json
import time
import yaml
import os
import logging
import paho.mqtt.client as mqtt
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_publisher")

class MqttPublisher:
    """
    V2.1.5 硬化版：導入 HAManager V2.9.4 的物理防護邏輯
    核心亮點：徹底阻斷 MQTT Storm、雙重狀態矩陣、物理去抖。
    """
    def __init__(self, config_path: str = "/data/config.yaml"):
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"找不到設定檔: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f)
        
        self.mqtt_cfg = full_cfg.get("mqtt", {})
        self.app_cfg = full_cfg.get("app", {})
        
        # --- 基礎配置 ---
        self.discovery_prefix = self.mqtt_cfg.get("discovery_prefix", "homeassistant")
        self.topic_prefix = self.mqtt_cfg.get("topic_prefix", "Jikong_BMS")
        self.client_id = self.mqtt_cfg.get("client_id", "jk_bms_monitor")
        self.status_topic = f"{self.topic_prefix}/status"
        
        # --- [🚀 V2.9.4 物理防護導入] ---
        self._last_state_publish = {}      # 格式: {topic: timestamp}
        self._state_min_interval = 0.2     # 狀態發布最小間隔 200ms
        self._discovery_sent = set()       # 防禦 Discovery Storm 旗標
        self._availability_cache = {}      # 格式: {device_id: bool}
        self._availability_min_interval = 1.0  # 狀態切換冷卻 1.0s
        self._last_availability_publish = {}

        # --- MQTT 初始化 ---
        broker = self.mqtt_cfg.get("host", "core-mosquitto")
        port = int(self.mqtt_cfg.get("port", 1883))
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311, clean_session=True)
        
        if self.mqtt_cfg.get("username") and self.mqtt_cfg.get("password"):
            self.client.username_pw_set(self.mqtt_cfg["username"], self.mqtt_cfg["password"])
        
        self.client.will_set(self.status_topic, payload="offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        
        try:
            self.client.connect_async(broker, port, keepalive=60)
            self.client.loop_start()
            logger.info(f"📡 MQTT 硬化版啟動: {broker}:{port}")
        except Exception as e:
            logger.error(f"❌ MQTT 啟動失敗: {e}")

        self.settings_last_publish = {}

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("✅ MQTT 已連線")
            self.client.publish(self.status_topic, payload="online", qos=1, retain=True)
        else:
            logger.warning(f"⚠️ MQTT 連線錯誤 rc={rc}")

    def _safe_publish(self, topic: str, payload, retain: bool = False, qos: int = 0):
        """[🚀 V2.9.4 導入] 安全發布：攔截所有序列化與網路例外"""
        try:
            if isinstance(payload, (dict, list)):
                data = json.dumps(payload)
            else:
                data = payload
            self.client.publish(topic, payload=data, retain=retain, qos=qos)
            return True
        except Exception as e:
            logger.debug(f"發布失敗 ({topic}): {e}")
            return False

    def publish_device_status(self, device_id: int, status: str):
        """[🚀 V2.9.4 導入] 物理去抖 (Debounce) 的設備可用性發布"""
        now = time.monotonic()
        last_pub = self._last_availability_publish.get(device_id, 0)
        
        # 1.0s 內禁止頻繁切換狀態 (防止網路抖動)
        if status == self._availability_cache.get(device_id) and (now - last_pub < self._availability_min_interval):
            return

        topic = f"{self.topic_prefix}/{device_id}/status"
        if self._safe_publish(topic, payload=status, retain=True, qos=1):
            self._availability_cache[device_id] = status
            self._last_availability_publish[device_id] = now
            logger.info(f"🔄 設備狀態同步: BMS {device_id} -> {status}")

    def publish_discovery_for_packet_type(self, device_id: int, packet_type: int, data_map: Dict[int, Any]):
        """[🚀 V2.9.4 導入] 防禦 Discovery Storm 旗標"""
        # 排除指令包
        if packet_type == 0x10: return
        
        # 防重發機制
        key = (device_id, packet_type)
        if key in self._discovery_sent: return
        self._discovery_sent.add(key)

        device_info = {
            "identifiers": [f"jk_bms_{device_id}"],
            "manufacturer": "JiKong (JK-BMS)",
            "model": "PB2A16S30P (Hardened)",
            "name": f"JK BMS {device_id if device_id != 0 else '0 (Master)'}",
        }
        
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        for offset, entry in data_map.items():
            name_cn = entry[0]
            unit = entry[1]
            ha_type = entry[4] if len(entry) > 4 else "sensor"
            icon = entry[5] if len(entry) > 5 else None
            key_en = entry[6] if len(entry) > 6 else f"reg_{packet_type}_{offset}"
            
            payload = {
                "name": name_cn,
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
            if icon: payload["icon"] = icon
            if unit and unit not in ("Hex", "Bit", "Enum"):
                payload["unit_of_measurement"] = unit
            
            disc_topic = f"{self.discovery_prefix}/{ha_type}/jk_bms_{device_id}/{key_en}/config"
            self._safe_publish(disc_topic, payload, retain=True, qos=1)

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        """[🚀 V2.9.4 導入] 物理節流 (200ms Throttle) 狀態發布"""
        if packet_type == 0x10: return
        
        now = time.monotonic()
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        # 節流判定：如果發得太快，直接攔截，保護 MQTT Broker
        last_pub = self._last_state_publish.get(state_topic, 0)
        if now - last_pub < self._state_min_interval:
            return

        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 60))
            if time.time() - self.settings_last_publish.get(device_id, 0) < interval:
                return
            self.settings_last_publish[device_id] = time.time()

        if self._safe_publish(state_topic, payload_dict, retain=False):
            self._last_state_publish[state_topic] = now
            # 自動觸發 Discovery (如果尚未發布)
            if packet_type in BMS_MAP:
                self.publish_discovery_for_packet_type(device_id, packet_type, BMS_MAP[packet_type])

_publisher_instance = None
def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
