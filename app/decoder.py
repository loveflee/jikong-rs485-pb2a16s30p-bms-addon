import struct
from typing import Dict, Any
from bms_registers import BMS_MAP

def extract_device_address(packet: bytes) -> int:
    try:
        return struct.unpack_from("<I", packet, 270)[0] if len(packet) >= 274 else 0
    except: return 0

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    # ✅ 還原 Master 指令解析
    if p_type == 0x10:
        return {
            "type": "master_cmd",
            "slave_id": packet[0],
            "function": f"0x{packet[1]:02X}",
            "register": f"0x{packet[2:4].hex().upper()}",
            "value": f"0x{packet[7:9].hex().upper()}"
        }

    # JK BMS 解析
    if p_type not in BMS_MAP: return {}
    res = {}
    for off, entry in BMS_MAP[p_type].items():
        name, _, dtype, conv, *_ = entry
        abs_off = 6 + off
        if abs_off < len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                res[name] = conv(raw) if conv else raw
            except: continue
    return res
