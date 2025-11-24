# decoder.py
import struct
import logging
from typing import Dict, Any

from bms_registers import BMS_MAP

logger = logging.getLogger("jk_bms_decoder")

HEADER_LEN = 6  # 0x55 0xAA 0xEB 0x90 + 2 bytes


def extract_device_address(packet_0x01: bytes) -> int:
    """
    從 0x01 (Settings) 封包中提取 Device Address。

    bms_registers 定義 offset 264（相對 payload），
    因此實際索引 = header(6) + 264 = 270。

    平常不會大量輸出 log，
    只有在 debug_raw_log=True（logging 等級 DEBUG）時，才會看到詳細解析。
    """
    try:
        pkt_len = len(packet_0x01)
        logger.debug("📦 0x01 length = %d", pkt_len)

        if pkt_len >= 274:  # 270 + 4 bytes
            raw = packet_0x01[270:274]
            logger.debug("🔍 raw addr bytes @270-273 = %s", raw.hex(" "))
            device_id = struct.unpack_from("<I", packet_0x01, 270)[0]
            logger.debug("🔑 解析得到 device_id = %d (hex 0x%x)", device_id, device_id)
            return device_id
        else:
            logger.debug("⚠️ 0x01 封包長度不足 274，無法取得設備地址")
        return 0
    except Exception as e:
        # 解析失敗時，用 WARNING/ERROR 讓你看得到
        logger.warning("❌ 提取設備地址失敗: %s", e)
        return 0


def get_value(data: bytes, offset: int, dtype: str):
    """小工具：依 dtype 從 data 中讀取數值。"""
    try:
        if dtype == "B":
            # 單一 byte
            return data[offset]
        if "s" in dtype:
            # 字串類型 (e.g. "16s")
            return struct.unpack_from(f"<{dtype}", data, offset)[0]
        # 其他數值型別 (H, I, f, ...)，全部丟給 struct.unpack_from
        return struct.unpack_from(f"<{dtype}", data, offset)[0]
    except Exception:
        # 若有任何問題，回傳 None，呼叫方會自行略過
        return None


def decode_packet(packet: bytes, packet_type: int) -> Dict[str, Any]:
    """
    將原始封包解析為 payload dict。

    - 不管 MQTT、不管 discovery，只單純把 BMS_MAP 定義的欄位轉成 dict。
    - 「暫存 0x02」「綁定 ID」的邏輯在 main.py 完成。
    - 這裡只做純解析，錯誤與異常一律安靜處理或用 DEBUG/WARNING 記錄。
    """
    if packet_type not in BMS_MAP:
        logger.debug("⚠️ 未知的封包類型: %s", hex(packet_type))
        return {}

    register_def = BMS_MAP[packet_type]
    base_index = HEADER_LEN  # Header 長度為 6
    payload: Dict[str, Any] = {}

    for offset in sorted(register_def.keys()):
        entry = register_def[offset]
        name = entry[0]
        dtype = entry[2]
        converter = entry[3]

        abs_offset = base_index + offset
        if abs_offset >= len(packet):
            # 封包不足以讀取這個 offset → 略過
            logger.debug(
                "略過欄位 %s：abs_offset=%d 超出封包長度 %d",
                name,
                abs_offset,
                len(packet),
            )
            continue

        raw_val = get_value(packet, abs_offset, dtype)
        if raw_val is not None:
            try:
                final_val = converter(raw_val)
            except Exception:
                final_val = raw_val
            payload[name] = final_val
        else:
            logger.debug("欄位 %s 解析失敗 (offset=%d, dtype=%s)", name, abs_offset, dtype)

    # 0x02 額外解析充放電開關 (虛擬欄位)
    if packet_type == 0x02:
        # 放电状态 → 放电开关
        discharge_val = payload.get("放电状态")
        if isinstance(discharge_val, str) and discharge_val.startswith("0x"):
            try:
                raw = int(discharge_val, 16)
                payload["放电开关"] = (raw & 0x1) == 1
            except Exception:
                logger.debug("解析放电开关失敗: %s", discharge_val)

        # 充电状态 → 充电开关
        charge_val = payload.get("充电状态")
        if isinstance(charge_val, str) and charge_val.startswith("0x"):
            try:
                raw = int(charge_val, 16)
                payload["充电开关"] = (raw & 0x1) == 1
            except Exception:
                logger.debug("解析充电开关失敗: %s", charge_val)

    return payload
