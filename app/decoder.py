# decoder.py

import struct
import logging
from typing import Dict, Any, Optional
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet: bytes) -> Optional[int]:
    """
    從 JK BMS 的 0x01 (Settings) 封包中提取硬體位址。
    v2.0.5 修正：同時探測 Offset 274 和 276，專治 Master 地址漂移。
    """
    try:
        # 策略 1: 檢查標準偏移 274
        val_274 = None
        if len(packet) >= 278:
            val_274 = struct.unpack_from("<I", packet, 274)[0]
            if val_274 == 0: return 0  # 抓到了！是 Master

        # 策略 2: 檢查偏移 276 (某些韌體版本)
        if len(packet) >= 280:
            val_276 = struct.unpack_from("<I", packet, 276)[0]
            if val_276 == 0: return 0  # 抓到了！是 Master

        # 如果都不是 0，回傳 274 的值 (預設 Slave 行為)
        return val_274
    except Exception as e:
        logger.debug(f"提取設備地址失敗: {e}")
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    # (維持不變，與上一版相同)
    if p_type == 0x10 or p_type == 16:
        try:
            target_sid = packet[0]
            reg_addr = f"0x{packet[2:4].hex().upper()}"
            val_hex = f"0x{packet[7:9].hex().upper()}"
            val_int = struct.unpack(">H", packet[7:9])[0]
            
            return {
                "msg_type": "master_cmd",
                "target_slave_id": target_sid,
                "register": reg_addr,
                "value_hex": val_hex,
                "value_int": val_int,
                "description": f"Master 控制從機 {target_sid}"
            }
        except Exception as e:
            logger.error(f"Modbus 0x10 解析失敗: {e}")
            return {}

    if p_type not in BMS_MAP:
        return {}
    
    res = {}
    register_def = BMS_MAP[p_type]
    base_index = 6 
    
    for off, entry in register_def.items():
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
