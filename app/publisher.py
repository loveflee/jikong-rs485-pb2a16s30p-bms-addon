# publisher.py
import json
import time
import yaml
import os
from typing import Dict, Any

import paho.mqtt.client as mqtt

from bms_registers import BMS_MAP


class MqttPublisher:
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

        broker = self.mqtt_cfg.get("broker", "127.0.0.1")
        port = int(self.mqtt_cfg.get("port", 1883))
        username = self.mqtt_cfg.get("username")
        password = self.mqtt_cfg.get("password")

        # ------- 新增：內部狀態 -------
        self._connected = False
        self._broker = broker
        self._port = port

        # ------- 建立 MQTT Client -------
        self.client = mqtt.Client(
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        if username:
            self.client.username_pw_set(username=username, password=password)

        # 自動重連延遲 (1~30 秒)
        # ※ paho v1/v2 都支援這個 API
        try:
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        except Exception:
            # 舊版 paho 沒這個也沒關係，只是少了 backoff 而已
            pass

        # ------- 綁定 callback -------
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        # ------- 使用 connect_async + loop_start 讓 client 自己重連 -------
        try:
            self.client.connect_async(self._broker, self._port, keepalive=60)
            self.client.loop_start()
            print(f"✅ 已嘗試連線到 MQTT {broker}:{port} (client_id={self.client_id})")
        except Exception as e:
            print(f"❌ 啟動 MQTT 連線失敗 {broker}:{port} - {e}")

        # 設定封包發佈節流 (settings)
        self.settings_last_publish: Dict[int, float] = {}
        # 避免重複發 discovery
        self._published_discovery = set()

    # ---------------- MQTT Callbacks ----------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            print(f"✅ MQTT 已連線成功: {self._broker}:{self._port}")
        else:
            # rc != 0 代表連線失敗，paho 會依 reconnect_delay 自動重試
            self._connected = False
            print(f"⚠️ MQTT 連線失敗 rc={rc}，將自動重試")

    def _on_disconnect(self, client, userdata, rc):
        # rc != 0 通常代表非正常斷線（例如 broker 重啟）
        self._connected = False
        if rc != 0:
            print(f"⚠️ MQTT 非預期斷線 rc={rc}，將自動嘗試重連")
        else:
            print("ℹ️ MQTT 已正常斷線")

    # ---------------- 安全發佈（含簡單重試） ----------------

    def _safe_publish(self, topic: str, payload: str, retain: bool = False, retries: int = 3):
        """
        包一層安全發佈：
        - 若尚未連線，會等一下再試
        - 發佈失敗時，做幾次重試，避免 broker 剛好重啟時包直接消失
        """
        for attempt in range(1, retries + 1):
            if not self._connected:
                # MQTT 尚未連線好，稍等一下再試
                time.sleep(1)
            try:
                result = self.client.publish(topic, payload=payload, retain=retain)
                # paho v1/v2 都會回一個 MQTTMessageInfo
                rc = getattr(result, "rc", result[0] if isinstance(result, tuple) else 0)
                if rc == mqtt.MQTT_ERR_SUCCESS:
                    # 成功就回傳
                    return True
                else:
                    print(f"⚠️ MQTT publish 失敗 (rc={rc})，第 {attempt}/{retries} 次重試...")
                    time.sleep(1)
            except Exception as e:
                print(f"❌ MQTT publish 發生例外: {e}，第 {attempt}/{retries} 次重試...")
                time.sleep(1)

        print(f"❌ MQTT publish 多次重試仍失敗，topic={topic}")
        return False

    # ---------------- MQTT Discovery ----------------

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

    # ---------------- 實際發佈 payload ----------------

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        if packet_type not in BMS_MAP:
            print(f"⚠️ 未知的封包類型: {hex(packet_type)}")
            return

        # Settings 節流
        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            if now - last_time < interval:
                # 這裡故意不再印一堆 log，保持乾淨
                return
            self.settings_last_publish[device_id] = now

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

#        try:
#            ok = self._safe_publish(state_topic, json.dumps(payload_dict), retain=False)
#            if ok and packet_type == 0x02:
#                # 這行會跟 main.py 的 log 配合：只留下你在意的關鍵資訊
#                print(f"📡 BMS {device_id} realtime 更新已發佈到 MQTT")
#        except Exception as e:
#            print(f"❌ publish payload failed: {e}")

        # Discovery (只發一次)
        register_def = BMS_MAP[packet_type]
        self.publish_discovery_for_packet_type(device_id, packet_type, register_def)


_publisher_instance = None


def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance


