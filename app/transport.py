# transport.py
import socket
import time
import sys
import os
import yaml
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Generator

try:
    import serial  # RS485 to USB 使用 (pyserial)
except ImportError:
    serial = None


CONFIG_PATH = "/data/config.yaml"

HEADER = b"\x55\xAA\xEB\x90"
PACKET_LEN_01 = 300
PACKET_LEN_02 = 308


def load_config():
    """從 /data/config.yaml 讀取整體設定。"""
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 找不到設定檔 {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class BaseTransport(ABC):
    """
    通訊層抽象基底類別：
    - 負責從「來源」收包（TCP 或 RS485）
    - 組合成完整封包後，產生 (packet_type, raw_bytes)
    - 不處理解碼、不發 MQTT
    """

    def __init__(self, cfg: dict):
        self.tcp_cfg = cfg.get("tcp", {})
        self.serial_cfg = cfg.get("serial", {})
        self.app_cfg = cfg.get("app", {})
        self.buffer_size = int(self.tcp_cfg.get("buffer_size", 4096))
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        """
        連線並持續產生封包。
        yield (packet_type, packet_bytes)
        """
        ...


class TcpTransport(BaseTransport):
    """
    使用 Modbus Gateway (TCP) 的傳輸方式
    """

    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        host = self.tcp_cfg.get("host", "127.0.0.1")
        port = int(self.tcp_cfg.get("port", 502))
        timeout = int(self.tcp_cfg.get("timeout", 10))

        while True:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                print(f"✅ 已連線到 {host}:{port}，開始監聽 BMS 數據 (TCP)...")

                buffer = bytearray()

                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        print("⚠️ 伺服器端已斷開連線 (TCP)")
                        break

                    # 除錯模式：只印 raw hexdump
                    if self.debug_raw_log:
                        hex_str = " ".join(f"{b:02X}" for b in chunk)
                        print(f"[DEBUG RAW] ({len(chunk)} bytes): {hex_str}")

                    buffer.extend(chunk)

                    # 解析 buffer 中的完整封包
                    while True:
                        header_index = buffer.find(HEADER)
                        if header_index == -1:
                            # 沒有找到 header，避免 buffer 無限長，保留最後 100 bytes
                            if len(buffer) > self.buffer_size:
                                buffer = buffer[-100:]
                            break

                        # 確保有 header + type + len 至少 6 bytes
                        if len(buffer) < header_index + 6:
                            break

                        pkt_type = buffer[header_index + 4]
                        # 第 5 byte 通常是長度 or 分類，我們用現有規則：
                        packet_len = PACKET_LEN_02 if pkt_type == 0x02 else PACKET_LEN_01

                        if len(buffer) >= header_index + packet_len:
                            packet = buffer[header_index:header_index + packet_len]

                            # 切出去丟給上層
                            yield pkt_type, bytes(packet)

                            # 丟掉已處理的部分
                            del buffer[:header_index + packet_len]
                        else:
                            # 封包尚未完整，等待下一次 recv
                            break

            except socket.timeout:
                print("⚠️ TCP 連線逾時，重新連線...")
            except Exception as e:
                print(f"❌ TCP 傳輸層異常: {e}，5 秒後重試...")
                time.sleep(5)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass


class Rs485Transport(BaseTransport):
    """
    使用 RS485 to USB (例如 /dev/ttyUSB0) 的傳輸方式
    - 讀取 serial 資料
    - 組合與 TCP 同樣格式的封包 0x01 / 0x02
    - 這裡先簡單示範：假設 BMS 透傳出來的資料格式一樣
    """

    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        if serial is None:
            print("❌ 未安裝 pyserial，無法使用 RS485 模式")
            return

        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baudrate = int(self.serial_cfg.get("baudrate", 115200))
        timeout = float(self.serial_cfg.get("timeout", 1.0))

        while True:
            ser = None
            try:
                ser = serial.Serial(port=device, baudrate=baudrate, timeout=timeout)
                print(f"✅ 已連線到 RS485 裝置 {device} (baudrate={baudrate})，開始監聽 BMS 數據 (RS485)...")

                buffer = bytearray()

                while True:
                    data = ser.read(1024)
                    if not data:
                        # timeout 會回空 bytes，單純繼續
                        continue

                    if self.debug_raw_log:
                        hex_str = " ".join(f"{b:02X}" for b in data)
                        print(f"[DEBUG RAW RS485] ({len(data)} bytes): {hex_str}")

                    buffer.extend(data)

                    while True:
                        header_index = buffer.find(HEADER)
                        if header_index == -1:
                            if len(buffer) > self.buffer_size:
                                buffer = buffer[-100:]
                            break

                        if len(buffer) < header_index + 6:
                            break

                        pkt_type = buffer[header_index + 4]
                        packet_len = PACKET_LEN_02 if pkt_type == 0x02 else PACKET_LEN_01

                        if len(buffer) >= header_index + packet_len:
                            packet = buffer[header_index:header_index + packet_len]
                            yield pkt_type, bytes(packet)
                            del buffer[:header_index + packet_len]
                        else:
                            break

            except Exception as e:
                print(f"❌ RS485 傳輸層異常: {e}，5 秒後重試...")
                time.sleep(5)
            finally:
                if ser:
                    try:
                        ser.close()
                    except Exception:
                        pass


def create_transport() -> BaseTransport:
    """
    根據 /data/config.yaml 的 app 開關，建立對應的 Transport。
    - app.use_modbus_gateway == true → TcpTransport
    - app.use_rs485_usb == true     → Rs485Transport
    - 兩個都 true 時，優先 TCP（你也可以反過來）
    """
    cfg = load_config()
    app_cfg = cfg.get("app", {})

    use_tcp = bool(app_cfg.get("use_modbus_gateway", True))
    use_rs485 = bool(app_cfg.get("use_rs485_usb", False))

    if use_tcp:
        print("🔧 Transport 模式：TCP Modbus Gateway")
        return TcpTransport(cfg)
    elif use_rs485:
        print("🔧 Transport 模式：RS485 to USB")
        return Rs485Transport(cfg)
    else:
        print("⚠️ 未啟用任何 transport（use_modbus_gateway / use_rs485_usb 都是 false），預設使用 TCP。")
        return TcpTransport(cfg)

