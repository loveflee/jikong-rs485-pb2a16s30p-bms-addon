import time
import os
import sys
import queue
import threading
import logging
import yaml
import json

# 匯入自定義模組
from transport import create_transport 
from decoder import decode_packet, extract_device_address
from publisher import get_publisher

# 全域變數
# 加大緩衝區，因為現在要同時處理 Master 指令與 BMS 數據
PACKET_QUEUE = queue.Queue(maxsize=300)
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
    消費者執行緒：處理 JK BMS 數據配對與 Master 指令解析
    """
    logger = logging.getLogger("jk_bms_worker")
    publisher = get_publisher(CONFIG_PATH)
    # 建議在 config.yaml 將此值設為 2.0
    packet_expire_time = app_config.get('packet_expire_time', 2.0)
    
    # 暫存最後收到的 0x02 封包，等待 0x01 來配對 ID
    pending_realtime_packets = {}

    logger.info("🔧 雙協議處理工兵 (Worker) 已啟動")

    while True:
        try:
            packet_item = PACKET_QUEUE.get()
            timestamp, packet_type, packet_data = packet_item
            
            try:
                # --- 邏輯 A: 處理 Master Modbus 指令 (還原後的邏輯) ---
                if packet_type == 0x10:
                    cmd_map = decode_packet(packet_data, 0x10)
                    if cmd_map:
                        slave_id = cmd_map.get("slave_id", 0)
                        # 將 Master 指令發布到對應 ID 的 topic
                        publisher.publish_payload(slave_id, 0x10, cmd_map)
                        logger.info(f"🎮 監聽到 Master 指令 -> Slave {slave_id} (Reg: {cmd_map.get('register')})")
                    continue # 處理完指令，直接跳過後續 JK 邏輯

                # --- 邏輯 B: 處理 JK BMS 廣播數據 ---
                if packet_type == 0x02:
                    # 暫存即時數據 (由於 0x02 沒 ID，我們存入 "last" 等待配對)
                    pending_realtime_packets["last"] = (timestamp, packet_data)

                elif packet_type == 0x01:
                    device_id = extract_device_address(packet_data)
                    
                    if device_id > 0:
                        # 1. 解碼並發布 0x01 (Settings)
                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(device_id, 0x01, settings_map)
                        
                        # 2. 配對暫存的 0x02
                        if "last" in pending_realtime_packets:
                            rt_time, rt_data = pending_realtime_packets.pop("last")
                            
                            time_diff = timestamp - rt_time
                            if 0 <= time_diff <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(device_id, 0x02, realtime_map)
                                    logger.info(f"📡 BMS {device_id} 數據更新 (延遲 {time_diff:.3f}s)")
                            else:
                                logger.warning(f"⚠️ 丟棄過期 0x02 封包: 延遲 {time_diff:.3f}s")

            except Exception as e:
                logger.error(f"❌ Worker 處理數據時出錯: {e}")
            finally:
                PACKET_QUEUE.task_done()

        except Exception as e:
            logger.error(f"❌ Worker 發生嚴重錯誤: {e}")
            time.sleep(1)

def main():
    # 1. 載入初步設定
    cfg = load_config()
    app_cfg = cfg.get('app', {})
    
    # 動態日誌等級
    is_debug = bool(app_cfg.get("debug_raw_log", False))
    log_level = logging.DEBUG if is_debug else logging.INFO
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    logger = logging.getLogger(__name__)
    logger.info("🚀 啟動主程式 main.py ...")
    if is_debug:
        logger.warning("🔍 除錯模式已開啟，將顯示原始 Master/BMS 數據流")

    # 2. 啟動 Publisher (MQTT 註冊)
    _ = get_publisher(CONFIG_PATH)

    # 3. 啟動解析執行緒
    worker = threading.Thread(
        target=process_packets_worker, 
        args=(app_cfg,), 
        name="WorkerThread", 
        daemon=True
    )
    worker.start()

    # 4. 建立傳輸層 (Producer)
    # create_transport 會根據 config 建立支援雙協議捕獲的 Rs485Transport
    transport_inst = create_transport()

    try:
        # 開始接收數據流
        for pkt_type, pkt_data in transport_inst.packets():
            if not PACKET_QUEUE.full():
                # 打上時間戳並塞入隊列
                PACKET_QUEUE.put((time.time(), pkt_type, pkt_data))
            else:
                logger.warning("☢️ PACKET_QUEUE 滿載，請檢查系統效能或加大 Queue")
                
    except KeyboardInterrupt:
        logger.info("🛑 收到結束指令")
    except Exception as e:
        logger.critical(f"💥 主程式崩潰: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
