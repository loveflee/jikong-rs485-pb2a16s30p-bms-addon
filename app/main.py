import time
import os
import sys
import queue
import threading
import logging
import yaml

# 匯入自定義模組
from transport import create_transport 
from decoder import decode_packet, extract_device_address
from publisher import get_publisher

# 全域變數
# 🟢 加大 Queue 緩衝區到 200，防止多台 BMS 監聽時溢位
PACKET_QUEUE = queue.Queue(maxsize=200)
CONFIG_PATH = "/data/config.yaml"

def load_config():
    """讀取設定檔"""
    if not os.path.exists(CONFIG_PATH):
        # 為了本地測試，如果 /data/ 下沒有，嘗試讀取當前目錄
        local_path = "test_data/config.yaml"
        if os.path.exists(local_path):
            with open(local_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        print(f"❌ 找不到設定檔: {CONFIG_PATH}")
        sys.exit(1)
        
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def process_packets_worker(app_config):
    """
    消費者執行緒：從 Queue 取出封包並處理
    """
    logger = logging.getLogger("jk_bms_worker")
    publisher = get_publisher(CONFIG_PATH)
    # 🟢 建議在 config.yaml 將此值設為 2.0，以適應多台 BMS
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    
    # 暫存 0x02 封包 (Key: DeviceID, Value: (timestamp, packet_data))
    # 使用字典儲存各個 ID 的數據，避免多台 BMS 混淆
    pending_realtime_packets = {}

    logger.info("🔧 封包處理工兵 (Worker) 已啟動")

    while True:
        try:
            packet_item = PACKET_QUEUE.get()
            timestamp, packet_type, packet_data = packet_item
            
            try:
                if packet_type == 0x02:
                    # 暫存即時數據。在監聽模式下，我們先不知道這包是誰的
                    # 所以先存一個臨時 Key，等 0x01 出現
                    pending_realtime_packets["last"] = (timestamp, packet_data)

                elif packet_type == 0x01:
                    device_id = extract_device_address(packet_data)
                    
                    if device_id > 0:
                        # 1. 發布 0x01 (Settings)
                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(device_id, 0x01, settings_map)
                        
                        # 2. 嘗試配對暫存的 0x02
                        if "last" in pending_realtime_packets:
                            rt_time, rt_data = pending_realtime_packets.pop("last")
                            
                            time_diff = timestamp - rt_time
                            if 0 <= time_diff <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(device_id, 0x02, realtime_map)
                                    logger.info(f"📡 BMS {device_id} 數據更新 (延遲 {time_diff:.3f}s)")
                            else:
                                logger.warning(f"⚠️ 丟棄過期 0x02 封包: 延遲 {time_diff:.3f}s (建議加大 expire_time)")
                
            except Exception as e:
                logger.error(f"❌ Worker 處理封包錯誤: {e}")
            finally:
                PACKET_QUEUE.task_done()

        except Exception as e:
            logger.error(f"❌ Worker 嚴重錯誤: {e}")
            time.sleep(1)

def main():
    # 1. 載入設定
    cfg = load_config()
    app_cfg = cfg.get('app', {})
    
    # ✅ [核心修正] 動態日誌等級設定
    # 只有當 debug_raw_log 為 true 時，才開啟 DEBUG 等級，否則只顯示 INFO
    is_debug = bool(app_cfg.get("debug_raw_log", False))
    log_level = logging.DEBUG if is_debug else logging.INFO
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # 重新獲取 logger
    logger = logging.getLogger(__name__)
    logger.info("🚀 啟動主程式 main.py ...")
    if is_debug:
        logger.warning("🔍 除錯模式已開啟，將顯示原始 RX 數據流")

    # 2. 啟動 Publisher
    _ = get_publisher(CONFIG_PATH)

    # 3. 啟動 Worker
    worker = threading.Thread(target=process_packets_worker, args=(app_cfg,), name="WorkerThread", daemon=True)
    worker.start()

    # 4. 建立並啟動傳輸層
    transport_inst = create_transport()

    try:
        # 開始接收封包生成器
        for pkt_type, pkt_data in transport_inst.packets():
            if not PACKET_QUEUE.full():
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
            else:
                logger.warning("☢️ PACKET_QUEUE 溢位，數據
