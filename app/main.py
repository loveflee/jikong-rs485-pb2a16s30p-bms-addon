import time
import os
import sys
import queue
import threading
import logging
from typing import Optional
import yaml

# 匯入模組
from transport import Transport
from decoder import decode_packet, extract_device_address
from publisher import get_publisher

# 設定 Log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 全域變數
PACKET_QUEUE = queue.Queue(maxsize=100)
CONFIG_PATH = "/data/config.yaml"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"❌ 找不到設定檔: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def process_packets_worker(app_config):
    """
    消費者執行緒：從 Queue 取出封包並處理
    """
    publisher = get_publisher(CONFIG_PATH)
    packet_expire_time = app_config.get('packet_expire_time', 1.0)
    
    # 用來暫存即時數據 (0x02)，等待設定數據 (0x01)
    # Key: Device Address (int), Value: (timestamp, packet_data)
    pending_realtime_packets = {}

    logger.info("🔧 封包處理工兵 (Worker) 已啟動")

    while True:
        try:
            # 1. 嘗試從 Queue 拿資料 (Blocking)
            # 這裡不設 timeout，讓它阻塞等待，避免 busy loop
            packet_item = PACKET_QUEUE.get()
            
            # ---------------------------------------------------------
            # 只有當程式執行到這裡，代表 get() 成功了，
            # 我們才有責任在處理完後呼叫一次 task_done()
            # ---------------------------------------------------------

            timestamp, packet_type, packet_data = packet_item
            
            try:
                # 處理邏輯
                if packet_type == 0x02:
                    # 如果是即時數據 (0x02)，裡面沒有 ID
                    # 我們先暫存起來，不做任何事，等待下一個 0x01
                    # 注意：這裡處理完了，就是處理完了，稍後要 task_done
                    pending_realtime_packets[0] = (timestamp, packet_data) # 暫存到 Key 0 (假設單機或循序)
                    # 如果你的 Transport 能保證順序，這裡通常暫存最後一筆即可

                elif packet_type == 0x01:
                    # 如果是設定數據 (0x01)，裡面有 ID
                    device_id = extract_device_address(packet_data)
                    
                    if device_id > 0:
                        # 1. 解碼並發布 0x01 (Settings)
                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(device_id, 0x01, settings_map)
                        
                        # 2. 檢查有沒有暫存的 0x02 (Realtime)
                        if 0 in pending_realtime_packets:
                            rt_time, rt_data = pending_realtime_packets.pop(0)
                            
                            # 檢查是否過期 (配對時間差)
                            time_diff = timestamp - rt_time
                            if 0 <= time_diff <= packet_expire_time:
                                # 配對成功！解碼 0x02
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(device_id, 0x02, realtime_map)
                                    logger.info(f"📡 BMS {device_id} 數據更新 (延遲 {time_diff:.3f}s)")
                            else:
                                logger.warning(f"⚠️ 丟棄過期封包: 延遲 {time_diff:.3f}s > {packet_expire_time}s")
                    else:
                        logger.debug(f"⚠️ 無效的設備 ID: {device_id}")

            except Exception as e:
                logger.error(f"❌ 處理封包時發生錯誤: {e}", exc_info=True)
            
            finally:
                # ✅ 關鍵修正：確保每個 get() 只對應一個 task_done()
                # 無論處理過程是否報錯，只要 get 出來了，就要標記完成
                PACKET_QUEUE.task_done()

        except Exception as e:
            # 這是最外層的防護，避免 Worker 整個崩潰
            logger.error(f"❌ Worker 迴圈發生嚴重錯誤: {e}", exc_info=True)
            time.sleep(1) # 避免死迴圈狂刷 log

def main():
    print("🚀 啟動主程式 main.py ...")
    
    # 1. 載入設定
    cfg = load_config()
    app_cfg = cfg.get('app', {})
    conn_cfg = cfg.get('connection', {}) # 兼容 Go 版結構
    if not conn_cfg: # 回退舊結構
        conn_cfg = {
            'type': cfg.get('connection_type', 'serial'),
            'serial': cfg.get('serial', {}),
            'tcp': cfg.get('tcp', {})
        }

    # 2. 啟動 Publisher (MQTT)
    # Publisher 會在內部自行連線
    _ = get_publisher(CONFIG_PATH)

    # 3. 啟動 Worker Thread
    worker = threading.Thread(target=process_packets_worker, args=(app_cfg,), name="WorkerThread", daemon=True)
    worker.start()

    logger.info("🚀 JiKong BMS main (Async Queue Mode) 啟動...")
    logger.info(f"⚙️ 封包過期時間: {app_cfg.get('packet_expire_time')}s, Queue大小: {PACKET_QUEUE.maxsize}")

    # 4. 啟動 Transport (Producer) - 這會阻塞主執行緒
    transport = Transport(conn_cfg, PACKET_QUEUE, app_cfg)
    try:
        transport.run()
    except KeyboardInterrupt:
        logger.info("🛑 收到中斷信號，正在停止...")

if __name__ == "__main__":
    main()
