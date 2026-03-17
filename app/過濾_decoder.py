import struct
import logging
from typing import Dict, Any, Optional
from bms_registers import BMS_MAP
logger = logging.getLogger("jk_bms_decoder")
# [底層邏輯優化] 針對物理特性與 Grafana 視覺一致性設計的限制清單
LIMITS = [
    # 1. 單體電壓: 必須包含 cell_ 且必須以 _voltage 結尾，防止誤傷 index/count
    {"min": 0.0, "max": 5.0, "incl": "cell_", "must_end": "_voltage", "excl": None},
    # 2. 總電壓: 物理邊界 0-70V
    {"min": 0.0, "max": 70.0, "incl": "total_voltage", "must_end": None, "excl": None},
    # 3. 均衡電流: 單位 mA，攔截超過 2.5A 的異常跳變
    {"min": -2500.0, "max": 2500.0, "incl": "balance_current", "must_end": None, "excl": None},
    # 4. 電池主電流: 單位 A，攔截超過 350A 的雜訊，並排除均衡電流(mA)以免混淆
    {"min": -350.0, "max": 350.0, "incl": "current", "must_end": None, "excl": "balance"},
    # 5. 溫度: 物理邊界 -40~120°C
    {"min": -40.0, "max": 120.0, "incl": "temp", "must_end": None, "excl": None},
    # 6. [新增] 最大壓差: 物理邊界 2V。超過 2V 的雜訊會讓 Grafana 圖表縮成一條直線。
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
    # 處理 Modbus 指令 (0x10)
    if p_type == 0x10 or p_type == 16:
        try:
            target_sid = packet[0]
            return {
                "msg_type": "master_cmd",
                "target_slave_id": target_sid,
                "register": f"0x{packet[2:4].hex().upper()}",
                "value_hex": f"0x{packet[7:9].hex().upper()}",
                "value_int": struct.unpack(">H", packet[7:9])[0],
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
        # 使用地圖檔中的 Key，若無則預設
        key_en = entry[6] if (len(entry) > 6 and entry[6]) else f"reg_{p_type}_{off}"
        
        abs_off = base_index + off
        if abs_off + struct.calcsize(f"<{dtype}") <= len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                val = conv(raw) if conv else raw
                
                # 🟢 [數據校驗核心] 結構化判定邏輯
                is_valid = True
                val_float = float(val)
                
                for rule in LIMITS:
                    # A. 檢查包含條件 (如 cell_, total_voltage 等)
                    if rule["incl"] in key_en:
                        # B. 檢查排除條件 (防止 balance_current 被誤認為 current)
                        if rule["excl"] and rule["excl"] in key_en:
                            continue
                        
                        # C. 檢查後綴條件 (解決 cell_index / cell_count 誤觸電壓過濾)
                        if rule["must_end"] and not key_en.endswith(rule["must_end"]):
                            continue
                        
                        # D. 執行數值邊界校驗
                        if not (rule["min"] <= val_float <= rule["max"]):
                            logger.warning(
                                f"⚠️ 攔截異常數據: {key_en} = {val} "
                                f"(合法範圍: {rule['min']}~{rule['max']})"
                            )
                            is_valid = False
                        
                        # 命中規則後即結束該欄位的規則比對
                        break

                if is_valid:
                    res[key_en] = val
                    
            except (ValueError, TypeError, struct.error):
                continue
    return res
