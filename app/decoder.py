# =============================================================================
# decoder.py - V2.2.3 Production Final (Edge Node Hardened)
# 模組名稱：數據解碼與校驗層
# 狀態：[Reject Risk-7] 拒絕放寬 LIMITS，維持 16S 電池組 (70V) 嚴格物理邊界。
# =============================================================================

import struct
import logging
from typing import Dict, Any, Optional
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

LIMITS = [
    {"min": 0.0, "max": 5.0, "incl": "cell_", "must_end": "_voltage", "excl": None},
    {"min": 0.0, "max": 70.0, "incl": "total_voltage", "must_end": None, "excl": None},
    {"min": -2500.0, "max": 2500.0, "incl": "balance_current", "must_end": None, "excl": None},
    {"min": -350.0, "max": 350.0, "incl": "current", "must_end": None, "excl": "balance"},
    {"min": -40.0, "max": 120.0, "incl": "temp", "must_end": None, "excl": None},
    {"min": 0.0, "max": 2.0, "incl": "max_diff_voltage", "must_end": None, "excl": None},
]

def extract_device_address(packet: bytes) -> Optional[int]:
    try:
        if len(packet) >= 274:
            val_270 = struct.unpack_from("<I", packet, 270)[0]
            if 0 <= val_270 <= 15: return val_270
        if len(packet) >= 278:
            val_274 = struct.unpack_from("<I", packet, 274)[0]
            if 0 <= val_274 <= 15: return val_274
        return None
    except Exception:
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    # 🟢 [防禦] 阻擋極短封包，防止 IndexError
    if not packet or len(packet) < 8:
        return {}

    # === Modbus 0x10 (Write Multiple Registers) 處理 ===
    if p_type == 0x10 or p_type == 16:
        try:
            target_sid = packet[0]
            byte_count = packet[6]
            expected_min_len = 9 + byte_count
            
            # 🟢 [防禦] 確保 payload 沒有被截斷
            if len(packet) < expected_min_len:
                logger.debug(f"Modbus 0x10 封包截斷或異常: 預期>={expected_min_len}, 實際={len(packet)}")
                return {}

            payload_bytes = packet[7 : 7 + byte_count]
            # 無損相容：只寫入1個暫存器(2 bytes)時才解譯 value_int
            val_int = struct.unpack(">H", payload_bytes)[0] if byte_count == 2 else None

            return {
                "msg_type": "master_cmd",
                "target_slave_id": target_sid,
                "register": f"0x{packet[2:4].hex().upper()}",
                "byte_count": byte_count,
                "value_hex": f"0x{payload_bytes.hex().upper()}",
                "value_int": val_int,
                "description": f"Master 控制從機 {target_sid}"
            }
        except Exception:
            logger.exception("Modbus 0x10 指令解析失敗")
            return {}

    # === JK BMS 專有協議處理 (維持原有邏輯與 LIMITS 攔截) ===
    if p_type not in BMS_MAP:
        return {}

    res = {}
    register_def = BMS_MAP[p_type]
    base_index = 6

    for off, entry in register_def.items():
        dtype = entry[2]
        conv = entry[3] if len(entry) > 3 else None
        key_en = entry[6] if (len(entry) > 6 and entry[6]) else f"reg_{p_type}_{off}"

        abs_off = base_index + off
        
        # 🟢 [防禦] 確保 unpack 不越界
        if abs_off + struct.calcsize(f"<{dtype}") <= len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                val = conv(raw) if conv else raw

                is_valid = True
                try:
                    val_float = float(val)
                    for rule in LIMITS:
                        if rule["incl"] in key_en:
                            if rule["excl"] and rule["excl"] in key_en:
                                continue
                            if rule["must_end"] and not key_en.endswith(rule["must_end"]):
                                continue

                            if not (rule["min"] <= val_float <= rule["max"]):
                                logger.warning(
                                    f"⚠️ 攔截異常數據(位元翻轉): {key_en} = {val} "
                                    f"(合法範圍: {rule['min']}~{rule['max']})"
                                )
                                is_valid = False
                                break
                except (ValueError, TypeError):
                    pass

                if is_valid:
                    res[key_en] = val

            except struct.error:
                continue
    return res
