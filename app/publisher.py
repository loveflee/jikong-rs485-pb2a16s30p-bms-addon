# publisher.py
import json
import time
import yaml
import os
import sys
import logging
from typing import Dict, Any, Optional

import paho.mqtt.client as mqtt
from bms_registers import BMS_MAP

# 獲取 logger (與 main.py 共用設定)
logger = logging.getLogger("jk_bms_mqtt")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

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

        self.broker = self.mqtt_cfg.get("broker", "127.0.0.1")
        self.port = int(self.mqtt_cfg.get("port", 1883))
        self.username = self.mqtt_cfg.get("username")
        self.password = self.mqtt_cfg.get("password")

        # 初始化 Client
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311)
        if self.username:
            self.client.username_pw_set(username=self.username, password=self.password)

        # 設定 Callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        
        # 設定自動重連延遲 (指數退避: 1s -> 2s -> ... -> 60s)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        # 嘗試初始連線 (帶重試機制)
        self._connect_loop()

        # 啟動背景執行緒處理網路流量 (包含自動重連)
        self.client.loop_start()

        # 設定封包發佈節流 (settings)
        self.settings_last_publish: Dict[int, float] = {}
        # 避免重複發 discovery
        self._published_discovery = set()
        
        # 標記連線狀態
        self.connected = False

    def _connect_loop(self):
        """嘗試連線直到成功，避免 Add-on 啟動時 Broker 還沒好就 Crash"""
        while True:
            try:
                logger.info(f"⏳ 正在連線到 MQTT Broker {self.broker}:{self.port} ...")
                self.client.connect(host=self.broker, port=self.port, keepalive=60)
                logger.info("✅ MQTT 連線指令已發送")
                return
            except Exception as e:
                logger.error(f"❌ MQTT 連線失敗: {e}，5 秒後重試...")
                time.sleep(5)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logger.info(f"✅ 已成功連線到 MQTT Broker (rc={rc})")
            # 連線成功後，可以考慮重新發送 Discovery (如果是重連)
            # 但這裡先保持簡單，依賴 main loop 觸發
        else:
            self.connected = False
            logger.error(f"❌ MQTT 連線拒絕，回傳碼: {rc}")

    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logger.warning(f"⚠️ MQTT 意外斷線 (rc={rc})，Paho 將嘗試自動重連...")
        else:
            logger.info("ℹ️ MQTT 已主動斷線")

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
                self.client.publish(topic, json.dumps(payload), retain=True)
            except Exception as e:
                logger.error(f"❌ publish discovery {ha_type} failed: {e}")

    # ---------------- 實際發佈 payload ----------------

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        # 檢查連線狀態，雖然 publish 會 queue 住，但若斷線太久不想一直印 log
        if not self.connected:
            # 可以選擇在這裡 return，或者讓 paho 把訊息 cache 住等待重連
            # logger.debug("⚠️ MQTT 目前斷線中，訊息將進入佇列等待發送...")
            pass

        if packet_type not in BMS_MAP:
            logger.warning(f"⚠️ 未知的封包類型: {hex(packet_type)}")
            return

        # Settings 節流
        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            if now - last_time < interval:
                # logger.debug(f"⏱️ Settings 發佈節流: {now - last_time:.1f}s < {interval}s，略過")
                return
            self.settings_last_publish[device_id] = now

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        try:
            info = self.client.publish(state_topic, json.dumps(payload_dict), retain=False)
            # info.is_published() 可以檢查是否送出，但在 loop_start 模式下是非同步的
            # logger.debug(f"✅ 已發佈到 MQTT: {state_topic}")
        except Exception as e:
            logger.error(f"❌ publish payload failed: {e}")

        # Discovery (只發一次，內部有 set 控制)
        try:
            register_def = BMS_MAP[packet_type]
            self.publish_discovery_for_packet_type(device_id, packet_type, register_def)
        except Exception as e:
             logger.error(f"❌ Discovery Logic Error: {e}")


_publisher_instance = None

def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
