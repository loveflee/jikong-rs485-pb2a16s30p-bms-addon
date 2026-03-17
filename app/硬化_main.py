import time
import os
import sys
import queue
import threading
import logging
import yaml
import json
import struct

from transport import create_transport
from decoder import decode_packet, extract_device_address
from publisher import get_publisher

# 500 * 308 bytes ≈ 154 KB, 限制隊列長度以保護內存
PACKET_QUEUE = queue.Queue(maxsize=500)
OPTIONS_PATH = "/data/options.json"
CONFIG_PATH = "/data/config.yaml"

# 單機心跳監控全域變數
DEVICE_STATUS_MAP = {}  
DEVICE_TIMEOUT = 60.0   
DEVICE_LOCK = threading.Lock() 

def load_ui_config():
    if not os.path.exists(OPTIONS_PATH):
        logging.error("❌ 找不到 HA options.json")
        sys.exit(1)

    with open(OPTIONS_PATH, 'r', encoding='utf-8') as f:
        options = json.load(f)

    ui_mode = options.get("connection_mode", "RS485 USB Dongle")

    config = {
        "app": {
            "use_modbus_gateway": ui_mode == "Modbus Gateway TCP",
            "use_rs485_usb": ui_mode == "RS485 USB Dongle",
            "debug_raw_log": options.get("debug_raw_log", False),
            "packet_expire_time": options.get("packet_expire_time", 2.0),
            "settings_publish_interval": options.get("settings_publish_interval", 60)
        },
        "tcp": {
            "host": options.get("modbus_host"),
            "port": options.get("modbus_port", 502),
            "timeout": options.get("modbus_timeout", 10),
            "buffer_size": options.get("modbus_buffer_size", 4096)
        },
        "serial": {
            "device": options.get("serial_device"),
            "baudrate": options.get("serial_baudrate", 115200),
            "timeout": 1.0
        },
        "mqtt": {
            "host": options.get("mqtt_host"),
            "port": options.get("mqtt_port", 1883),
            "username": options.get("mqtt_username"),
            "password": options.get("mqtt_password"),
            "discovery_prefix": options.get("mqtt_discovery_prefix", "homeassistant"),
            "topic_prefix": options.get("mqtt_topic_prefix", "Jikong_BMS"),
            "client_id": options.get("mqtt_client_id", "jk_bms_monitor")
        }
    }

    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f)
    return config

def device_watchdog_worker():
    """獨立看門狗：監控設備在線狀態"""
    logger = logging.getLogger("watchdog")
    publisher = get_publisher(CONFIG_PATH)

    while True:
        try:
            now = time.time()
            with DEVICE_LOCK:
                devices_snapshot = list(DEVICE_STATUS_MAP.items())

            for dev_id, info in devices_snapshot:
                if info["state"] == "online" and (now - info["last_seen"]) > DEVICE_TIMEOUT:
                    with DEVICE_LOCK:
                        DEVICE_STATUS_MAP[dev_id]["state"] = "offline"
                    publisher.publish_device_status(dev_id, "offline")
                    logger.warning(f"⚠️ 設備掉線: BMS {dev_id} 已超過 {DEVICE_TIMEOUT} 秒未回應")
            time.sleep(5)
        except Exception:
            logger.exception("Watchdog 循環發生未知異常")
            time.sleep(10)

