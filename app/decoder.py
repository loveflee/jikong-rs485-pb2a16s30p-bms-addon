import struct
import logging
from typing import Dict, Any, Optional
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

def extract_device_address(packet: bytes) -> Optional[int]:
    try:
        # 策略 1: 優先檢查 270 (與 BMS_MAP 對齊)
        if len(packet) >= 274:
            val_270 = struct.unpack_from("<I", packet, 270)[0]
            # 🟢 [優化] 防禦 RS485 雜訊：限制 ID 在 0~15 的合理範圍
            if 0 <= val_270 <= 15:
                return val_270

        # 策略 2: 相容性檢查
        if len(packet) >= 278:
            val_274 = struct.unpack_from("<I", packet, 274)[0]
            if 0 <= val_274 <= 15:
                return val_274

        return None
    except Exception as e:
        logger.debug(f"提取設備地址失敗: {e}")
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    # 處理 Modbus 指令 (0x10)
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
        dtype = entry[2]
        conv = entry[3] if len(entry) > 3 else None

        # 🟢 [優化] 防禦字典空字串：如果 entry[6] 存在且不為空字串，否則用預設值
        key_en = entry[6] if (len(entry) > 6 and entry[6]) else f"reg_{p_type}_{off}"

        abs_off = base_index + off
        if abs_off + struct.calcsize(f"<{dtype}") <= len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                res[key_en] = conv(raw) if conv else raw
            except Exception:
                continue

    return res
