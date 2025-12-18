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

# 全域隊列：加速生產者與消費者分離
PACKET_QUEUE = queue.Queue(maxsize=500)
OPTIONS_PATH = "/data/options.json"  # Home Assistant 標準路徑
CONFIG_PATH = "/data/config.yaml"    # 內部映射路徑

def load_ui_config():
    """解析 HA UI 設定並轉換為程式內部需要的階層式格式"""
    if not os.path.exists(OPTIONS_PATH):
        logging.error("❌ 找不到 HA options.json，請檢查 Add-on 設定")
        sys.exit(1)
        
    with open(OPTIONS_PATH, 'r', encoding='utf-8') as f:
        options = json.load(f)

    ui_mode = options.get("connection_mode", "RS485 USB Dongle")
    
    # 建立階層式配置，適配 transport 與 publisher 模組
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
    
    # 同步寫入 config.yaml 供其他單例模組讀取
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f)
        
    return config

def process_packets_worker(app_config):
    """
    指令導引型消費者：
    利用 Master 的點名紀錄輔助 Slave ID 判定，並給予 Master (ID 0) 絕對優先權。
    """
    publisher = get_publisher(CONFIG_PATH)
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    
    # 狀態追蹤器
    last_polled_slave_id = None
    last_poll_timestamp = 0
    pending_realtime_data = {} # 暫存最近一次收到的 0x02 數據包

    logger = logging.getLogger("worker")

    while True:
        try:
            packet_item = PACKET_QUEUE.get()
            timestamp, packet_type, packet_data = packet_item
            
            try:
                # 1. 識別 Master 控制指令 (0x10) -> 更新「點名簿」
                if packet_type == 0x10:
                    target_id = packet_data[0]  # Modbus ID
                    last_polled_slave_id = target_id
                    last_poll_timestamp = timestamp
                    
                    cmd_map = decode_packet(packet_data, 0x10)
                    if cmd_map:
                        # 將 Master 的行為發布到 MQTT (ID 0 為 Master 動作紀錄)
                        publisher.publish_payload(0, 0x10, cmd_map)
                    continue 

                # 2. 暫存 JK BMS 實體數據包 (0x02) -> 等待 ID 包來啟動判定
                if packet_type == 0x02:
                    pending_realtime_data["last"] = (timestamp, packet_data)
                    continue

                # 3. 處理 JK BMS ID/設定封包 (0x01) -> 觸發最終歸屬判定
                if packet_type == 0x01:
                    hw_id = extract_device_address(packet_data)
                    if hw_id is None: continue

                    # A. 發布設定/ID 資訊 (這部分 ID 是明確的)
                    settings_map = decode_packet(packet_data, 0x01)
                    if settings_map:
                        publisher.publish_payload(hw_id, 0x01, settings_map)
                    
                    # B. 判定剛才收到的 0x02 數據歸屬於誰
                    if "last" in pending_realtime_data:
                        rt_time, rt_data = pending_realtime_data.pop("last")
                        
                        # --- 指令導引判定邏輯 ---
                        # 規則 1: 如果封包自報是 ID 0，則絕對歸屬 Master，不受點名邏輯干擾
                        if hw_id == 0:
                            target_id = 0
                        # 規則 2: 如果自報 ID 與 Master 剛點名的 ID 一致，強化信任度
                        elif (timestamp - last_poll_timestamp) < 1.2 and hw_id == last_polled_slave_id:
                            target_id = hw_id
                        # 規則 3: 若時序合理，以自報 ID 為準
                        elif 0 <= (timestamp - rt_time) <= packet_expire_time:
                            target_id = hw_id
                        else:
                            continue # 數據過期或無法識別，捨棄

                        realtime_map = decode_packet(rt_data, 0x02)
                        if realtime_map:
                            publisher.publish_payload(target_id, 0x02, realtime_map)

            except Exception as e:
                logger.error(f"解析錯誤: {e}")
            finally:
                PACKET_QUEUE.task_done()
        except Exception as e:
            logger.error(f"Worker 循環錯誤: {e}")
            time.sleep(1)

def main():
    # 🚀 載入優化後的介面設定
    full_cfg = load_ui_config()
    app_cfg = full_cfg.get('app', {})
    
    logging.basicConfig(
        level=logging.DEBUG if bool(app_cfg.get("debug_raw_log", False)) else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    logger = logging.getLogger("main")
    logger.info("==========================================")
    logger.info("🚀 JiKong BMS 指令導引監控系統 v2.0.1")
    logger.info(f"📡 模式: {'USB 直連' if app_cfg.get('use_rs485_usb') else 'TCP 網關'}")
    logger.info("==========================================")
    
    # 預熱發布器
    _ = get_publisher(CONFIG_PATH)
    
    # 啟動智能消費者
    worker = threading.Thread(target=process_packets_worker, args=(app_cfg,), daemon=True)
    worker.start()

    # 啟動傳輸層 (生產者)
    transport_inst = create_transport()
    try:
        for pkt_type, pkt_data in transport_inst.packets():
            if not PACKET_QUEUE.full():
                # 放入隊列：包含 (時間戳, 封包類型, 原始數據)
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
            else:
                logger.warning("⚠️ 隊列已滿，請檢查系統效能或增加 packet_expire_time")
    except KeyboardInterrupt:
        logger.info("🛑 系統手動停止")
    except Exception as e:
        logger.error(f"💥 傳輸層崩潰: {e}")

if __name__ == "__main__":
    main()
