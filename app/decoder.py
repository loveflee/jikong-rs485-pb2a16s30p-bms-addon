# decoder.py

import struct
import logging
from typing import Dict, Any, Optional
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet: bytes) -> Optional[int]:
    """
    從 JK BMS 的 0x01 (Settings) 封包中提取硬體位址。
    這是判定數據歸屬最權威的依據，Master 固定為 0。
    """
    try:
        # JK BMS 協議定義地址位於 Payload 的第 270 字節
        # 封包結構: [55 AA EB 90] (4 bytes) + [Len] (2 bytes) = 6 bytes Header
        # 絕對偏移 = 6 + 270 = 276
        if len(packet) >= 280:  # 確保長度足夠讀取 4 bytes ID
            return struct.unpack_from("<I", packet, 276)[0]
        return None
    except Exception as e:
        logger.debug(f"提取設備地址失敗: {e}")
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    """
    多協議解碼器 (v2.0.2):
    1. 處理 Modbus 0x10 (Master 控制指令): 用於指令引導與應答確認
    2. 處理 JK BMS 0x01/0x02 (廣播數據): 即時數據與參數設定
    """
    
    # ✅ 處理 Modbus 指令 (Master 對 Slave 的控制動作)
    if p_type == 0x10 or p_type == 16:
        try:
            # 標準 Modbus RTU 寫入指令 (11 bytes)
            # [ID] [10] [RegH] [RegL] [QtyH] [QtyL] [Len] [ValH] [ValL] [CRCL] [CRCH]
            target_sid = packet[0]
            reg_addr = f"0x{packet[2:4].hex().upper()}"
            val_hex = f"0x{packet[7:9].hex().upper()}"
            val_int = struct.unpack(">H", packet[7:9])[0]
            
            return {
                "msg_type": "master_cmd",
                "target_slave_id": target_sid, # 與 main.py 邏輯對齊
                "register": reg_addr,
                "value_hex": val_hex,
                "value_int": val_int,
                "description": f"Master 控制從機 {target_sid}"
            }
        except Exception as e:
            logger.error(f"Modbus 0x10 解析失敗: {e}")
            return {}

    # ✅ 處理 JK BMS 廣播數據 (0x01/0x02)
    if p_type not in BMS_MAP:
        return {}
    
    res = {}
    register_def = BMS_MAP[p_type]
    
    # 標頭定義: 55 AA EB 90 (4 bytes) + 長度 (2 bytes) = 6 bytes
    # Payload 起始於索引 6
    base_index = 6 
    
    for off, entry in register_def.items():
        # entry 結構: (名稱, 單位, 類型, 轉換函數, HA類型, 圖標, 英文Key)
        name = entry[0]
        dtype = entry[2]
        conv = entry[3] if len(entry) > 3 else None
        
        abs_off = base_index + off
        # 檢查封包長度是否足以解析該欄位
        if abs_off + struct.calcsize(f"<{dtype}") <= len(packet):
            try:
                # 使用小端格式讀取
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                res[name] = conv(raw) if conv else raw
            except Exception:
                continue
                
    return res
