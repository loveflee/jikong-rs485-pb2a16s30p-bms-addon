# =============================================================================
# publisher.py - V2.1.7 生產硬化版 (Production Hardened)
# 模組名稱：JK-BMS MQTT 數據發布模組
# 升級亮點：
#   - [Fix] 補齊 typing 定義，防止 Python 運行時 NameError。
#   - [Fix] 嚴格檢查 MQTT rc 狀態碼，確保數據鏈路真實可用。
#   - [Opt] 導入斷線監控 (on_disconnect)，強化現場網路除錯能力。
#   - [Opt] 增強 JSON 序列化安全性，攔截不合法數據 (NaN/Inf)。
#   - [Security] 維持物理節流與去抖動邏輯，阻斷 MQTT 風暴。
# =============================================================================

import json
import time
import yaml
import os
import logging
import paho.mqtt.client as mqtt
from typing import Dict, Any, Optional, Tuple, Set  # 🚀 [V2.1.7 Fix] 補齊類型定義

from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_publisher")

class MqttPublisher:
    """
    V2.1.7 硬化版：具備物理防護、連線狀態回饋與數據合法性檢查。
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
        
        # --- 物理防護緩衝區 ---
        self._last_state_publish: Dict[str, float] = {}      
        self._state_min_interval = 0.2     # 狀態發布最小間隔 200ms
        self._discovery_sent: Set[Tuple] = set()       
        self._availability_cache: Dict[int, str] = {}      
        self._availability_min_interval = 1.0  
        self._last_availability_publish: Dict[int, float] = {}

        # --- MQTT 核心初始化 ---
        broker = self.mqtt_cfg.get("host", "core-mosquitto")
        port = int(self.mqtt_cfg.get("port", 1883))
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311, clean_session=True)
        
        if self.mqtt_cfg.get("username") and self.mqtt_cfg.get("password"):
            self.client.username_pw_set(self.mqtt_cfg["username"], self.mqtt_cfg["password"])
        
        # 設定遺言與回調
        self.client.will_set(self.status_topic, payload="offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect  # 🚀 [V2.1.7 Opt] 監控斷線

        try:
            self.client.connect_async(broker, port, keepalive=60)
            self.client.loop_start()
            logger.info(f"📡 MQTT V2.1.7 硬化版啟動: {broker}:{port}")
        except Exception as e:
            logger.error(f"❌ MQTT 啟動失敗: {e}")

        self.settings_last_publish: Dict[int, float] = {}

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("✅ MQTT 已連線")
            self.client.publish(self.status_topic, payload="online", qos=1, retain=True)
        else:
            logger.warning(f"⚠️ MQTT 連線錯誤 rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        """🚀 [V2.1.7 新增] 物理斷線監控回調"""
        if rc != 0:
            logger.warning(f"⚠️ MQTT 非預期斷線 (rc={rc})，系統將自動重連")

    def _safe_publish(self, topic: str, payload, retain: bool = False, qos: int = 0):
        """🚀 [V2.1.7 Fix] 檢查 rc 回傳值與 JSON 合法性"""
        try:
            if isinstance(payload, (dict, list)):
                # [V2.1.7 Opt] 攔截 NaN/Inf 以防止非法 JSON 污染看板
                data = json.dumps(payload, allow_nan=False)
            else:
                data = payload
            
            info = self.client.publish(topic, payload=data, retain=retain, qos=qos)
            
            # 嚴格檢查連線狀態碼
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"MQTT publish 失敗 rc={info.rc} topic={topic}")
                return False
                
            return True
        except (ValueError, TypeError) as e:
            logger.error(f"JSON 序列化異常 (可能有 NaN 數據): {e} | Topic: {topic}")
            return False
        except Exception as e:
            logger.debug(f"MQTT 發布未知錯誤 ({topic}): {e}")
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
        
        # 🚀 [V2.1.7 Opt] 強化 Discovery 唯一性鍵值
        key = (device_id, packet_type, tuple(data_map.keys()))
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
        if packet_type == 0x10: return
        
        now = time.monotonic()
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

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
            if packet_type in BMS_MAP:
                self.publish_discovery_for_packet_type(device_id, packet_type, BMS_MAP[packet_type])

_publisher_instance = None
def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
