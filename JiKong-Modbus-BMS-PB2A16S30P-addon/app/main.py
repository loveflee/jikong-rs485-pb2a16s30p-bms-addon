# main.py
#
# 流程：
#   1. 讀 config.yaml
#   2. 建立 transport (Modbus Gateway or RS485 USB)
#   3. 建立 MQTT publisher
#   4. 進入主迴圈：
#        - transport.iter_packets() 取得 (pkt_type, packet)
#        - 如果 pkt_type == 0x02 → 暫存 pending_realtime_packet
#        - 如果 pkt_type == 0x01 →
#              a. 解析 device_id
#              b. decode 0x01 → dict → publish
#              c. 若有 pending 0x02 且未過期 → decode 0x02 → publish
#
#   ⚠️ 你要求「0x02 綁定邏輯完整搬到 main.py」→ 已經放在這裡了。

import os
import sys
import time
import yaml
from typing import Optional, Tuple

from transport import create_transport, BaseTransport
from decoder import extract_device_address, decode_packet_to_dict
from publisher import get_publisher


CONFIG_PATH = "/data/config.yaml"


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 找不到設定檔 {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    tcp = cfg.get("tcp", {})
    mqtt = cfg.get("mqtt", {})
    app_cfg = cfg.get("app", {})
    serial_cfg = cfg.get("serial", {})
    return tcp, mqtt, app_cfg, serial_cfg


def hexdump(prefix: str, data: bytes):
    """
    debug_raw_log 模式下用：把 raw 資料用 HEX 顯示。
    """
    hex_str = " ".join(f"{b:02X}" for b in data)
    print(f"{prefix} RAW ({len(data)} bytes): {hex_str}")


def main():
    tcp_cfg, mqtt_cfg, app_cfg, serial_cfg = load_config()

    PACKET_EXPIRE_TIME = float(app_cfg.get("packet_expire_time", 0.4))
    debug_raw_log = bool(app_cfg.get("debug_raw_log", False))

    # 建立 MQTT publisher（會建立 MQTT 連線）
    publisher = get_publisher(config_path=CONFIG_PATH)

    # 建立 Transport（TCP 或 RS485 USB）
    transport: BaseTransport = create_transport(tcp_cfg, serial_cfg, app_cfg)

    pending_realtime_packet: Optional[bytes] = None
    last_realtime_time: float = 0.0

    while True:
        try:
            print("🔌 開始建立連線並監聽 BMS 數據...")
            transport.open()
            print("✅ Transport 已開啟，開始收封包...")

            # 這裡開始迴圈讀封包
            for pkt_type, packet in transport.iter_packets():
                # 如果有開除錯模式，就印出 raw hexdump
                if debug_raw_log:
                    hexdump(f"[pkt_type={hex(pkt_type)}]", packet)

                # ---------------------------
                # 0x02: Realtime 資料 → 先暫存
                # ---------------------------
                if pkt_type == 0x02:
                    if pending_realtime_packet is not None:
                        print(
                            "⚠️ 警告：上一筆 0x02 尚未配對到 0x01，就被新數據覆蓋"
                        )
                    pending_realtime_packet = packet[:]
                    last_realtime_time = time.time()
                    print("📥 [收到 0x02] 即時數據已暫存，等待 0x01 取得 ID...")
                    continue

                # ---------------------------
                # 0x01: Settings → 解析 ID、發佈、並嘗試綁定 0x02
                # ---------------------------
                if pkt_type == 0x01:
                    device_id = extract_device_address(packet)
                    print(
                        f"🔑 [收到 0x01] 參數設定封包，解析出 ID: {hex(device_id)}"
                    )

                    # 解析 0x01 → dict
                    payload_settings = decode_packet_to_dict(
                        packet, packet_type=0x01
                    )
                    # 發佈 0x01（settings）
                    publisher.publish_packet(
                        device_id=device_id,
                        packet_type=0x01,
                        payload_dict=payload_settings,
                    )

                    # 有沒有 pending 的 0x02 ?
                    if pending_realtime_packet is not None:
                        time_diff = time.time() - last_realtime_time
                        if time_diff < PACKET_EXPIRE_TIME:
                            print(
                                f"🚀 [綁定成功] 使用 ID {hex(device_id)} 發佈暫存 0x02 (延遲 {time_diff:.2f}s)"
                            )
                            payload_rt = decode_packet_to_dict(
                                pending_realtime_packet, packet_type=0x02
                            )
                            publisher.publish_packet(
                                device_id=device_id,
                                packet_type=0x02,
                                payload_dict=payload_rt,
                            )
                        else:
                            print(
                                f"🗑️ [過期丟棄] 暫存 0x02 超過 {PACKET_EXPIRE_TIME}s，放棄"
                            )
                        pending_realtime_packet = None
                    else:
                        print("ℹ️ 目前沒有暫存的 0x02 即時數據")
                    continue

                # 如未來有其他 pkt_type，可在這裡加 elif
                print(f"ℹ️ 收到未處理 pkt_type: {hex(pkt_type)}")

        except KeyboardInterrupt:
            print("🛑 收到中斷訊號，準備關閉...")
            break
        except Exception as e:
            print(f"❌ 連線/處理錯誤: {e}，5 秒後重試...")
            time.sleep(5)
        finally:
            try:
                transport.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
