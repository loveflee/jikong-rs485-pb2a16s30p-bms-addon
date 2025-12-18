import struct
import logging
from typing import Dict, Any
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet_0x01: bytes) -> int:
    try:
        if len(packet_0x01) >= 274:
            return struct.unpack_from("<I", packet_0x01, 270)[0]
        return 0
    except Exception:
        return 0

def decode_packet(packet: bytes, packet_type: int) -> Dict[str, Any]:
    # ✅ 還原 Master 指令解析邏輯
    if packet_type == 0x10:
        try:
            return {
                "msg_type": "master_command",
                "slave_id": packet[0],
                "function": packet[1],
                "register": f"0x{packet[2:4].hex().upper()}",
                "value_raw": packet[7:9].hex().upper(),
                "description": "Master Register Write"
            }
        except Exception:
            return {}

    # JK BMS 解析
    if packet_type not in BMS_MAP: return {}
    
    register_def = BMS_MAP[packet_type]
    payload = {}
    base_index = 6

    for offset, entry in register_def.items():
        name, _, dtype, converter, *_ = entry
        abs_offset = base_index + offset
        if abs_offset < len(packet):
            try:
                raw_val = struct.unpack_from(f"<{dtype}", packet, abs_offset)[0]
                payload[name] = converter(raw_val) if converter else raw_val
            except Exception:
                continue
    return payload
