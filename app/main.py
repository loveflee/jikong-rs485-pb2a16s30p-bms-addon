# main.py
import time

from transport import create_transport, BaseTransport
from publisher import get_publisher
from decoder import decode_packet, extract_device_address


def main():
    # 建立通訊層（TCP or RS485）
    transport: BaseTransport = create_transport()
    # 建立 MQTT 發佈器
    publisher = get_publisher(config_path="/data/config.yaml")

    # 這裡還是用原本的 "0x02 綁定邏輯"
    pending_realtime_packet = None
    last_realtime_time = 0.0

    # 從 config 裡拿到 expire_time（放在 transport.app_cfg）
    PACKET_EXPIRE_TIME = float(transport.app_cfg.get("packet_expire_time", 0.4))

    print("🚀 主程式啟動，開始從 transport 收封包...")

    # 持續從 transport 收 (packet_type, raw_bytes)
    for pkt_type, packet in transport.packets():
        try:
            if pkt_type == 0x02:
                # 先暫存，等 0x01 來補 ID
                if pending_realtime_packet is not None:
                    print("⚠️ 警告：上一筆 0x02 尚未等到 0x01 ID，就已被新數據覆蓋")
                pending_realtime_packet = packet[:]
                last_realtime_time = time.time()
                print("📥 [收到 0x02] 即時數據已暫存... 等待 ID (0x01)")

            elif pkt_type == 0x01:
                # 解析設備 ID
                current_id = extract_device_address(packet)
                print(f"🔑 [收到 0x01] 參數設定，解析出 ID: {hex(current_id)}")

                # 先把 0x01 封包解碼成 dict
                settings_payload = decode_packet(packet, 0x01)
                # 發佈 settings
                publisher.publish_payload(current_id, 0x01, settings_payload)

                # 處理之前暫存的 0x02
                if pending_realtime_packet:
                    time_diff = time.time() - last_realtime_time
                    if time_diff < PACKET_EXPIRE_TIME:
                        print(
                            f"🚀 [關聯成功] 使用 ID {hex(current_id)} 發布暫存 0x02 (延遲 {time_diff:.2f}s)"
                        )
                        realtime_payload = decode_packet(pending_realtime_packet, 0x02)
                        publisher.publish_payload(current_id, 0x02, realtime_payload)
                    else:
                        print(
                            f"🗑️ [過期丟棄] 暫存 0x02 超過 {PACKET_EXPIRE_TIME}s， 不發布"
                        )
                    pending_realtime_packet = None
                else:
                    print("ℹ️ 目前無暫存 0x02 數據")

            else:
                # 未知封包型別先略過
                print(f"ℹ️ 收到未知封包型別: {hex(pkt_type)}，略過")

        except Exception as e:
            print(f"❌ main 處理封包發生錯誤: {e}")


if __name__ == "__main__":
    main()
