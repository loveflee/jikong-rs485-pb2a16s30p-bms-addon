# main.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import logging
import sys

# 確保在 import 其他模組前設定好基本 logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

from transport import create_transport, BaseTransport
from publisher import get_publisher
from decoder import decode_packet, extract_device_address

logger = logging.getLogger("jk_bms_main")

def update_log_level(debug_raw: bool) -> None:
    """
    根據 config 更新 root logger 等級。
    """
    level = logging.DEBUG if debug_raw else logging.INFO
    logging.getLogger().setLevel(level)
    logger.info(f"📝 Logging level set to: {'DEBUG' if debug_raw else 'INFO'}")

def main():
    logger.info("🚀 JiKong BMS Monitor 啟動中...")

    # 1. 建立通訊層 (TCP or RS485)
    # 這一步只是建立物件，真正連線是在 transport.packets() 迴圈內
    transport: BaseTransport = create_transport()
    
    # 讀取 Config 用來設定 Log
    debug_raw_log = bool(transport.app_cfg.get("debug_raw_log", False))
    update_log_level(debug_raw_log)
    PACKET_EXPIRE_TIME = float(transport.app_cfg.get("packet_expire_time", 0.4))

    # 2. 建立 MQTT 發佈器
    # 注意：新的 publisher __init__ 包含重試迴圈，若 MQTT Broker 沒開會在這裡等待直到連線成功
    try:
        publisher = get_publisher(config_path="/data/config.yaml")
    except Exception as e:
        logger.critical(f"❌ 無法初始化 MQTT Publisher，程式即將結束: {e}")
        sys.exit(1)

    # 3. 變數初始化
    pending_realtime_packet = None
    last_realtime_time = 0.0

    logger.info("📡 開始監聽 Transport 數據流...")

    # 4. 主迴圈：持續從 transport 收 (packet_type, raw_bytes)
    # 若 transport 斷線，generator 內部會自動重試，不會讓這個 for loop 結束
    try:
        for pkt_type, packet in transport.packets():
            try:
                if pkt_type == 0x02:
                    # 收到即時數據，暫存等待 0x01 來綁定 ID
                    if pending_realtime_packet is not None:
                        logger.warning("⚠️ 上一筆 0x02 尚未等到 0x01 ID，已被新數據覆蓋")
                    
                    pending_realtime_packet = packet[:]
                    last_realtime_time = time.time()
                    logger.debug("📥 收到 0x02 即時數據 (Length: %d)，暫存中...", len(packet))

                elif pkt_type == 0x01:
                    # 收到設定數據，這是所有邏輯的核心 (因為只有它帶有 Device ID)
                    current_id = extract_device_address(packet)
                    if current_id == 0:
                        logger.warning("⚠️ 收到 0x01 但無法解析 Device ID，跳過處理")
                        continue

                    logger.debug(f"🔑 收到 0x01，解析出 ID: {hex(current_id)}")

                    # A. 發佈 Settings
                    settings_payload = decode_packet(packet, 0x01)
                    publisher.publish_payload(current_id, 0x01, settings_payload)

                    # B. 檢查是否有對應的 0x02 暫存數據
                    if pending_realtime_packet:
                        time_diff = time.time() - last_realtime_time
                        
                        if time_diff < PACKET_EXPIRE_TIME:
                            logger.info(
                                f"✅ [配對成功] ID:{hex(current_id)} | 0x02 延遲:{time_diff:.3f}s"
                            )
                            realtime_payload = decode_packet(pending_realtime_packet, 0x02)
                            publisher.publish_payload(current_id, 0x02, realtime_payload)
                        else:
                            logger.warning(
                                f"🗑️ [配對過期] ID:{hex(current_id)} | 0x02 延遲:{time_diff:.3f}s > {PACKET_EXPIRE_TIME}s"
                            )
                        
                        # 清空暫存，避免重複使用
                        pending_realtime_packet = None
                    else:
                        logger.debug("ℹ️ 收到 0x01，但目前無暫存的 0x02")

                else:
                    logger.debug(f"ℹ️ 收到其他封包型別: {hex(pkt_type)}，略過")

            except Exception as inner_e:
                logger.error(f"❌ 封包處理邏輯錯誤: {inner_e}", exc_info=True)

    except KeyboardInterrupt:
        logger.info("👋 使用者中斷，程式結束")
    except Exception as e:
        logger.critical(f"❌ 主程式發生致命錯誤: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
