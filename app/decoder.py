import struct
import logging
from typing import Dict, Any
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet: bytes) -> int:
    """提取 ID，即使是 0 也回傳。"""
    try:
        if len(packet) >= 274:
            # 讀取 Offset 270 (BMS Address)
            addr = struct.unpack_from("<I", packet, 270)[0]
            return addr
        return None
    except:
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    if p_type == 0x10:
        return {
            "msg_type": "master_cmd",
            "slave_id": packet[0],
            "register": f"0x{packet[2:4].hex().upper()}",
            "value": f"0x{packet[7:9].hex().upper()}"
        }

    if p_type not in BMS_MAP: return {}
    res = {}
    base_index = 6
    for off, entry in BMS_MAP[p_type].items():
        name, _, dtype, conv, *_ = entry
        abs_off = base_index + off
        if abs_off < len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                res[name] = conv(raw) if conv else raw
            except: continue
    return res
