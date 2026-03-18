# =============================================================================
# transport.py - V2.2.1 Production Final (Industrial Hardened - Field Optimized)
# 模組名稱：數據傳輸層 (RS485/TCP)
# 修正亮點：
#   - [Opt] 聽從現場實戰反饋：將溫和截斷改回核彈級清除 (buffer.clear())，以實現最快的 RS485 重新同步。
#   - [Fix] 結構化驗證：保留 _is_valid_master_cmd 防禦 Modbus 特徵碼碰撞。
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
        if len(buffer) < idx + 11:
            return False
        reg_count = (buffer[idx + 4] << 8) | buffer[idx + 5]
        byte_count = buffer[idx + 6]
        if not (1 <= reg_count <= 10):
            return False
        if byte_count != reg_count * 2:
            return False
        return True

    def _extract_packets(self, buffer: bytearray) -> Generator[Tuple[int, bytes], None, None]:
        while True:
            jk_idx = buffer.find(HEADER_JK)
            mb_idx = -1
            for mb_head in MASTER_LIST:
                idx = buffer.find(mb_head)
                if idx != -1 and (mb_idx == -1 or idx < mb_idx):
                    mb_idx = idx

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
                if len(buffer) >= mb_idx + 11:
                    if self._is_valid_master_cmd(buffer, mb_idx):
                        yield 0x10, bytes(buffer[mb_idx : mb_idx + 11])
                        del buffer[:mb_idx + 11]
                    else:
                        if self.debug_raw_log:
                            logger.debug(f"[防禦] 偵測到偽造 Modbus Header (idx:{mb_idx})，跳過")
                        del buffer[:mb_idx + 2]
                    continue
                else: 
                    break
            else:
                # 🚀 [現場實戰優化] 放棄溫和截斷，直接清空 Buffer 強制重新同步 (Drop & Resync)
                if len(buffer) > 1024:
                    logger.warning(f"⚠️ 偵測到 RS485 嚴重失去同步，強制清空 Buffer ({len(buffer)} bytes)")
                    buffer.clear()
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
    if not os.path.exists(CONFIG_PATH):
        return Rs485Transport({"app": {}, "serial": {}})
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    if cfg.get("app", {}).get("use_rs485_usb"):
        return Rs485Transport(cfg)
    return TcpTransport(cfg)
