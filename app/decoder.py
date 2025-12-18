import struct
import logging
from typing import Dict, Any
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet: bytes) -> int:
    """提取 ID，即使是 0 也回傳。"""
    try:
        # 針對 JK 0x01 封包提取地址位 (Offset 270)
        if len(packet) >= 274:
            addr = struct.unpack_from("<I", packet, 270)[0]
            return addr
        return None
    except:
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    """
    雙協議解碼器：
    1. 處理 Master Modbus 寫入指令 (p_type = 16)
    2. 處理 JK BMS 廣播數據 (p_type = 1 或 2)
    """
    
    # ✅ 修正點：優先攔截 Master 指令 (0x10 = 16)，避免進入 BMS_MAP 查表
    if p_type == 0x10 or p_type == 16:
        try:
            return {
                "msg_type": "master_cmd",
                "slave_id": packet[0],
                "function": f"0x{packet[1]:02X}",
                "register": f"0x{packet[2:4].hex().upper()}",
                "value": f"0x{packet[7:9].hex().upper()}",
                "description": "Master Command Captured"
            }
        except Exception as e:
            logger.debug(f"解析 Master 指令失敗: {e}")
            return {}

    # --- 以下為 JK BMS 原有解析邏輯 ---
    if p_type not in BMS_MAP:
        return {}

    res = {}
    register_def = BMS_MAP[p_type]
    base_index = 6
    
    for off, entry in register_def.items():
        name, _, dtype, conv, *_ = entry
        abs_off = base_index + off
        if abs_off < len(packet):
            try:
                # 根據型別解包數據
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                # 執行轉換函數 (lambda)
                res[name] = conv(raw) if conv else raw
            except:
                continue
                
    return res
