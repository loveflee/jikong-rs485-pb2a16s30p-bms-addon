import socket
import time
import os
import yaml
import logging
from abc import ABC, abstractmethod
from typing import Tuple, Generator, Optional

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger("jk_bms_transport")
CONFIG_PATH = "/data/config.yaml"
HEADER_JK = b"\x55\xAA\xEB\x90"
# Master 可能使用的 ID 範圍 (包含 0x00)
MASTER_LIST = [0x0F, 0x01, 0x02, 0x03, 0x00]

class BaseTransport(ABC):
    def __init__(self, cfg: dict):
        # 🟢 修正：對齊新版階層式配置路徑
        self.app_cfg = cfg.get("app", {})
        self.serial_cfg = cfg.get("serial", {})
        self.tcp_cfg = cfg.get("tcp", {})
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        pass

    def _extract_packets(self, buffer: bytearray) -> Generator[Tuple[int, bytes], None, None]:
        """共通的協議切片邏輯：支援 JK BMS 廣播與 Modbus Master 指令"""
        while True:
            jk_idx = buffer.find(HEADER_JK)
            mb_idx = -1
            # 搜尋 Modbus 寫入指令 (ID + 0x10)
            for mid in MASTER_LIST:
                idx = buffer.find(bytes([mid, 0x10]))
                if idx != -1 and (mb_idx == -1 or idx < mb_idx):
                    mb_idx = idx

            # 判斷優先處理哪一種封包
            if jk_idx != -1 and (mb_idx == -1 or jk_idx < mb_idx):
                if len(buffer) < jk_idx + 6: break
                p_type = buffer[jk_idx + 4]
                p_len = 308 if p_type == 0x02 else 300
                if len(buffer) >= jk_idx + p_len:
                    yield p_type, bytes(buffer[jk_idx : jk_idx + p_len])
                    del buffer[:jk_idx + p_len]
                    continue
                else: break
            elif mb_idx != -1:
                # Master Modbus 指令通常為 11 bytes
                if len(buffer) >= mb_idx + 11:
                    yield 0x10, bytes(buffer[mb_idx : mb_idx + 11])
                    del buffer[:mb_idx + 11]
                    continue
                else: break
            else:
                # 緩衝區防溢位處理
                if len(buffer) > 4096:
                    del buffer[:len(buffer)-1024]
                break

class Rs485Transport(BaseTransport):
    """USB 串列傳輸模式"""
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baud = int(self.serial_cfg.get("baudrate", 115200))
        
        while True:
            ser = None
            try:
                ser = serial.Serial(port=device, baudrate=baud, timeout=1.0)
                logger.info(f"🔌 USB 連線成功: {device} (雙協議監聽中)")
                buffer = bytearray()
                while True:
                    data = ser.read(1024)
                    if not data: continue
                    if self.debug_raw_log:
                        logger.debug(f"[RAW RX] {data.hex(' ').upper()}")
                    buffer.extend(data)
                    yield from self._extract_packets(buffer)
            except Exception as e:
                logger.error(f"❌ USB 異常: {e}")
                time.sleep(5)
            finally:
                if ser: ser.close()

class TcpTransport(BaseTransport):
    """Modbus Gateway TCP 傳輸模式"""
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        host = self.tcp_cfg.get("host")
        port = int(self.tcp_cfg.get("port", 502))
        
        while True:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10.0)
                sock.connect((host, port))
                logger.info(f"🌐 TCP 連線成功: {host}:{port}")
                buffer = bytearray()
                while True:
                    data = sock.recv(4096)
                    if not data: break
                    if self.debug_raw_log:
                        logger.debug(f"[RAW RX] {data.hex(' ').upper()}")
                    buffer.extend(data)
                    yield from self._extract_packets(buffer)
            except Exception as e:
                logger.error(f"❌ TCP 異常: {host}:{port} - {e}")
                time.sleep(5)
            finally:
                if sock: sock.close()

def create_transport() -> BaseTransport:
    """工廠函式：根據新版 UI 選取的模式建立實體"""
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"找不到配置文件: {CONFIG_PATH}")
        # 後備方案
        return Rs485Transport({"app": {}, "serial": {}})

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    
    app_cfg = cfg.get("app", {})
    if app_cfg.get("use_rs485_usb"):
        return Rs485Transport(cfg)
    else:
        return TcpTransport(cfg)
