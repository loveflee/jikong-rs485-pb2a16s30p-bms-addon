# decoder.py
import struct
from typing import Dict, Any

from bms_registers import BMS_MAP


HEADER_LEN = 6  # 0x55 0xAA 0xEB 0x90 + 2 bytes


def extract_device_address(packet_0x01: bytes) -> int:
    """
    從 0x01 (Settings) 封包中提取 Device Address。
    bms_registers 定義 offset 264（相對 payload），因此實際索引 = header(6) + 264 = 270
    """
    try:
        print(f"📦 0x01 length = {len(packet_0x01)}")
        if len(packet_0x01) >= 274:  # 270 + 4 bytes
            raw = packet_0x01[270:274]
            print(f"🔍 raw addr bytes @270-273 = {raw.hex(' ')}")
            device_id = struct.unpack_from("<I", packet_0x01, 270)[0]
            print(f"🔑 解析得到 device_id = {device_id} (hex {device_id:#x})")
            return device_id
        else:
            print("⚠️ 0x01 封包長度不足 274，無法取得設備地址")
        return 0
    except Exception as e:
        print(f"❌ 提取設備地址失敗: {e}")
        return 0


def get_value(data: bytes, offset: int, dtype: str):
    """小工具：依 dtype 從 data 中讀取數值。"""
    try:
        if dtype == "B":
            return data[offset]
        if "s" in dtype:
            return struct.unpack_from(f"<{dtype}", data, offset)[0]
        return struct.unpack_from(f"<{dtype}", data, offset)[0]
    except Exception:
        return None


def decode_packet(packet: bytes, packet_type: int) -> Dict[str, Any]:
    """
    將原始封包解析為 payload dict。
    - 不管 MQTT、不管 discovery，只單純把 BMS_MAP 定義的欄位轉成 dict。
    - 這裡不做「暫存 0x02」「綁定 ID」，那部份在 main.py 完成。
    """
    if packet_type not in BMS_MAP:
        print(f"⚠️ 未知的封包類型: {hex(packet_type)}")
        return {}

    register_def = BMS_MAP[packet_type]
    base_index = 6  # Header 長度
    payload: Dict[str, Any] = {}

    for offset in sorted(register_def.keys()):
        entry = register_def[offset]
        name = entry[0]
        dtype = entry[2]
        converter = entry[3]

        abs_offset = base_index + offset
        if abs_offset >= len(packet):
            # 封包不足以讀取這個 offset → 略過
            continue

        raw_val = get_value(packet, abs_offset, dtype)
        if raw_val is not None:
            try:
                final_val = converter(raw_val)
            except Exception:
                final_val = raw_val
            payload[name] = final_val

    # 0x02 額外解析充放電開關 (虛擬欄位)
    if packet_type == 0x02:
        # 放电状态 → 放电开关
        discharge_val = payload.get("放电状态")
        if isinstance(discharge_val, str) and discharge_val.startswith("0x"):
            try:
                raw = int(discharge_val, 16)
                payload["放电开关"] = (raw & 0x1) == 1
            except Exception:
                pass

        # 充电状态 → 充电开关
        charge_val = payload.get("充电状态")
        if isinstance(charge_val, str) and charge_val.startswith("0x"):
            try:
                raw = int(charge_val, 16)
                payload["充电开关"] = (raw & 0x1) == 1
            except Exception:
                pass

    return payload
