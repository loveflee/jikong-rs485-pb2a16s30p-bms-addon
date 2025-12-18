# main.py

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

PACKET_QUEUE = queue.Queue(maxsize=500)
OPTIONS_PATH = "/data/options.json"
CONFIG_PATH = "/data/config.yaml"

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

def process_packets_worker(app_config):
    publisher = get_publisher(CONFIG_PATH)
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    
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
                    if hw_id is None: continue

                    target_publish_id = None

                    # --- 關鍵修正：確保 BMS 0 歸位 ---
                    if hw_id == 0:
                        target_publish_id = 0
                    else:
                        # 如果不是 0，再啟用時間差保險機制
                        # 防止 Master 數據被誤判為 Slave
                        if (timestamp - last_poll_timestamp) > 1.5: 
                            # 時間過久，推定為 Master 自發廣播
                            target_publish_id = 0
                        else:
                            # 正常回應點名
                            target_publish_id = last_polled_slave_id

                    if target_publish_id is not None:
                        # (A) 發布指令
                        if target_publish_id in pending_cmds:
                            publisher.publish_payload(0, 0x10, pending_cmds.pop(target_publish_id))
                        
                        # (B) 發布 0x01
                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(target_publish_id, 0x01, settings_map)
                        
                        # (C) 發布 0x02
                        if "last" in pending_realtime_data:
                            rt_time, rt_data = pending_realtime_data.pop("last")
                            if (timestamp - rt_time) <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(target_publish_id, 0x02, realtime_map)

                    if (timestamp - last_poll_timestamp) > 5.0:
                        pending_cmds.clear()

            except Exception as e:
                logger.error(f"解析錯誤: {e}")
            finally:
                PACKET_QUEUE.task_done()
        except Exception as e:
            logger.error(f"Worker 循環錯誤: {e}")
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
    logger.info("🚀 JiKong BMS 指令導引監控系統 v2.0.6")
    logger.info("✅ 最終修正: 地址偏移量校準為 270 (BMS 0 回歸)")
    logger.info(f"📡 介面: {'USB 直連' if app_cfg.get('use_rs485_usb') else 'TCP 網關'}")
    logger.info("==========================================")
    
    _ = get_publisher(CONFIG_PATH)
    
    worker = threading.Thread(target=process_packets_worker, args=(app_cfg,), daemon=True)
    worker.start()

    transport_inst = create_transport()
    try:
        for pkt_type, pkt_data in transport_inst.packets():
            if not PACKET_QUEUE.full():
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
            else:
                logger.warning("⚠️ 隊列已滿，請檢查系統效能")
    except KeyboardInterrupt:
        logger.info("🛑 系統停止")
    except Exception as e:
        logger.error(f"💥 傳輸層崩倉: {e}")

if __name__ == "__main__":
    main()
