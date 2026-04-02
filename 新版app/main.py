# =============================================================================
# main.py - V2.2.4 Production Final (Edge Node Hardened)
# 模組名稱：JK-BMS 監控系統核心調度模組
# 修正亮點：
#   - [Fix] 日誌去噪：優化 _on_transport_down，僅在設備狀態真實改變時輸出警告，消除重複重試造成的日誌污染。
#   - [Fix] 傳輸層斷線即時反灰：USB/TCP 斷線瞬間主動推送所有設備 offline (V2.2.4)
#   - [Fix] 單調時鐘：全面替換 time.time()，免疫 NTP 校時 (承襲 V2.2.3)
#   - [Fix] I/O 原子寫入：防禦跳電產生 0-byte 殭屍檔 (承襲 V2.2.3)
#   - [Fix] Watchdog TOCTOU 防禦 (承襲 V2.2.2)
#   - [Fix] task_done 孤兒防禦 (承襲 V2.2.2)
# =============================================================================

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

PACKET_QUEUE = queue.Queue(maxsize=800)
OPTIONS_PATH = "/data/options.json"
CONFIG_PATH = "/data/config.yaml"

DEVICE_STATUS_MAP = {}
DEVICE_TIMEOUT = 60.0
DEVICE_LOCK = threading.Lock()


def load_ui_config():
    # 模式 1：HA Add-on 環境
    if os.path.exists(OPTIONS_PATH):
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

        # [V2.2.3] POSIX 原子寫入
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, sort_keys=False)
        os.replace(tmp_path, CONFIG_PATH)
        return config

    # 模式 2：獨立 Docker 模式
    elif os.path.exists(CONFIG_PATH):
        logging.info("ℹ️ 偵測為獨立 Docker 模式，讀取現有 config.yaml")
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        # [V2.2.3] 0-byte 殭屍檔防禦
        if not cfg:
            logging.error("❌ config.yaml 為空或損壞 (可能因跳電引起)，請檢查 /data 目錄")
            sys.exit(1)
        return cfg

    else:
        logging.error("❌ 找不到 HA options.json，也沒有 config.yaml！請確認 /data 掛載與設定檔。")
        sys.exit(1)


def _on_transport_down():
    """
    🚀 [V2.2.4] 傳輸層斷線回調：USB/TCP 斷線瞬間主動將所有在線設備標記為 offline。
    🚀 [Fix] 消除重複噪訊：僅在真實有設備在線時才觸發 MQTT 推送與警告日誌。
    """
    logger = logging.getLogger("main")

    devices_to_offline = []
    with DEVICE_LOCK:
        for dev_id, info in DEVICE_STATUS_MAP.items():
            if info["state"] == "online":
                info["state"] = "offline"
                devices_to_offline.append(dev_id)

    # 如果沒有設備需要下線，直接 return，保持日誌乾淨
    if not devices_to_offline:
        return

    logger.warning(f"🔌 傳輸層斷線，主動推送 {len(devices_to_offline)} 台設備 offline")
    publisher = get_publisher(CONFIG_PATH)
    for dev_id in devices_to_offline:
        publisher.publish_device_status(dev_id, "offline")
    logger.warning(f"📴 已標記設備離線: {devices_to_offline}")


def device_watchdog_worker():
    logger = logging.getLogger("watchdog")
    publisher = get_publisher(CONFIG_PATH)

    while True:
        try:
            # [V2.2.3] 單調時鐘，免疫 NTP 跳躍
            now = time.monotonic()
            with DEVICE_LOCK:
                devices_snapshot = DEVICE_STATUS_MAP.copy()

            for dev_id, info in devices_snapshot.items():
                if info["state"] == "online" and (now - info["last_seen"]) > DEVICE_TIMEOUT:
                    with DEVICE_LOCK:
                        # [V2.2.2] TOCTOU 二次確認
                        live_info = DEVICE_STATUS_MAP.get(dev_id)
                        if live_info and live_info["state"] == "online" and \
                                (now - live_info["last_seen"]) > DEVICE_TIMEOUT:
                            live_info["state"] = "offline"
                        else:
                            continue
                    publisher.publish_device_status(dev_id, "offline")
                    logger.warning(f"⚠️ 設備掉線: BMS {dev_id} 已超過 {DEVICE_TIMEOUT} 秒未回應")
            time.sleep(2)
        except Exception:
            logger.exception("Watchdog 循環發生未知異常")
            time.sleep(10)


