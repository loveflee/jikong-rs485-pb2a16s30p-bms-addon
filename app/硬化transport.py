# =============================================================================
# transport.py - V2.2.1 Production Final (Industrial Hardened)
# 模組名稱：數據傳輸層 (RS485/TCP)
# 修正亮點：
#   - [Fix] 修正緩衝區清理邏輯的縮排錯誤。
#   - [Fix] 強化雜訊防護：將原本的 .clear() 改為 del buffer[:512] 溫和截斷，保留部分數據鏈路。
#   - [Fix] 結構化驗證：導入 _is_valid_master_cmd 防禦 Modbus 特徵碼碰撞。
#   - [Opt] 資源回收：確保 Serial/Socket 在 Exception 發生時能準確關閉資源。
# =============================================================================

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

# Master 指令監控清單 (Modbus Slave ID 0-15)
MASTER_LIST = [bytes([i, 0x10]) for i in range(16)]

class BaseTransport(ABC):
    def __init__(self, cfg: dict):
        self.app_cfg = cfg.get("app", {})
        self.serial_cfg = cfg.get("serial", {})
        self.tcp_cfg = cfg.get("tcp", {})
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        pass

    def _is_valid_master_cmd(self, buffer: bytearray, idx: int) -> bool:
        """🟢 [硬化] 驗證 Modbus 0x10 封包結構是否合法，防止與 JK BMS 特徵碼產生碰撞"""
        if len(buffer) < idx + 11:
            return False

        # Byte 4~5: Register Count
        reg_count = (buffer[idx + 4] << 8) | buffer[idx + 5]
        # Byte 6: Byte Count
        byte_count = buffer[idx + 6]

        # 工業級判定條件：
        # 1. 讀取長度在合理範圍 (1~10 個 Register)
        # 2. 回傳 Byte 數必須是 Register 數量的 2 倍
        if not (1 <= reg_count <= 10):
            return False
        if byte_count != reg_count * 2:
            return False

        return True

    def _extract_packets(self, buffer: bytearray) -> Generator[Tuple[int, bytes], None, None]:
        """核心解析邏輯：從緩衝區切分出正確的 JK BMS 或 Modbus 封包"""
        while True:
            jk_idx = buffer.find(HEADER_JK)
            mb_idx = -1
            for mb_head in MASTER_LIST:
                idx = buffer.find(mb_head)
                if idx != -1 and (mb_idx == -1 or idx < mb_idx):
                    mb_idx = idx

            # 處理 JK BMS 封包 (0x55 0xAA...)
            if jk_idx != -1 and (mb_idx == -1 or jk_idx < mb_idx):
                if len(buffer) < jk_idx + 6: break
                p_type = buffer[jk_idx + 4]
                # 根據 JK 協定：0x02 為 308 bytes, 0x01 為 300 bytes
                p_len = 308 if p_type == 0x02 else 300
                if len(buffer) >= jk_idx + p_len:
                    yield p_type, bytes(buffer[jk_idx : jk_idx + p_len])
                    del buffer[:jk_idx + p_len]
                    continue
                else: break

            # 處理 Modbus Master 指令 (0x10)
            elif mb_idx != -1:
                if len(buffer) >= mb_idx + 11:
                    if self._is_valid_master_cmd(buffer, mb_idx):
                        yield 0x10, bytes(buffer[mb_idx : mb_idx + 11])
                        del buffer[:mb_idx + 11]
                    else:
                        # 假 Header 防禦：跳過 2 bytes 繼續搜尋，避免誤刪周圍數據
                        if self.debug_raw_log:
                            logger.debug(f"[防禦] 偵測到偽造 Modbus Header (idx:{mb_idx})，執行跳轉")
                        del buffer[:mb_idx + 2]
                    continue
                else: 
                    break

            # 🟢 [修正] 縮排與緩衝區截斷邏輯
            else:
                if len(buffer) > 1024:
                    logger.warning(f"⚠️ 偵測到 RS485 極端雜訊，執行溫和截斷 (現存: {len(buffer)} bytes)")
                    # 🚀 [V2.2.1] 不使用 .clear()，保留後半段數據以防殘留有效封包
                    del buffer[:512]
                break

class Rs485Transport(BaseTransport):
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baud = int(self.serial_cfg.get("baudrate", 115200))

        while True:
            ser = None
            try:
                if serial is None:
                    logger.error("❌ 未安裝 pyserial 模組")
                    time.sleep(10); continue

                ser = serial.Serial(port=device, baudrate=baud, timeout=1.0)
                logger.info(f"🔌 USB 連線成功: {device} ({baud}bps)")
                buffer = bytearray()
                while True:
                    data = ser.read(1024)
                    if not data: continue
                    if self.debug_raw_log:
                        logger.debug(f"[RAW] {data.hex().upper()}")
                    buffer.extend(data)
                    yield from self._extract_packets(buffer)
            except Exception as e:
                logger.error(f"❌ USB 傳輸錯誤: {e}"); time.sleep(5)
            finally:
                if ser: ser.close()

class TcpTransport(BaseTransport):
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        host = self.tcp_cfg.get("host")
        port = int(self.tcp_cfg.get("port", 502))
        if not host:
            logger.error("❌ TCP 模式未設定主機地址"); time.sleep(10); return

        while True:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10.0)
                sock.connect((host, port))
                logger.info(f"🌐 TCP 網關連線成功: {host}:{port}")
                buffer = bytearray()
                while True:
                    data = sock.recv(4096)
                    if not data: break
                    buffer.extend(data)
                    yield from self._extract_packets(buffer)
            except Exception as e:
                logger.error(f"❌ TCP 連線錯誤: {e}"); time.sleep(5)
            finally:
                if sock: sock.close()

def create_transport() -> BaseTransport:
    """工廠函式：根據配置檔建立對應的傳輸實體"""
    if not os.path.exists(CONFIG_PATH):
        return Rs485Transport({"app": {}, "serial": {}})
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    if cfg.get("app", {}).get("use_rs485_usb"):
        return Rs485Transport(cfg)
    return TcpTransport(cfg)
