# publisher.py
import json
import time
import yaml
import os
from typing import Dict, Any
import paho.mqtt.client as mqtt
from bms_registers import BMS_MAP

class MqttPublisher:
    """
    Python 版 MQTT 發布器 (v2.1 LWT + Naming Fix)
    """
    
    def __init__(self, config_path: str = "/data/config.yaml"):
        if not os.path.exists(config_path):
            raise FileNotFoundError(config_path)

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        self.mqtt_cfg = cfg.get("mqtt", {})
        self.app_cfg = cfg.get("app", {})
        
        self.discovery_prefix = self.mqtt_cfg.get("discovery_prefix", "homeassistant")
        self.topic_prefix = self.mqtt_cfg.get("topic_prefix", "Jikong_BMS")
        self.client_id = self.mqtt_cfg.get("client_id", "jk_bms_monitor")

        # ✅ 新增：狀態 Topic (用於 LWT 和 Availability)
        self.status_topic = f"{self.topic_prefix}/status"

        broker = self.mqtt_cfg.get("broker", "127.0.0.1")
        # 兼容舊設定檔 host 欄位
        if not broker and "host" in self.mqtt_cfg:
            broker = self.mqtt_cfg["host"]
            
        port = int(self.mqtt_cfg.get("port", 1883))
        username = self.mqtt_cfg.get("username")
        password = self.mqtt_cfg.get("password")

        self._connected = False
        self._broker = broker
        self._port = port

        self.client = mqtt.Client(
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        if username:
            self.client.username_pw_set(username=username, password=password)

        # ✅ 新增：設定遺囑 (LWT)
        # 當程式崩潰或斷網時，Broker 會自動發布 "offline"
        # retain=True 是必須的，確保 HA 重啟後知道我們掛了
        self.client.will_set(self.status_topic, payload="offline", qos=1, retain=True)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect_async(self._broker, self._port, keepalive=60)
            self.client.loop_start() 
            print(f"✅ 已嘗試連線到 MQTT {broker}:{port}")
        except Exception as e:
            print(f"❌ 啟動 MQTT 連線失敗 {broker}:{port} - {e}")

        self.settings_last_publish: Dict[int, float] = {}
        self._published_discovery = set()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            print(f"✅ MQTT 已連線成功: {self._broker}:{self._port}")
            # ✅ 新增：連線成功後，立刻報平安 "online"
            client.publish(self.status_topic, payload="online", qos=1, retain=True)
        else:
            self._connected = False
            print(f"⚠️ MQTT 連線失敗 rc={rc}，將自動重試")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            print(f"⚠️ MQTT 非預期斷線 rc={rc}，將自動嘗試重連")
        else:
            print("ℹ️ MQTT 已正常斷線")

    def _safe_publish(self, topic: str, payload: str, retain: bool = False, retries: int = 3):
        for attempt in range(1, retries + 1):
            if not self._connected:
                time.sleep(1)
            try:
                result = self.client.publish(topic, payload=payload, retain=retain)
                rc = getattr(result, "rc", result[0] if isinstance(result, tuple) else 0)
                if rc == mqtt.MQTT_ERR_SUCCESS:
                    return True
                else:
                    time.sleep(1)
            except Exception as e:
                time.sleep(1)
        return False

    def _make_device_info(self, device_id: int) -> Dict[str, Any]:
        return {
            "identifiers": [f"jk_bms_{device_id}"],
            "manufacturer": "JiKong",
            "model": "PB2A16S30P",
            "name": f"JK BMS {device_id}", 
        }

    def publish_discovery_for_packet_type(
        self, device_id: int, packet_type: int, data_map: Dict[int, Any]
    ):
        key = (device_id, packet_type)
        if key in self._published_discovery:
            return
        self._published_discovery.add(key)

        device_info = self._make_device_info(device_id)
        
        state_topic = (
            f"{self.topic_prefix}/{device_id}/realtime"
            if packet_type == 0x02
            else f"{self.topic_prefix}/{device_id}/settings"
        )

        for offset in sorted(data_map.keys()):
            entry = data_map[offset]

            name_cn = entry[0]
            unit = entry[1]
            ha_type = entry[4] if len(entry) > 4 else "sensor"
            icon = entry[5] if len(entry) > 5 else None
            
            # ✅ 新增：讀取英文 Key (第 7 個元素)
            # 如果 bms_registers.py 還沒更新，則使用 offset 作為後備方案
            if len(entry) > 6:
                key_en = entry[6]
            else:
                key_en = f"reg_{packet_type}_{offset}"

            # 建立標準 ID
            # unique_id: 資料庫用 (jk_bms_15_cell_voltage_01)
            # object_id: 實體 ID 用 (sensor.jk_bms_15_cell_voltage_01)
            base_id = f"jk_bms_{device_id}_{key_en}"
            
            payload = {
                # ✅ 修正：名稱只放中文，不含設備名，解決 HA 顯示重複問題
                "name": name_cn, 
                
                # ✅ 修正：指定 object_id 以生成乾淨的英文 Entity ID
                "unique_id": base_id,
                "object_id": base_id,
                
                "state_topic": state_topic,
                "device": device_info,
                
                # ✅ 新增：Availability 設定 (狀態監控)
                "availability_topic": self.status_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
            }
            
            if icon:
                payload["icon"] = icon

            value_key = name_cn # JSON 裡的 key 還是中文 (為了相容 decoder)

            if ha_type == "binary_sensor":
                payload["payload_on"] = "1"
                payload["payload_off"] = "0"
                payload["value_template"] = f"{{{{ 1 if value_json['{value_key}'] in (1, True, '1', 'ON') else 0 }}}}"
                topic = f"{self.discovery_prefix}/binary_sensor/jk_bms_{device_id}/{key_en}/config"
            else:
                payload["value_template"] = f"{{{{ value_json['{value_key}'] }}}}"
                if unit and unit not in ("Hex", "Bit", "Enum"):
                    payload["unit_of_measurement"] = unit
                topic = f"{self.discovery_prefix}/sensor/jk_bms_{device_id}/{key_en}/config"

            self._safe_publish(topic, json.dumps(payload), retain=True)

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        # ... (節流邏輯保持不變) ...
        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            if now - last_time < interval:
                return
            self.settings_last_publish[device_id] = now

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"
        
        # ✅ 確認 retain=False
        self._safe_publish(state_topic, json.dumps(payload_dict), retain=False)

        register_def = BMS_MAP[packet_type]
        self.publish_discovery_for_packet_type(device_id, packet_type, register_def)

_publisher_instance = None

def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
