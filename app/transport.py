# transport.py
import socket
import time
import sys
import os
import yaml
import logging
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

# 設置 logger
logger = logging.getLogger("jk_bms_transport")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

def load_config():
    """從 /data/config.yaml 讀取整體設定。"""
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"❌ 找不到設定檔 {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class BaseTransport(ABC):
    def __init__(self, cfg: dict):
        self.tcp_cfg = cfg.get("tcp", {})
        self.serial_cfg = cfg.get("serial", {})
        self.app_cfg = cfg.get("app", {})
        self.buffer_size = int(self.tcp_cfg.get("buffer_size", 4096))
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        """連線並持續產生封包 (packet_type, packet_bytes)"""
        ...


class TcpTransport(BaseTransport):
    """
    使用 Modbus Gateway (TCP) 的傳輸方式
    具備完整的斷線重連機制
    """

    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        host = self.tcp_cfg.get("host", "127.0.0.1")
        port = int(self.tcp_cfg.get("port", 502))
        timeout = int(self.tcp_cfg.get("timeout", 10))

        logger.info(f"🔧 TCP Transport 初始化: {host}:{port}, timeout={timeout}")

        while True:
            sock = None
            try:
                # 建立連線
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                logger.info(f"✅ [TCP] 已連線到 {host}:{port}，開始監聽 BMS 數據...")

                buffer = bytearray()
                
                # 內層迴圈：持續讀取數據
                while True:
                    try:
                        chunk = sock.recv(1024)
                    except socket.timeout:
                        # 這是正常的 timeout，表示暫時沒資料，檢查一下連線是否還健在
                        # 在 TCP 中，timeout 不代表斷線，我們可以繼續 loop
                        continue
                    except (ConnectionResetError, BrokenPipeError) as e:
                        logger.warning(f"⚠️ [TCP] 連線被重置或中斷: {e}")
                        break
                    except Exception as e:
                        logger.error(f"❌ [TCP] 讀取發生未預期錯誤: {e}")
                        break

                    if not chunk:
                        logger.warning("⚠️ [TCP] 伺服器端已關閉連線 (Received empty bytes)")
                        break

                    # Debug Raw
                    if self.debug_raw_log:
                        hex_str = " ".join(f"{b:02X}" for b in chunk)
                        logger.debug(f"[RAW TCP] {hex_str}")

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
                            
                            # Yield packet out
                            yield pkt_type, bytes(packet)

                            del buffer[: header_index + packet_len]
                        else:
                            break
            
            except socket.timeout:
                logger.warning(f"⚠️ [TCP] 連線逾時 ({host}:{port})，正在重試...")
            except ConnectionRefusedError:
                logger.error(f"❌ [TCP] 連線被拒 ({host}:{port})，Modbus Gateway 可能未啟動。")
            except Exception as e:
                logger.error(f"❌ [TCP] 傳輸層異常: {e}")
            
            finally:
                # 確保 socket 關閉
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                sock = None
            
            # 斷線後的冷卻時間
            logger.info("⏳ [TCP] 5 秒後嘗試重新連線...")
            time.sleep(5)


class Rs485Transport(BaseTransport):
    """
    使用 RS485 to USB 的傳輸方式
    """

    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        if serial is None:
            logger.error("❌ 未安裝 pyserial，無法使用 RS485 模式")
            return

        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baudrate = int(self.serial_cfg.get("baudrate", 115200))
        timeout = float(self.serial_cfg.get("timeout", 1.0))

        logger.info(f"🔧 RS485 Transport 初始化: {device}, baud={baudrate}")

        while True:
            ser = None
            try:
                ser = serial.Serial(port=device, baudrate=baudrate, timeout=timeout)
                logger.info(f"✅ [RS485] 已開啟 Serial Port {device}")

                buffer = bytearray()

                while True:
                    try:
                        # Serial read 會依照 timeout 返回，若沒資料就是 b''
                        data = ser.read(1024)
                    except serial.SerialException as e:
                        logger.error(f"❌ [RS485] 讀取錯誤 (可能裝置拔除): {e}")
                        break
                    except Exception as e:
                        logger.error(f"❌ [RS485] 未預期錯誤: {e}")
                        break

                    if not data:
                        # Serial timeout 是正常的，不像 TCP 需要斷線重連
                        continue

                    if self.debug_raw_log:
                        hex_str = " ".join(f"{b:02X}" for b in data)
                        logger.debug(f"[RAW RS485] {hex_str}")

                    buffer.extend(data)

                    # 解析 buffer (邏輯同 TCP)
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
                logger.error(f"❌ [RS485] 開啟或連線異常: {e}")
            
            finally:
                if ser:
                    try:
                        ser.close()
                    except Exception:
                        pass
                ser = None

            logger.info("⏳ [RS485] 5 秒後嘗試重新開啟 Serial Port...")
            time.sleep(5)


def create_transport() -> BaseTransport:
    cfg = load_config()
    app_cfg = cfg.get("app", {})

    use_tcp = bool(app_cfg.get("use_modbus_gateway", True))
    use_rs485 = bool(app_cfg.get("use_rs485_usb", False))

    if use_tcp:
        return TcpTransport(cfg)
    elif use_rs485:
        return Rs485Transport(cfg)
    else:
        logger.warning("⚠️ 未啟用任何 transport，預設使用 TCP。")
        return TcpTransport(cfg)
