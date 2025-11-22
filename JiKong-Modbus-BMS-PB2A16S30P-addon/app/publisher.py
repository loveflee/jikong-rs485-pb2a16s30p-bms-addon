# publisher.py
import json
import time
import struct
from typing import Dict, Any

import paho.mqtt.client as mqtt
import yaml

from bms_registers import BMS_MAP


# =================================================================
#  Config & MQTT Client
# =================================================================

class MqttPublisher:
    def __init__(self, config_path: str = "config.yaml"):
        # 載入 config.yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        self.mqtt_cfg = cfg.get("mqtt", {})
        self.app_cfg = cfg.get("app", {})
        self.discovery_prefix = self.mqtt_cfg.get("discovery_prefix", "homeassistant")
        self.topic_prefix = self.mqtt_cfg.get("topic_prefix", "bms")
        self.client_id = self.mqtt_cfg.get("client_id", "jk_bms_monitor")

        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv5)
        username = self.mqtt_cfg.get("username")
        password = self.mqtt_cfg.get("password")
        if username:
            self.client.username_pw_set(username=username, password=password)

        self.client.connect(
            host=self.mqtt_cfg.get("broker", "127.0.0.1"),
            port=int(self.mqtt_cfg.get("port", 1883)),
            keepalive=60,
        )
        self.client.loop_start()

        # 設定類封包(0x01) 每個 device 的「上次發布時間」
        self.settings_last_publish: Dict[int, float] = {}

    # -------------------------------------------------------------
    # 基礎工具
    # -------------------------------------------------------------
    def get_value(self, data: bytes, offset: int, dtype: str):
        """從 binary data 中提取數值"""
        try:
            if dtype == 'B':
                return data[offset]
            if 's' in dtype:
                return struct.unpack_from(f'<{dtype}', data, offset)[0]
            return struct.unpack_from(f'<{dtype}', data, offset)[0]
        except Exception:
            return None

    # -------------------------------------------------------------
    # HA Discovery 相關
    # -------------------------------------------------------------
    def _make_device_info(self, device_id: int) -> Dict[str, Any]:
        """Home Assistant device 區塊"""
        ident = f"jk_bms_{device_id}"
        return {
            "identifiers": [ident],
            "manufacturer": "JiKong",
            "model": "PB2A16S30P",
            "name": f"JK modbus BMS {device_id}",
        }

    def _sensor_discovery_topic(self, device_id: int, object_id: str) -> str:
        """homeassistant/sensor/<node_id>/<object_id>/config"""
        node_id = f"jk_bms_{device_id}"
        return f"{self.discovery_prefix}/sensor/{node_id}/{object_id}/config"

    def publish_discovery_for_packet_type(
        self,
        device_id: int,
        packet_type: int,
        data_map: Dict[int, Any]
    ):
        """
        為當前封包內所有欄位發送一次 Discovery Config。
        可在程序啟動時或第一次看到此 device 時發送。
        """
        device_info = self._make_device_info(device_id)

        for offset in sorted(data_map.keys()):
            name, unit, dtype, _ = data_map[offset]

            # object_id 使用英文+offset 避免中文亂碼
            # 例如： voltage_01_000, temperature_138 等
            # 這裡簡單用 f"reg_{packet_type}_{offset}"
            object_id = f"reg_{packet_type}_{offset}"
            unique_id = f"jk_bms_{device_id}_{packet_type}_{offset}"

            state_topic = f"{self.topic_prefix}/{device_id}/{packet_type}/state"

            # 用 attributes 中的 key 來對應 name，所以這邊就用同一個 key
            value_key = name

            payload = {
                "name": f"{name}",
                "unique_id": unique_id,
                "state_topic": state_topic,
                "unit_of_measurement": unit if unit not in ("Hex", "Bit", "Enum") else None,
                "value_template": f"{{{{ value_json['{value_key}'] }}}}",
                "device": device_info,
            }

            # 非數值資料不設單位
            if unit in ("Hex", "Bit", "Enum"):
                payload.pop("unit_of_measurement", None)

            topic = self._sensor_discovery_topic(device_id, object_id)
            self.client.publish(topic, json.dumps(payload), retain=True)

    # -------------------------------------------------------------
    # 主解析 + 發布
    # -------------------------------------------------------------
    def process_and_publish(self, data_packet: bytes, device_id: int, packet_type: int):
        """
        解析並發布數據
        :param data_packet: 二進制封包數據 (包含 Header)
        :param device_id: 從 0x01 封包提取出的設備地址 (Slave ID)
        :param packet_type: 0x01 (Settings) 或 0x02 (Realtime)
        """
        if packet_type not in BMS_MAP:
            print(f"⚠️ 未知的封包類型: {hex(packet_type)}")
            return

        register_def = BMS_MAP[packet_type]
        packet_name = "Realtime Data (0x02)" if packet_type == 0x02 else "Settings (0x01)"
        formatted_id = f"0x{device_id:08X}" if isinstance(device_id, int) else str(device_id)
        print(f"\n========== 🚀 發布: Device [{formatted_id}] - Type [{packet_name}] ==========")

        # Header (6 bytes) 之後才是數據
        base_index = 6

        payload_dict: Dict[str, Any] = {}

        for offset in sorted(register_def.keys()):
            name, unit, dtype, converter = register_def[offset]
            abs_offset = base_index + offset

            if abs_offset >= len(data_packet):
                continue

            raw_val = self.get_value(data_packet, abs_offset, dtype)
            if raw_val is not None:
                final_val = converter(raw_val)
                payload_dict[name] = final_val

        # MQTT 主題:
        # bms/<device_id>/realtime 或 bms/<device_id>/settings
        kind = "realtime" if packet_type == 0x02 else "settings"

        # Settings: 應用「半小時發佈一次」邏輯
        if packet_type == 0x01:
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            if now - last_time < interval:
                print(f"⏱️ Settings 內容距離上次發布未超過 {interval}s，略過本次設定發布。")
                return
            self.settings_last_publish[device_id] = now

        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"
        # 讓 discovery 用統一 state_topic（依 packet_type 區分也可以）
        # 另外提供一個 generic 主題用於所有欄位
        generic_state_topic = f"{self.topic_prefix}/{device_id}/{packet_type}/state"

        # 發布資料
        self.client.publish(state_topic, json.dumps(payload_dict), retain=False)
        self.client.publish(generic_state_topic, json.dumps(payload_dict), retain=False)

        print(f"✅ 已發布到 MQTT: {state_topic}")
        print("=" * 70 + "\n")

        # 第一次看到某個 device 某個 packet_type 時，順便發 discovery
        # （如果不想每次都發，可以自行加判斷 flag）
        self.publish_discovery_for_packet_type(device_id, packet_type, register_def)


# 建立全域 publisher 供 main.py 使用
_publisher_instance: MqttPublisher | None = None


def get_publisher() -> MqttPublisher:
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher("config.yaml")
    return _publisher_instance