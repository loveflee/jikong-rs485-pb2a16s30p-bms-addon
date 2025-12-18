import time
import os
import sys
import queue
import threading
import logging
import yaml
import json

from transport import create_transport 
from decoder import decode_packet, extract_device_address
from publisher import get_publisher

PACKET_QUEUE = queue.Queue(maxsize=500)
OPTIONS_PATH = "/data/options.json"  # HA Add-on 標準設定路徑
CONFIG_PATH = "/data/config.yaml"   # 程式內部映射路徑

def load_ui_config():
    """解析 HA UI 設定並轉換為程式內部需要的 app_cfg 格式"""
    if not os.path.exists(OPTIONS_PATH):
        logging.error("❌ 找不到 HA options.json")
        sys.exit(1)
        
    with open(OPTIONS_PATH, 'r', encoding='utf-8') as f:
        options = json.load(f)

    # 1. 識別連線模式
    ui_mode = options.get("connection_mode", "RS485 USB Dongle")
    
    # 2. 建立標準化的內部配置結構 (適配原本的 transport 邏輯)
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
        # MQTT 部分透傳給 publisher 使用
        "mqtt": {
            "host": options.get("mqtt_host"),
            "port": options.get("mqtt_port"),
            "username": options.get("mqtt_username"),
            "password": options.get("mqtt_password"),
            "discovery_prefix": options.get("mqtt_discovery_prefix"),
            "topic_prefix": options.get("mqtt_topic_prefix"),
            "client_id": options.get("mqtt_client_id")
        }
    }
    
    # 將設定同步寫入 config.yaml 供其他模組(如 publisher)讀取
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f)
        
    return config

def process_packets_worker(app_config):
    """消費者執行緒：處理數據與指令"""
    publisher = get_publisher(CONFIG_PATH)
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    pending_realtime_packets = {}

    while True:
        try:
            packet_item = PACKET_QUEUE.get()
            timestamp, packet_type, packet_data = packet_item
            
            try:
                # 1. 處理 Master 指令 (0x10)
                if packet_type == 0x10:
                    cmd_map = decode_packet(packet_data, 0x10)
                    if cmd_map:
                        publisher.publish_payload(cmd_map.get("slave_id", 0), 0x10, cmd_map)
                    continue 

                # 2. 處理 JK BMS 廣播數據 (0x01/0x02)
                if packet_type == 0x02:
                    pending_realtime_packets["last"] = (timestamp, packet_data)
                elif packet_type == 0x01:
                    device_id = extract_device_address(packet_data)
                    # 確保包含 ID 0 (Master BMS 本身)
                    if device_id is not None:
                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(device_id, 0x01, settings_map)
                        
                        if "last" in pending_realtime_packets:
                            rt_time, rt_data = pending_realtime_packets.pop("last")
                            if 0 <= (timestamp - rt_time) <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(device_id, 0x02, realtime_map)
            except Exception: pass
            finally: PACKET_QUEUE.task_done()
        except Exception: time.sleep(1)

def main():
    # 🚀 載入優化後的 UI 設定
    full_cfg = load_ui_config()
    app_cfg = full_cfg.get('app', {})
    
    # 設定日誌等級
    logging.basicConfig(
        level=logging.DEBUG if bool(app_cfg.get("debug_raw_log", False)) else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    logger = logging.getLogger(__name__)
    mode_str = "USB 模式 (全功能監聽)" if app_cfg.get("use_rs485_usb") else "TCP 模式"
    logger.info(f"🚀 JiKong BMS 系統已啟動 | 模式: {mode_str}")
    
    # 啟動 MQTT 發布器
    _ = get_publisher(CONFIG_PATH)
    
    # 啟動背景處理執行緒
    worker = threading.Thread(target=process_packets_worker, args=(app_cfg,), daemon=True)
    worker.start()

    # 啟動傳輸層 (Producer)
    transport_inst = create_transport()
    try:
        for pkt_type, pkt_data in transport_inst.packets():
            if not PACKET_QUEUE.full():
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
    except KeyboardInterrupt:
        logger.info("🛑 系統停止")
    except Exception as e:
        logger.error(f"💥 傳輸層崩潰: {e}")

if __name__ == "__main__":
    main()