def process_packets_worker(app_config):
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
            # [V2.2.3] 單調時鐘
            now = time.monotonic()

            if "last" in pending_realtime_data:
                if now - pending_realtime_data["last"][0] > packet_expire_time:
                    pending_realtime_data.clear()

            for sid in list(pending_cmds.keys()):
                if now - pending_cmds[sid][0] > 5.0:
                    del pending_cmds[sid]

            # [V2.2.2] task_done 孤兒防禦
            packet_item = PACKET_QUEUE.get()
            try:
                timestamp, packet_type, packet_data = packet_item

                if packet_type == 0x10:
                    cmd_map = decode_packet(packet_data, 0x10)
                    if cmd_map:
                        target_id = cmd_map.get("target_slave_id")
                        if is_debug:
                            logger.debug(f" [詢問] Master 正在呼叫從機 ID: {target_id}")
                        last_polled_slave_id = target_id
                        last_poll_timestamp = timestamp
                        pending_cmds[target_id] = (timestamp, cmd_map)
                    continue

                if packet_type == 0x02:
                    pending_realtime_data["last"] = (timestamp, packet_data)
                    continue

                if packet_type == 0x01:
                    hw_id = extract_device_address(packet_data)
                    if hw_id is None:
                        if is_debug:
                            logger.debug("[忽略] 封包解析硬體 ID 失敗")
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
                        # [V2.2.3] 單調時鐘
                        now_ts = time.monotonic()
                        with DEVICE_LOCK:
                            dev_info = DEVICE_STATUS_MAP.setdefault(
                                target_publish_id, {"last_seen": 0.0, "state": "offline"}
                            )
                            dev_info["last_seen"] = now_ts
                            current_state = dev_info["state"]
                            if current_state == "offline":
                                dev_info["state"] = "online"

                        if current_state == "offline":
                            publisher.publish_device_status(target_publish_id, "online")

                        if target_publish_id in pending_cmds:
                            _, actual_cmd = pending_cmds.pop(target_publish_id)
                            publisher.publish_payload(0, 0x10, actual_cmd)

                        settings_map = decode_packet(packet_data, 0x01)
                        if settings_map:
                            publisher.publish_payload(target_publish_id, 0x01, settings_map)

                        if "last" in pending_realtime_data:
                            rt_time, rt_data = pending_realtime_data.pop("last")
                            if (timestamp - rt_time) <= packet_expire_time:
                                realtime_map = decode_packet(rt_data, 0x02)
                                if realtime_map:
                                    publisher.publish_payload(target_publish_id, 0x02, realtime_map)

                    if (timestamp - last_poll_timestamp) > 5.0:
                        pending_cmds.clear()

            except Exception:
                logger.exception("解析封包內容時發生異常")
            finally:
                PACKET_QUEUE.task_done()

        except Exception:
            logger.exception("Worker 執行緒發生嚴重循環錯誤")
            time.sleep(1)


def main():
    full_cfg = load_ui_config()
    app_cfg = full_cfg.get('app', {})

    logging.basicConfig(
        level=logging.DEBUG if bool(app_cfg.get("debug_raw_log", False)) else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
        force=True
    )

    logger = logging.getLogger("main")
    logger.info("==========================================")
    logger.info(" JiKong BMS 監控系統 v2.2.4 Production Final")
    logger.info("==========================================")

    _ = get_publisher(CONFIG_PATH)

    threading.Thread(target=process_packets_worker, args=(app_cfg,), daemon=True).start()
    threading.Thread(target=device_watchdog_worker, daemon=True).start()

    transport_inst = create_transport()
    # 🚀 [V2.2.4] 注入斷線回調：USB/TCP 斷線瞬間觸發，無需等待 Watchdog 60 秒
    transport_inst.on_link_down = _on_transport_down

    try:
        for pkt_type, pkt_data in transport_inst.packets():
            try:
                # [V2.2.3] 入隊時間戳使用單調時鐘
                PACKET_QUEUE.put((time.monotonic(), pkt_type, pkt_data), block=False)
            except queue.Full:
                logger.warning("⚠️ PACKET_QUEUE 已滿，丟棄封包")
            except Exception:
                logger.exception("存入隊列時發生未知異常")
    except KeyboardInterrupt:
        logger.info(" 系統由使用者停止")
    except Exception:
        logger.exception("💥 傳輸層發生致命故障")


if __name__ == "__main__":
    main()
