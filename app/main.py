import time
import os
import sys
import queue
import threading
import logging
import yaml

# 匯入自定義模組
# 注意：這裡對應你的 transport.py 中的工廠函數
from transport import create_transport 
from decoder import decode_packet, extract_device_address
from publisher import get_publisher

# 設定 Logging
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
    """讀取設定檔供 main 使用"""
    if not os.path.exists(CONFIG_PATH):
        # 為了本地測試，如果 /data/ 下沒有，嘗試讀取當前目錄
        local_path = "test_data/config.yaml"
        if os.path.exists(local_path):
            with open(local_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
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
    
    # 暫存 0x02 封包 (Key: 0 代表最後收到的即時數據)
    pending_realtime_packets = {}

    logger.info("🔧 封包處理工兵 (Worker) 已啟動")

    while True:
        try:
            # 從 Queue 獲取封包項 (Blocking)
            packet_item = PACKET_QUEUE.get()
            
            timestamp, packet_type, packet_data = packet_item
            
            try:
                if packet_type == 0x02:
                    # 暫存即時數據，等待與下一個 0x01 (ID) 配對
                    pending_realtime_packets[0] = (timestamp, packet_data)

                elif packet_type == 0x01:
                    # 這是包含 Device ID 的設定封包
                    device_id = extract_device_address(packet_data)
                    
                    if device_id > 0:
                        # 1. 處理並發布 0x01 (Settings)
                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(device_id, 0x01, settings_map)
                        
                        # 2. 嘗試配對暫存的 0x02 (Realtime)
                        if 0 in pending_realtime_packets:
                            rt_time, rt_data = pending_realtime_packets.pop(0)
                            
                            time_diff = timestamp - rt_time
                            # 檢查配對是否在有效時間內
                            if 0 <= time_diff <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(device_id, 0x02, realtime_map)
                                    logger.info(f"📡 BMS {device_id} 數據更新 (延遲 {time_diff:.3f}s)")
                            else:
                                logger.warning(f"⚠️ 丟棄過期 0x02 封包: 延遲 {time_diff:.3f}s")
                
            except Exception as e:
                logger.error(f"❌ Worker 處理封包時發生錯誤: {e}")
            finally:
                # 標記任務完成
                PACKET_QUEUE.task_done()

        except Exception as e:
            logger.error(f"❌ Worker 發生嚴重錯誤: {e}")
            time.sleep(1)

def main():
    logger.info("🚀 啟動主程式 main.py ...")
    
    # 1. 載入設定
    cfg = load_config()
    app_cfg = cfg.get('app', {})

    # 2. 啟動 Publisher (MQTT 註冊與 LWT)
    _ = get_publisher(CONFIG_PATH)

    # 3. 啟動消費者執行緒 (Worker)
    worker = threading.Thread(
        target=process_packets_worker, 
        args=(app_cfg,), 
        name="WorkerThread", 
        daemon=True
    )
    worker.start()

    logger.info(f"⚙️ 系統初始化完成 (過期時間: {app_cfg.get('packet_expire_time')}s)")

    # 4. 建立並啟動傳輸層 (Producer)
    # 使用工廠模式建立實例 (TcpTransport 或 Rs485Transport)
    transport_inst = create_transport()

    try:
        # 開始接收封包生成器
        # 這裡會根據 config 決定是連線 TCP 還是 開啟 Serial 埠
        for pkt_type, pkt_data in transport_inst.packets():
            # 將收到的原始封包打上時間戳後塞入 Queue
            if not PACKET_QUEUE.full():
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
            else:
                logger.warning("☢️ PACKET_QUEUE 已滿，丟棄封包")
                
    except KeyboardInterrupt:
        logger.info("🛑 收到中斷信號，正在停止程式...")
    except Exception as e:
        logger.critical(f"💥 主程式發生崩潰: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
