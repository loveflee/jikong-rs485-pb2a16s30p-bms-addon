# publisher.py
import json
import struct
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

        # MQTT client
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311)
        if username:
            self.client.username_pw_set(username=username, password=password)
        try:
            self.client.connect(host=broker, port=port, keepalive=60)
            self.client.loop_start()
            print(f"✅ 已連線到 MQTT {broker}:{port} (client_id={self.client_id})")
        except Exception as e:
            print(f"❌ 無法連線到 MQTT {broker}:{port} - {e}")

        # track last settings publish times per device
        self.settings_last_publish: Dict[int, float] = {}

        # to avoid repeatedly publishing discovery for same device+packet_type
        self._published_discovery = set()

    # util
    def get_value(self, data: bytes, offset: int, dtype: str):
        try:
            if dtype == 'B':
                return data[offset]
            if 's' in dtype:
                return struct.unpack_from(f'<{dtype}', data, offset)[0]
            return struct.unpack_from(f'<{dtype}', data, offset)[0]
        except Exception:
            return None

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

    def publish_discovery_for_packet_type(self, device_id: int, packet_type: int, data_map: Dict[int, Any]):
        """
        Publish discovery for all registers in a packet_type.
        Only publish once per (device_id, packet_type).
        """
        key = (device_id, packet_type)
        if key in self._published_discovery:
            return
        self._published_discovery.add(key)

        device_info = self._make_device_info(device_id)
        state_topic = f"{self.topic_prefix}/{device_id}/realtime" if packet_type == 0x02 else f"{self.topic_prefix}/{device_id}/settings"

        # publish sensors
        for offset in sorted(data_map.keys()):
            name, unit, dtype, _ = data_map[offset]
            object_id = f"reg_{packet_type}_{offset}"
            unique_id = f"jk_bms_{device_id}_{packet_type}_{offset}"
            value_key = name

            payload = {
                "name": f"{name}",
                "unique_id": unique_id,
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json['{value_key}'] }}}}",
                "device": device_info,
            }
            if unit not in ("Hex", "Bit", "Enum") and unit:
                payload["unit_of_measurement"] = unit

            topic = self._sensor_discovery_topic(device_id, object_id)
            try:
                self.client.publish(topic, json.dumps(payload), retain=True)
            except Exception as e:
                print(f"❌ publish discovery sensor failed: {e}")

        # also add binary sensors for charge/discharge switches (if relevant)
        # Discharge switch
        bs_payload = {
            "name": f"放电开关",
            "unique_id": f"jk_bms_{device_id}_discharge_switch",
            "state_topic": state_topic,
            "value_template": "{{ 'ON' if value_json['放电开关'] == True else 'OFF' }}",
            "device_class": "power",
            "device": device_info
        }
        try:
            self.client.publish(self._binary_sensor_discovery_topic(device_id, "discharge_switch"), json.dumps(bs_payload), retain=True)
        except Exception as e:
            print(f"❌ publish discharge bs failed: {e}")

        # Charge switch
        cs_payload = {
            "name": f"充电开关",
            "unique_id": f"jk_bms_{device_id}_charge_switch",
            "state_topic": state_topic,
            "value_template": "{{ 'ON' if value_json['充电开关'] == True else 'OFF' }}",
            "device_class": "power",
            "device": device_info
        }
        try:
            self.client.publish(self._binary_sensor_discovery_topic(device_id, "charge_switch"), json.dumps(cs_payload), retain=True)
        except Exception as e:
            print(f"❌ publish charge bs failed: {e}")

    def process_and_publish(self, data_packet: bytes, device_id: int, packet_type: int):
        if packet_type not in BMS_MAP:
            print(f"⚠️ 未知的封包類型: {hex(packet_type)}")
            return

        register_def = BMS_MAP[packet_type]
        print(f"\n========== 🚀 發布: Device [{device_id}] - Type [{hex(packet_type)}] ==========")

        base_index = 6
        payload_dict: Dict[str, Any] = {}

        for offset in sorted(register_def.keys()):
            name, unit, dtype, converter = register_def[offset]
            abs_offset = base_index + offset
            if abs_offset >= len(data_packet):
                continue
            raw_val = self.get_value(data_packet, abs_offset, dtype)
            if raw_val is not None:
                try:
                    final_val = converter(raw_val)
                except Exception:
                    final_val = raw_val
                payload_dict[name] = final_val

        # extra: parse common bit fields into clear booleans/labels
        # 放电状态
        discharge_val = payload_dict.get("放电状态")
        if isinstance(discharge_val, str) and discharge_val.startswith("0x"):
            try:
                raw = int(discharge_val, 16)
                payload_dict["放电开关"] = (raw & 0x1) == 1
            except Exception:
                pass

        # 充电状态
        charge_val = payload_dict.get("充电状态")
        if isinstance(charge_val, str) and charge_val.startswith("0x"):
            try:
                raw = int(charge_val, 16)
                payload_dict["充电开关"] = (raw & 0x1) == 1
            except Exception:
                pass

        # Settings rate-limiting
        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            if now - last_time < interval:
                print(f"⏱️ Settings 發佈節流: {now - last_time:.1f}s < {interval}s ，略過")
                return
            self.settings_last_publish[device_id] = now

        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"

        try:
            self.client.publish(state_topic, json.dumps(payload_dict), retain=False)
            print(f"✅ 已發布到 MQTT: {state_topic}")
        except Exception as e:
            print(f"❌ publish payload failed: {e}")

        # 發 discovery（只發一次）
        self.publish_discovery_for_packet_type(device_id, packet_type, register_def)

# helper to reuse single instance
_publisher_instance = None

def get_publisher(config_path: str = "/data/config.yaml"):
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
