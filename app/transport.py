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

# 🟢 修正：Master 指令監控清單 (確保包含 ID 0x00 到 0x0F 的控制行為)
MASTER_LIST = [0x00, 0x01, 0x02, 0x03, 0x0F]

class BaseTransport(ABC):
    def __init__(self, cfg: dict):
        # 🟢 修正：精確對齊 main.py 生成的階層式配置
        self.app_cfg = cfg.get("app", {})
        self.serial_cfg = cfg.get("serial", {})
        self.tcp_cfg = cfg.get("tcp", {})
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        pass

    def _extract_packets(self, buffer: bytearray) -> Generator[Tuple[int, bytes], None, None]:
        """
        核心協議切片邏輯：利用「標頭競爭」同時識別 JK 廣播與 Modbus 指令。
        這是「指令導引機制」的數據源頭。
        """
        while True:
            jk_idx = buffer.find(HEADER_JK)
            mb_idx = -1
            
            # 搜尋 Modbus 寫入指令 (ID + 0x10)，作為 Slave ID 判斷的導引信號
            for mid in MASTER_LIST:
                idx = buffer.find(bytes([mid, 0x10]))
                if idx != -1 and (mb_idx == -1 or idx < mb_idx):
                    mb_idx = idx

            # 情況 A：優先發現 JK BMS 數據包
            if jk_idx != -1 and (mb_idx == -1 or jk_idx < mb_idx):
                if len(buffer) < jk_idx + 6: break
                p_type = buffer[jk_idx + 4]
                # JK BMS 標準長度：0x02(實體數據)為 308 bytes, 0x01(設定/ID)為 300 bytes
                p_len = 308 if p_type == 0x02 else 300
                if len(buffer) >= jk_idx + p_len:
                    yield p_type, bytes(buffer[jk_idx : jk_idx + p_len])
                    del buffer[:jk_idx + p_len]
                    continue
                else: break
            
            # 情況 B：發現 Master 發出的 Modbus 控制指令 (導引標記)
            elif mb_idx != -1:
                # Modbus 寫入指令固定為 11 bytes
                if len(buffer) >= mb_idx + 11:
                    yield 0x10, bytes(buffer[mb_idx : mb_idx + 11])
                    del buffer[:mb_idx + 11]
                    continue
                else: break
            
            # 情況 C：無效數據清理
            else:
                # 緩衝區防溢位：若累積超過 4KB 且無有效標頭，清理舊數據
                if len(buffer) > 4096:
                    del buffer[:len(buffer)-1024]
                break

class Rs485Transport(BaseTransport):
    """USB 串列傳輸模式：實作全功能監聽"""
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baud = int(self.serial_cfg.get("baudrate", 115200))
        
        while True:
            ser = None
            try:
                if serial is None:
                    logger.error("❌ Python 環境未安裝 pyserial 模組")
                    time.sleep(10)
                    continue

                ser = serial.Serial(port=device, baudrate=baud, timeout=1.0)
                logger.info(f"🔌 USB 連線成功: {device} (全功能指令導引監聽中)")
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
        
        if not host:
            logger.error("❌ TCP 模式未設定主機位址 (modbus_host)")
            time.sleep(10)
            return

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
    """工廠函式：根據 options.json 轉換後的 config.yaml 建立對應實體"""
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"❌ 找不到配置文件: {CONFIG_PATH}")
        return Rs485Transport({"app": {}, "serial": {}})

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"❌ 讀取設定檔失敗: {e}")
        return Rs485Transport({"app": {}, "serial": {}})
    
    app_cfg = cfg.get("app", {})
    # 🟢 修正：邏輯判定，優先權根據 UI 選項
    if app_cfg.get("use_rs485_usb"):
        return Rs485Transport(cfg)
    else:
        return TcpTransport(cfg)
