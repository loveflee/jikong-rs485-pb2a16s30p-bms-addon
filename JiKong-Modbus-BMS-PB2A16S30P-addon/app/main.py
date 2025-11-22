# main.py
import socket
import struct
import time
import yaml

from publisher import get_publisher

CONFIG_PATH = "config.yaml"


def extract_device_address(packet_0x01: bytes) -> int:
    """
    [核心功能] 從 0x01 (Settings) 封包中提取 Device Address。
    依 bms_registers：Offset 264 U32 => index = 6(header) + 264 = 270
    """
    try:
        if len(packet_0x01) >= 274:  # 270 + 4 bytes (U32)
            device_id = struct.unpack_from('<I', packet_0x01, 270)[0]
            return device_id
        return 0
    except Exception as e:
        print(f"❌ 提取設備地址失敗: {e}")
        return 0


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    tcp = cfg.get("tcp", {})
    app_cfg = cfg.get("app", {})
    return tcp, app_cfg


def main():
    tcp_cfg, app_cfg = load_config()
    publisher = get_publisher()

    TCP_HOST = tcp_cfg.get("host", "192.168.106.13")
    TCP_PORT = int(tcp_cfg.get("port", 502))
    SOCKET_TIMEOUT = int(tcp_cfg.get("timeout", 10))
    BUFFER_SIZE = int(tcp_cfg.get("buffer_size", 4096))
    PACKET_EXPIRE_TIME = float(app_cfg.get("packet_expire_time", 0.4))

    pending_realtime_packet = None
    last_realtime_time = 0

    while True:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT)
            sock.connect((TCP_HOST, TCP_PORT))
            print(f"✅ 已連線到 {TCP_HOST}:{TCP_PORT}，開始監聽 BMS 數據...")

            buffer = bytearray()

            while True:
                try:
                    chunk = sock.recv(1024)

                    if not chunk:
                        print("⚠️ 伺服器端已斷開連線")
                        break

                    buffer.extend(chunk)

                    while True:
                        header_index = buffer.find(b'\x55\xAA\xEB\x90')

                        if header_index == -1:
                            if len(buffer) > BUFFER_SIZE:
                                buffer = buffer[-100:]
                            break

                        if len(buffer) < header_index + 6:
                            break

                        pkt_type = buffer[header_index + 4]

                        # JK 的封包長度目前你量到：0x02 ~= 308, 0x01 ~= 300
                        packet_len = 308 if pkt_type == 0x02 else 300

                        if len(buffer) >= header_index + packet_len:
                            packet = buffer[header_index: header_index + packet_len]

                            if pkt_type == 0x02:
                                # 即時數據 → 每包都發
                                if pending_realtime_packet is not None:
                                    print("⚠️ 警告：上一筆 0x02 尚未等到 0x01 ID，就已被新數據覆蓋")
                                pending_realtime_packet = packet[:]
                                last_realtime_time = time.time()
                                print("📥 [收到 0x02] 即時數據已暫存... 等待 ID (0x01)")

                            elif pkt_type == 0x01:
                                # 設定封包 → 內含 Device ID
                                current_id = extract_device_address(packet)
                                print(f"🔑 [收到 0x01] 參數設定，解析出 ID: {hex(current_id)}")

                                # 發布設定（內部會自己判斷 30 分鐘節流）
                                publisher.process_and_publish(packet, current_id, 0x01)

                                # 若有暫存的 0x02 且未過期 → 一起發布
                                if pending_realtime_packet:
                                    time_diff = time.time() - last_realtime_time
                                    if time_diff < PACKET_EXPIRE_TIME:
                                        print(
                                            f"🚀 [關聯成功] 使用 ID {hex(current_id)} "
                                            f"發布暫存 0x02 (延遲 {time_diff:.2f}s)"
                                        )
                                        publisher.process_and_publish(
                                            pending_realtime_packet, current_id, 0x02
                                        )
                                    else:
                                        print(
                                            f"🗑️ [過期丟棄] 暫存 0x02 超過 "
                                            f"{PACKET_EXPIRE_TIME}s，不發布"
                                        )
                                    pending_realtime_packet = None
                                else:
                                    print("ℹ️ 目前無暫存 0x02 數據")

                            else:
                                # 其他型別，有需要再加
                                pass

                            del buffer[:header_index + packet_len]
                        else:
                            break

                except socket.timeout:
                    if pending_realtime_packet:
                        age = time.time() - last_realtime_time
                        if age > 10:
                            print(
                                f"⚠️ [連線閒置] 有一筆 0x02 超過 {age:.1f} 秒未配對 0x01，丟棄。"
                            )
                            pending_realtime_packet = None
                    continue

                except Exception as e:
                    print(f"❌ 數據處理異常: {e}")
                    buffer = bytearray()

        except Exception as e:
            print(f"❌ 連線錯誤: {e}，5秒後重試...")
            time.sleep(5)
        finally:
            try:
                if sock:
                    sock.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()