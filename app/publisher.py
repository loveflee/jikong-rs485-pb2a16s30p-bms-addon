# publisher.py
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

        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311)
        if username:
            self.client.username_pw_set(username=username, password=password)

        try:
            self.client.connect(host=broker, port=port, keepalive=60)
            self.client.loop_start()
            logger.info("✅ MQTT 已連線: %s:%s (client_id=%s)", broker, port, self.client_id)
        except Exception as e:
            logger.error("❌ 無法連線到 MQTT %s:%s - %s", broker, port, e)

        # 設定封包發佈節流 (settings)
        self.settings_last_publish: Dict[int, float] = {}
        # 避免重複發 discovery
        self._published_discovery = set()

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
                logger.debug("📤 MQTT discovery 發佈: %s", topic)
            except Exception as e:
                logger.warning("❌ publish discovery %s failed: %s", ha_type, e)

    # ---------------- 實際發佈 payload ----------------

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        if packet_type not in BMS_MAP:
            logger.debug("⚠️ 未知的封包類型: %s", hex(packet_type))
            return

        # Settings 節流
        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            if now - last_time < interval:
                logger.info(
                    "⏱️ Settings 節流: device %s, %.1fs < %.1fs，略過",
                    device_id,
                    now - last_time,
                    interval,
                )
                return
            self.settings_last_publish[device_id] = now

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        try:
            self.client.publish(state_topic, json.dumps(payload_dict), retain=False)
            # 這裡 log 也保持簡潔
            logger.info("📡 BMS %s %s 更新已發佈到 MQTT", device_id, kind)
            logger.debug("📤 MQTT publish: %s => %s", state_topic, payload_dict)
        except Exception as e:
            logger.error("❌ publish payload failed: %s", e)

        # Discovery (只發一次)
        register_def = BMS_MAP[packet_type]
        self.publish_discovery_for_packet_type(device_id, packet_type, register_def)


_publisher_instance = None


def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
