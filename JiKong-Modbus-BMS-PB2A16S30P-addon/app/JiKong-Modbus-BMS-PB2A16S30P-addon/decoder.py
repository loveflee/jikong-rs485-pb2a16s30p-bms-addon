# decoder.py
#
# 專門負責：
#   - 從 0x01 封包中提取 Device Address (device_id)
#   - 按照 BMS_MAP 解出 payload_dict
#   - 做充電/放電 bit 解析
#
# 不做任何 MQTT / Discovery 相關處理。

import struct
import time
from typing import Dict, Any

from bms_registers import BMS_MAP


def extract_device_address(packet_0x01: bytes) -> int:
    """
    從 0x01 (Settings) 封包中提取 Device Address（設備地址）。
    bms_registers 定義 offset 264（相對 payload），
    實際索引 = header(6 bytes) + 264 = 270
    """
    try:
        print(f"📦 0x01 length = {len(packet_0x01)}")
        if len(packet_0x01) >= 274:  # 270 + 4 bytes
            raw = packet_0x01[270:274]
            print(f"🔍 raw addr bytes @270-273 = {raw.hex(' ')}")
            device_id = struct.unpack_from("<I", packet_0x01, 270)[0]
            print(f"🔑 解出 device_id = {device_id} (hex {device_id:#x})")
            return device_id
        else:
            print("⚠️ 0x01 封包長度不足 274，無法取得設備地址")
        return 0
    except Exception as e:
        print(f"❌ 提取設備地址失敗: {e}")
        return 0


def _get_value(data: bytes, offset: int, dtype: str):
    """
    通用的 struct unpack 小工具。
    """
    try:
        if dtype == "B":
            return data[offset]
        if "s" in dtype:
            return struct.unpack_from(f"<{dtype}", data, offset)[0]
        return struct.unpack_from(f"<{dtype}", data, offset)[0]
    except Exception:
        return None


def decode_packet_to_dict(
    data_packet: bytes,
    packet_type: int,
    *,
    base_index: int = 6,
) -> Dict[str, Any]:
    """
    依照 BMS_MAP 將 data_packet 解成 payload_dict。

    - packet_type: 0x01 or 0x02
    - base_index: payload 起始位置（目前都是 6）
    """
    if packet_type not in BMS_MAP:
        print(f"⚠️ 未知的封包類型: {hex(packet_type)}")
        return {}

    register_def = BMS_MAP[packet_type]
    payload_dict: Dict[str, Any] = {}

    for offset in sorted(register_def.keys()):
        entry = register_def[offset]
        name = entry[0]
        dtype = entry[2]
        converter = entry[3]

        abs_offset = base_index + offset
        if abs_offset >= len(data_packet):
            # 避免越界，例如 9001, 9002 這種虛擬 ID
            continue

        raw_val = _get_value(data_packet, abs_offset, dtype)
        if raw_val is not None:
            try:
                final_val = converter(raw_val)
            except Exception:
                final_val = raw_val
            payload_dict[name] = final_val

    # 額外 bit 解析邏輯（原本在 publisher 裡）
    # 這裡一起做，publisher 就只管 publish dict。
    if packet_type == 0x02:
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

    return payload_dict
