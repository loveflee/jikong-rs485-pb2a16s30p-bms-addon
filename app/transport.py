#transport.py
import socket time sys os yaml 
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
    通訊層抽象基底類別
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
    修正：加入自動重連機制
    """

    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        host = self.tcp_cfg.get("host", "127.0.0.1")
        port = int(self.tcp_cfg.get("port", 502))
        timeout = int(self.tcp_cfg.get("timeout", 10))

        # 外層無窮迴圈：確保斷線後可以重新連線
        while True:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                print(f"✅ 已連線到 {host}:{port} (TCP)，開始監聽...")

                buffer = bytearray()

                # 內層迴圈：資料讀取
                while True:
                    try:
                        chunk = sock.recv(1024)
                    except socket.timeout:
                        # timeout 不代表斷線，繼續嘗試讀取
                        continue
                    except OSError:
                        # 連線異常 (Connection reset 等)
                        print("⚠️ TCP 連線中斷，準備重連...")
                        break

                    if not chunk:
                        print("⚠️ 伺服器端已斷開連線 (TCP)")
                        break

                    # 除錯模式
                    if self.debug_raw_log:
                        hex_str = " ".join(f"{b:02X}" for b in chunk)
                        print(f"[DEBUG RAW] {hex_str}")

                    buffer.extend(chunk)

                    # 解析 buffer
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
                            packet = buffer[header_index : header_index + packet_len]
                            
                            yield pkt_type, bytes(packet)

                            del buffer[: header_index + packet_len]
                        else:
                            break

            except Exception as e:
                print(f"❌ TCP 連線失敗或異常: {e}，5 秒後重試...")
            finally:
                if sock:
                    try:
                        sock.close()
                    except:
                        pass
                sock = None
            
            # 斷線後的冷卻時間
            time.sleep(5)


class Rs485Transport(BaseTransport):
    """
    使用 RS485 to USB 的傳輸方式
    修正：加入自動重連機制 (防止 USB 拔除或錯誤時 Crash)
    """

    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        if serial is None:
            print("❌ 未安裝 pyserial，無法使用 RS485 模式")
            return

        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baudrate = int(self.serial_cfg.get("baudrate", 115200))
        timeout = float(self.serial_cfg.get("timeout", 1.0))

        # 外層無窮迴圈：確保重開
        while True:
            ser = None
            try:
                ser = serial.Serial(port=device, baudrate=baudrate, timeout=timeout)
                print(f"✅ 已連線到 RS485 裝置 {device}")

                buffer = bytearray()

                while True:
                    try:
                        data = ser.read(1024)
                    except Exception as e:
                        print(f"⚠️ RS485 讀取錯誤: {e}")
                        break

                    if not data:
                        continue

                    if self.debug_raw_log:
                        hex_str = " ".join(f"{b:02X}" for b in data)
                        print(f"[DEBUG RAW RS485] {hex_str}")

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
                            packet = buffer[header_index : header_index + packet_len]
                            yield pkt_type, bytes(packet)
                            del buffer[: header_index + packet_len]
                        else:
                            break
            
            except Exception as e:
                print(f"❌ RS485 裝置異常: {e}，5 秒後重試...")
            finally:
                if ser:
                    try:
                        ser.close()
                    except:
                        pass
                ser = None
            
            time.sleep(5)


def create_transport() -> BaseTransport:
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
        print("⚠️ 未啟用任何 transport，預設使用 TCP。")
        return TcpTransport(cfg)