def process_packets_worker(app_config):
    """資料處理核心：解析封包並分配設備歸屬"""
    publisher = get_publisher(CONFIG_PATH)
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    is_debug = bool(app_config.get("debug_raw_log", False))

    last_polled_slave_id = None
    last_poll_timestamp = 0
    pending_cmds = {}
    pending_realtime_data = {}

    logger = logging.getLogger("worker")

    while True:
        try:
            packet_item = PACKET_QUEUE.get()
            timestamp, packet_type, packet_data = packet_item

            try:
                # 1. 監聽到 Master 指令 (0x10)
                if packet_type == 0x10:
                    cmd_map = decode_packet(packet_data, 0x10)
                    if cmd_map:
                        target_id = cmd_map.get("target_slave_id")
                        if is_debug:
                            logger.debug(f" [詢問] Master 正在呼叫從機 ID: {target_id}")

                        last_polled_slave_id = target_id
                        last_poll_timestamp = timestamp
                        pending_cmds[target_id] = cmd_map
                    continue

                # 2. 暫存 0x02
                if packet_type == 0x02:
                    pending_realtime_data["last"] = (timestamp, packet_data)
                    continue

                # 3. 處理回應 (0x01)
                if packet_type == 0x01:
                    hw_id = extract_device_address(packet_data)
                    if hw_id is None:
                        if is_debug: logger.debug("[忽略] 封包解析硬體 ID 失敗")
                        continue

                    target_publish_id = None
                    reason_msg = "" 

                    if hw_id == 0:
                        target_publish_id = 0
                        reason_msg = "硬體 ID 為 0 -> 判定為 Master"
                    else:
                        time_diff = timestamp - last_poll_timestamp
                        if time_diff > 1.5:
                            target_publish_id = 0
                            reason_msg = f"回應超時 ({time_diff:.1f}s) -> 推定為 Master 廣播"
                        else:
                            target_publish_id = last_polled_slave_id
                            reason_msg = f"回應即時 -> 歸屬點名 ID: {last_polled_slave_id}"

                    if is_debug:
                        logger.debug(f" [回答] 硬體ID: {hw_id} | 判定歸屬: {target_publish_id} | 理由: {reason_msg}")

                    if target_publish_id is not None:
                        now = time.time()
                        with DEVICE_LOCK:
                            dev_info = DEVICE_STATUS_MAP.setdefault(target_publish_id, {"last_seen": 0, "state": "offline"})
                            dev_info["last_seen"] = now
                            current_state = dev_info["state"]
                            if current_state == "offline":
                                dev_info["state"] = "online"

                        if current_state == "offline":
                            publisher.publish_device_status(target_publish_id, "online")

                        if target_publish_id in pending_cmds:
                            publisher.publish_payload(0, 0x10, pending_cmds.pop(target_publish_id))

                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(target_publish_id, 0x01, settings_map)

                        if "last" in pending_realtime_data:
                            rt_time, rt_data = pending_realtime_data.pop("last")
                            if (timestamp - rt_time) <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(target_publish_id, 0x02, realtime_map)
                                    if is_debug: logger.debug(f"✅ [發布] BMS {target_publish_id} 數據完成")

                    if (timestamp - last_poll_timestamp) > 5.0:
                        pending_cmds.clear()

            except Exception:
                # 🟢 [修正] 導入 logger.exception 記錄完整的堆疊資訊
                logger.exception("解析封包內容時發生異常")
            finally:
                PACKET_QUEUE.task_done()
        except Exception:
            # 🟢 [修正] 防止 Worker 執行緒崩潰，並記錄完整堆疊
            logger.exception("Worker 執行緒發生嚴重循環錯誤")
            time.sleep(1)

def main():
    full_cfg = load_ui_config()
    app_cfg = full_cfg.get('app', {})

    logging.basicConfig(
        level=logging.DEBUG if bool(app_cfg.get("debug_raw_log", False)) else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    logger = logging.getLogger("main")
    logger.info("==========================================")
    logger.info(" JiKong BMS 監控系統 v2.1.6 (生產硬化版)")
    logger.info("==========================================")

    _ = get_publisher(CONFIG_PATH)

    threading.Thread(target=process_packets_worker, args=(app_cfg,), daemon=True).start()
    threading.Thread(target=device_watchdog_worker, daemon=True).start()

    transport_inst = create_transport()
    try:
        for pkt_type, pkt_data in transport_inst.packets():
            try:
                # 🟢 [修正] 使用 Non-blocking put 防禦 Race Condition 與隊列溢出
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data), block=False)
            except queue.Full:
                logger.warning("⚠️ PACKET_QUEUE 已滿，丟棄封包（請檢查系統效能或數據頻率）")
            except Exception:
                logger.exception("將封包存入隊列時發生異常")
    except KeyboardInterrupt:
        logger.info(" 系統由使用者停止")
    except Exception:
        logger.exception("💥 傳輸層發生致命故障")

if __name__ == "__main__":
    main()
