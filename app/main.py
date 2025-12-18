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

# 加大緩衝區以應對高頻率的 Master 指令與數據併發
PACKET_QUEUE = queue.Queue(maxsize=500)
CONFIG_PATH = "/data/config.yaml"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def process_packets_worker(app_config):
    logger = logging.getLogger("jk_bms_worker")
    publisher = get_publisher(CONFIG_PATH)
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    
    # 暫存 0x02 封包等待 0x01 配對
    pending_realtime_packets = {}

    logger.info("🔧 雙協議處理工兵已啟動 (Master + BMS)")

    while True:
        try:
            packet_item = PACKET_QUEUE.get()
            timestamp, packet_type, packet_data = packet_item
            
            try:
                # --- 邏輯 A: 處理 Master Modbus 指令 ---
                if packet_type == 0x10:
                    cmd_map = decode_packet(packet_data, 0x10)
                    if cmd_map:
                        slave_id = cmd_map.get("slave_id", 0)
                        publisher.publish_payload(slave_id, 0x10, cmd_map)
                        logger.info(f"🎮 Master -> ID {slave_id} | Reg: {cmd_map.get('register')}")
                    continue 

                # --- 邏輯 B: 處理 JK BMS 數據 ---
                if packet_type == 0x02:
                    pending_realtime_packets["last"] = (timestamp, packet_data)

                elif packet_type == 0x01:
                    device_id = extract_device_address(packet_data)
                    if device_id > 0:
                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(device_id, 0x01, settings_map)
                        
                        if "last" in pending_realtime_packets:
                            rt_time, rt_data = pending_realtime_packets.pop("last")
                            time_diff = timestamp - rt_time
                            if 0 <= time_diff <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(device_id, 0x02, realtime_map)
                                    logger.info(f"📡 BMS {device_id} 更新 (延遲 {time_diff:.3f}s)")
            except Exception as e:
                logger.error(f"❌ 解析錯誤: {e}")
            finally:
                PACKET_QUEUE.task_done()
        except Exception as e:
            time.sleep(1)

def main():
    cfg = load_config()
    app_cfg = cfg.get('app', {})
    
    # 動態日誌等級控制
    is_debug = bool(app_cfg.get("debug_raw_log", False))
    logging.basicConfig(
        level=logging.DEBUG if is_debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    logger = logging.getLogger(__name__)
    _ = get_publisher(CONFIG_PATH)

    worker = threading.Thread(target=process_packets_worker, args=(app_cfg,), daemon=True)
    worker.start()

    transport_inst = create_transport()
    try:
        for pkt_type, pkt_data in transport_inst.packets():
            if not PACKET_QUEUE.full():
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
