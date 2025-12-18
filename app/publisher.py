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
    MQTT 發佈器類別 (LWT 升級版)：
    支援 Last Will and Testament，當程式斷線時自動宣告 Offline。
    """
    
    def __init__(self, config_path: str = "/data/config.yaml"):
        if not os.path.exists(config_path):
            raise FileNotFoundError(config_path)

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        self.mqtt_cfg = cfg.get("mqtt", {})
        self.app_cfg = cfg.get("app", {})
        
        self.discovery_prefix = self.mqtt_cfg.get("discovery_prefix", "homeassistant")
        self.topic_prefix = self.mqtt_cfg.get("topic_prefix", "bms")
        self.client_id = self.mqtt_cfg.get("client_id", "jk_bms_monitor")

        # ✅ 新增：定義狀態 Topic
        self.status_topic = f"{self.topic_prefix}/status"

        broker = self.mqtt_cfg.get("broker", "127.0.0.1")
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
        # 當 Python 程式被殺掉或斷網時，Broker 會自動發 "offline"
        # retain=True 是必須的，這樣 HA 重啟才知道你是死的
        self.client.will_set(
            self.status_topic, 
            payload="offline", 
            qos=1, 
            retain=True
        )

        try:
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        except Exception:
            pass

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect_async(self._broker, self._port, keepalive=60)
            self.client.loop_start() 
            print(f"✅ 已嘗試連線到 MQTT {broker}:{port} (client_id={self.client_id})")
        except Exception as e:
            print(f"❌ 啟動 MQTT 連線失敗 {broker}:{port} - {e}")

        self.settings_last_publish: Dict[int, float] = {}
        self._published_discovery = set()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            print(f"✅ MQTT 已連線成功: {self._broker}:{self._port}")
            
            # ✅ 新增：連線成功後，立刻報平安 "online"
            # retain=True 代表「我現在活著」的狀態會被 Broker 記住
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
                    print(f"⚠️ MQTT publish 失敗 (rc={rc})，第 {attempt}/{retries} 次重試...")
                    time.sleep(1)
            except Exception as e:
                print(f"❌ MQTT publish 發生例外: {e}，第 {attempt}/{retries} 次重試...")
                time.sleep(1)

        print(f"❌ MQTT publish 多次重試仍失敗，topic={topic}")
        return False

    def _make_device_info(self, device_id: int) -> Dict[str, Any]:
        ident = f"jk_bms_{device_id}"
        return {
            "identifiers": [ident],
            "manufacturer": "JiKong",
            "model": "PB2A16S30P",
            "name": f"JK modbus BMS {device_id}",
        }

    def _sensor_discovery_topic(self, device_id: int, object_id: str) -> str:
        node_id = f"jk_bms_{device_id}"
        return f"{self.discovery_prefix}/sensor/{node_id}/{object_id}/config"

    def _binary_sensor_discovery_topic(self, device_id: int, object_id: str) -> str:
        node_id = f"jk_bms_{device_id}"
        return f"{self.discovery_prefix}/binary_sensor/{node_id}/{object_id}/config"

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

            name = entry[0]
            unit = entry[1]
            ha_type = entry[4] if len(entry) > 4 else "sensor" 
            icon = entry[5] if len(entry) > 5 else None

            object_id = f"reg_{packet_type}_{offset}"
            unique_id = f"jk_bms_{device_id}_{packet_type}_{offset}"
            value_key = name

            payload = {
                "name": name,
                "unique_id": unique_id,
                "state_topic": state_topic,
                "device": device_info,
                
                # ✅ 新增：Availability 設定
                # 告訴 HA：如果要確認這個 Sensor 能不能用，請去看 status_topic
                "availability_topic": self.status_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
            }
            if icon:
                payload["icon"] = icon

            if ha_type == "binary_sensor":
                payload["payload_on"] = "1"
                payload["payload_off"] = "0"
                payload[
                    "value_template"
                ] = f"{{{{ 1 if value_json['{value_key}'] in (1, True, '1', 'ON') else 0 }}}}"
                topic = self._binary_sensor_discovery_topic(device_id, object_id)
            else:
                payload["value_template"] = f"{{{{ value_json['{value_key}'] }}}}"
                if unit and unit not in ("Hex", "Bit", "Enum"):
                    payload["unit_of_measurement"] = unit
                topic = self._sensor_discovery_topic(device_id, object_id)

            try:
                self._safe_publish(topic, json.dumps(payload), retain=True)
            except Exception as e:
                print(f"❌ publish discovery {ha_type} failed: {e}")

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        if packet_type not in BMS_MAP:
            print(f"⚠️ 未知的封包類型: {hex(packet_type)}")
            return

        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            if now - last_time < interval:
                return
            self.settings_last_publish[device_id] = now

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"
        
        try:
            # ✅ Sensor 數據 retain=False (預設就是 False)，確保離線時不會殘留
            ok = self._safe_publish(state_topic, json.dumps(payload_dict), retain=False)
            if ok and packet_type == 0x02:
                pass
        except Exception as e:
            print(f"❌ publish payload failed: {e}")

        register_def = BMS_MAP[packet_type]
        self.publish_discovery_for_packet_type(device_id, packet_type, register_def)


_publisher_instance = None

def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
