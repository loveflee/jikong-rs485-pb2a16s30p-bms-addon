import struct
import logging
from typing import Dict, Any
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet: bytes) -> int:
    try:
        if len(packet) >= 274:
            return struct.unpack_from("<I", packet, 270)[0]
        return None
    except: return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    # ✅ 關鍵修正：攔截 0x10 (16)，不讓它進入 BMS_MAP 查表
    if p_type == 0x10 or p_type == 16:
        try:
            return {
                "msg_type": "master_cmd",
                "slave_id": packet[0],
                "register": f"0x{packet[2:4].hex().upper()}",
                "value": f"0x{packet[7:9].hex().upper()}"
            }
        except: return {}

    # JK BMS 解析邏輯
    if p_type not in BMS_MAP: return {}
    
    res = {}
    register_def = BMS_MAP[p_type]
    base_index = 6
    for off, entry in register_def.items():
        name, _, dtype, conv, *_ = entry
        abs_off = base_index + off
        if abs_off < len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                res[name] = conv(raw) if conv else raw
            except: continue
    return res
