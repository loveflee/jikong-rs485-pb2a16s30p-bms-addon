import time
import os
import sys
import queue
import threading
import logging
import yaml

from transport import create_transport 
from decoder import decode_packet, extract_device_address
from publisher import get_publisher

PACKET_QUEUE = queue.Queue(maxsize=500)
CONFIG_PATH = "/data/config.yaml"

def load_config():
    if not os.path.exists(CONFIG_PATH): sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def process_packets_worker(app_config):
    # 此處不再頻繁輸出 INFO Log
    publisher = get_publisher(CONFIG_PATH)
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    pending_realtime_packets = {}

    while True:
        try:
            packet_item = PACKET_QUEUE.get()
            timestamp, packet_type, packet_data = packet_item
            
            try:
                # 1. 處理 Master 指令 (靜默處理，不印 Log)
                if packet_type == 0x10:
                    cmd_map = decode_packet(packet_data, 0x10)
                    if cmd_map:
                        publisher.publish_payload(cmd_map.get("slave_id", 0), 0x10, cmd_map)
                    continue 

                # 2. 處理 JK BMS 廣播數據 (靜默更新)
                if packet_type == 0x02:
                    pending_realtime_packets["last"] = (timestamp, packet_data)
                elif packet_type == 0x01:
                    device_id = extract_device_address(packet_data)
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
            except Exception: pass # 解析錯誤靜默處理
            finally: PACKET_QUEUE.task_done()
        except Exception: time.sleep(1)

def main():
    cfg = load_config()
    app_cfg = cfg.get('app', {})
    
    # 日誌等級維持由設定控制，但 process_packets_worker 已經不主動印 INFO
    logging.basicConfig(
        level=logging.DEBUG if bool(app_cfg.get("debug_raw_log", False)) else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    logger = logging.getLogger(__name__)
    logger.info("🚀 JiKong BMS 全功能監聽系統已啟動 (BMS 0/2/3)")
    
    _ = get_publisher(CONFIG_PATH)
    worker = threading.Thread(target=process_packets_worker, args=(app_cfg,), daemon=True)
    worker.start()

    transport_inst = create_transport()
    try:
        for pkt_type, pkt_data in transport_inst.packets():
            if not PACKET_QUEUE.full():
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
    except KeyboardInterrupt:
        logger.info("🛑 系統停止")

if __name__ == "__main__":
    main()
