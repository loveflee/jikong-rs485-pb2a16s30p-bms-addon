#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import logging
import sys

from transport import create_transport, BaseTransport
from publisher import get_publisher
from decoder import decode_packet, extract_device_address


def setup_logging(debug_raw: bool) -> None:
    """
    設定 logging 格式與等級。

    debug_raw = True 時，輸出 DEBUG（可以在 decoder / transport 那邊多印 raw hex）
    否則只顯示 INFO 以上。
    """
    level = logging.DEBUG if debug_raw else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


logger = logging.getLogger("jk_bms_main")


def main():
    # 建立通訊層（TCP or RS485）
    transport: BaseTransport = create_transport()
    # 建立 MQTT 發佈器
    publisher = get_publisher(config_path="/data/config.yaml")

    # 從 config 裡拿到 expire_time（放在 transport.app_cfg）
    PACKET_EXPIRE_TIME = float(transport.app_cfg.get("packet_expire_time", 0.4))
    debug_raw_log = bool(transport.app_cfg.get("debug_raw_log", False))

    # 啟動 logging
    setup_logging(debug_raw_log)

    # 這裡還是用原本的 "0x02 綁定邏輯"
    pending_realtime_packet = None
    last_realtime_time = 0.0

    logger.info("🚀 主程式啟動，開始從 transport 收封包...")

    # 持續從 transport 收 (packet_type, raw_bytes)
    for pkt_type, packet in transport.packets():
        try:
            if pkt_type == 0x02:
                # 先暫存，等 0x01 來補 ID
                if pending_realtime_packet is not None:
                    logger.warning("⚠️ 上一筆 0x02 尚未等到 0x01 ID，就已被新數據覆蓋")
                pending_realtime_packet = packet[:]
                last_realtime_time = time.time()
                logger.info("📡 收到 0x02 即時數據，已暫存等待 0x01 (設定) 來關聯")

            elif pkt_type == 0x01:
                # 解析設備 ID
                current_id = extract_device_address(packet)
                logger.info("🔑 收到 0x01 設定封包，解析出 ID: %s", hex(current_id))

                # 先把 0x01 封包解碼成 dict
                settings_payload = decode_packet(packet, 0x01)
                # 發佈 settings
                publisher.publish_payload(current_id, 0x01, settings_payload)
                logger.debug("📤 已發佈 0x01 設定資料到 MQTT (ID=%s)", hex(current_id))

                # 處理之前暫存的 0x02
                if pending_realtime_packet:
                    time_diff = time.time() - last_realtime_time
                    if time_diff < PACKET_EXPIRE_TIME:
                        logger.info(
                            "✅ [關聯成功] 使用 ID %s 發布暫存 0x02 即時數據 (延遲 %.2fs)",
                            hex(current_id),
                            time_diff,
                        )
                        realtime_payload = decode_packet(pending_realtime_packet, 0x02)
                        publisher.publish_payload(current_id, 0x02, realtime_payload)
                        logger.debug("📤 已發佈 0x02 即時資料到 MQTT (ID=%s)", hex(current_id))
                    else:
                        logger.warning(
                            "🗑️ 暫存 0x02 已超過 %.2fs，丟棄 (實際延遲 %.2fs)",
                            PACKET_EXPIRE_TIME,
                            time_diff,
                        )
                    pending_realtime_packet = None
                else:
                    logger.info("ℹ️ 收到 0x01，但目前沒有暫存的 0x02 即時數據")

            else:
                # 未知封包型別先略過
                logger.debug("ℹ️ 收到未知封包型別: %s，略過", hex(pkt_type))

        except Exception as e:
            logger.error("❌ main 處理封包發生錯誤: %s", e, exc_info=debug_raw_log)


if __name__ == "__main__":
    main()
