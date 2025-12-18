import struct
import logging
from typing import Dict, Any, Optional
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet: bytes) -> Optional[int]:
    """
    從 JK BMS 的 0x01 (Settings) 封包中提取硬體位址。
    這是判定數據歸屬最權威的依據。
    """
    try:
        # JK BMS 地址位於 Offset 270 (包含標頭後的相對位置)
        # 封包結構為 55 AA EB 90 ... [Payload]
        if len(packet) >= 278:
            # 使用小端 4 字節整數讀取
            return struct.unpack_from("<I", packet, 270 + 4)[0]
        return None
    except Exception as e:
        logger.debug(f"提取設備地址失敗: {e}")
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    """
    多協議解碼器：
    1. 處理 Modbus 0x10 (Master 控制指令)
    2. 處理 JK BMS 0x01/0x02 (廣播數據)
    """
    # ✅ 核心升級：Modbus 指令解讀 (Master 點名與控制行為)
    if p_type == 0x10 or p_type == 16:
        try:
            # 標準 Modbus RTU 寫入指令格式 (11 bytes):
            # [ID][10][Reg_High][Reg_Low][Qty_H][Qty_L][Len][Val_H][Val_L][CRC_H][CRC_L]
            return {
                "msg_type": "master_cmd",
                "slave_id": packet[0],
                "register": f"0x{packet[2:4].hex().upper()}",
                "value_hex": f"0x{packet[7:9].hex().upper()}",
                "value_int": struct.unpack(">H", packet[7:9])[0],
                "description": "監聽到 Master 控制指令"
            }
        except Exception as e:
            logger.error(f"Modbus 0x10 解析失敗: {e}")
            return {}

    # ✅ JK BMS 標準解析邏輯 (依賴 BMS_MAP)
    if p_type not in BMS_MAP:
        return {}
    
    res = {}
    register_def = BMS_MAP[p_type]
    
    # JK 封包 Payload 起始位置 (55 AA EB 90 [Len] [Type] ...)
    # 標頭 4 + 長度 2 + 類型 1 = 7，但通常偏移定義是相對於標頭結束後
    base_index = 6 
    
    for off, entry in register_def.items():
        # entry 結構範例: ("總電壓", "V", "H", lambda x: x * 0.01, "sensor", "mdi:bolt", "total_voltage")
        name = entry[0]
        dtype = entry[2]
        conv = entry[3] if len(entry) > 3 else None
        
        abs_off = base_index + off
        if abs_off + struct.calcsize(f"<{dtype}") <= len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                res[name] = conv(raw) if conv else raw
            except Exception:
                continue
                
    return res
