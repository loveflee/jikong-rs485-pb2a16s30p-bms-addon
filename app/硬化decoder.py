# =============================================================================
# decoder.py - V2.2.1 Production Final (Industrial Hardened)
# 模組名稱：數據解碼與校驗層
# 修正亮點：
#   - [Fix] 位元翻轉過濾：強化 LIMITS 判定邏輯，精準攔截 RS485 干擾產生的突跳值。
#   - [Fix] 安全解碼：導入更嚴謹的 struct 異常處理，防止封包長度不足導致的崩潰。
#   - [Opt] 效能硬化：優化 LIMITS 規則循環，對非目標欄位快速放行。
#   - [Opt] 日誌追蹤：logger.warning 訊息包含合法範圍提示，方便現場調試。
# =============================================================================

import struct
import logging
from typing import Dict, Any, Optional
from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

# 🟢 [底層邏輯優化] 針對物理特性與 Grafana 視覺一致性設計的限制清單
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
    
    # 6. 最大壓差: 物理邊界 2V。防止雜訊使 Grafana 座標軸失真。
    {"min": 0.0, "max": 2.0, "incl": "max_diff_voltage", "must_end": None, "excl": None},
]

def extract_device_address(packet: bytes) -> Optional[int]:
    """從封包特定偏移量提取設備硬體地址 (Slave ID)"""
    try:
        # 策略 1: 檢查偏移 270 (新版韌體標準)
        if len(packet) >= 274:
            val_270 = struct.unpack_from("<I", packet, 270)[0]
            if 0 <= val_270 <= 15: return val_270
        # 策略 2: 檢查偏移 274 (相容模式)
        if len(packet) >= 278:
            val_274 = struct.unpack_from("<I", packet, 274)[0]
            if 0 <= val_274 <= 15: return val_274
        return None
    except Exception:
        return None

def decode_packet(packet: bytes, p_type: int) -> Dict[str, Any]:
    """
    將原始二進制封包解碼為物理數值字典，並執行嚴格的邊界檢查。
    """
    # 處理 Master 發出的 Modbus 控制指令 (0x10)
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
        except Exception:
            logger.exception("Modbus 0x10 指令解析失敗")
            return {}

    # 若封包類型不在地圖定義中，直接丟棄
    if p_type not in BMS_MAP:
        return {}

    res = {}
    register_def = BMS_MAP[p_type]
    base_index = 6  # JK 協定數據段從第 6 byte 開始

    for off, entry in register_def.items():
        dtype = entry[2]
        conv = entry[3] if len(entry) > 3 else None
        # 提取 Key 名稱，優先使用地圖定義，否則自動生成
        key_en = entry[6] if (len(entry) > 6 and entry[6]) else f"reg_{p_type}_{off}"
        
        abs_off = base_index + off
        # 檢查封包剩餘長度是否足夠讀取該型別
        if abs_off + struct.calcsize(f"<{dtype}") <= len(packet):
            try:
                raw = struct.unpack_from(f"<{dtype}", packet, abs_off)[0]
                val = conv(raw) if conv else raw
                
                # 🟢 [核心過濾邏輯]
                is_valid = True
                try:
                    val_float = float(val)
                    for rule in LIMITS:
                        # 檢查包含條件
                        if rule["incl"] in key_en:
                            # 檢查排除條件
                            if rule["excl"] and rule["excl"] in key_en:
                                continue
                            # 檢查後綴要求 (如必須是 _voltage)
                            if rule["must_end"] and not key_en.endswith(rule["must_end"]):
                                continue
                            
                            # 數值邊界判定
                            if not (rule["min"] <= val_float <= rule["max"]):
                                logger.warning(
                                    f"⚠️ 攔截異常數據(位元翻轉): {key_en} = {val} "
                                    f"(合法範圍: {rule['min']}~{rule['max']})"
                                )
                                is_valid = False
                            break # 命中一條規則後即跳出規則循環
                except (ValueError, TypeError):
                    # 若無法轉為浮點數進行比較，則視為不需過濾的數據（如字串）
                    pass

                if is_valid:
                    res[key_en] = val
                    
            except struct.error:
                continue
    return res
