import socket
import time
import sys
import os
import yaml
import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Generator

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger("jk_bms_transport")

CONFIG_PATH = "/data/config.yaml"
HEADER_JK = b"\x55\xAA\xEB\x90"
# 定義 Master 可能發送的 ID (根據你的 Log: 0F, 01, 02, 03)
MASTER_IDS = [0x0F, 0x01, 0x02, 0x03]

class BaseTransport(ABC):
    def __init__(self, cfg: dict):
        self.tcp_cfg = cfg.get("tcp", {})
        self.serial_cfg = cfg.get("serial", {})
        self.app_cfg = cfg.get("app", {})
        self.buffer_size = int(self.tcp_cfg.get("buffer_size", 4096))
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        pass

class Rs485Transport(BaseTransport):
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        if serial is None:
            logger.error("❌ 未安裝 pyserial")
            return

        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baudrate = int(self.serial_cfg.get("baudrate", 115200))
        timeout = float(self.serial_cfg.get("timeout", 1.0))

        while True:
            ser = None
            try:
                ser = serial.Serial(port=device, baudrate=baudrate, timeout=timeout)
                logger.info("🔌 連線成功: %s，雙協議監聽中 (JK + Modbus Master)...", device)
                buffer = bytearray()

                while True:
                    data = ser.read(1024)
                    if not data: continue
                    
                    if self.debug_raw_log:
                        logger.debug("[DEBUG RAW RS485] (%d bytes): %s", len(data), data.hex(" ").upper())
                    
                    buffer.extend(data)

                    while True:
                        # 1. 尋找 JK 標頭
                        jk_idx = buffer.find(HEADER_JK)
                        
                        # 2. 尋找 Master Modbus 標頭 (ID + Function 0x10)
                        mb_idx = -1
                        for mid in MASTER_IDS:
                            idx = buffer.find(bytes([mid, 0x10]))
                            if idx != -1 and (mb_idx == -1 or idx < mb_idx):
                                mb_idx = idx

                        # 判斷先處理哪一個
                        if jk_idx != -1 and (mb_idx == -1 or jk_idx < mb_idx):
                            if len(buffer) < jk_idx + 6: break
                            pkt_type = buffer[jk_idx + 4]
                            pkt_len = 308 if pkt_type == 0x02 else 300
                            if len(buffer) >= jk_idx + pkt_len:
                                yield pkt_type, bytes(buffer[jk_idx : jk_idx + pkt_len])
                                del buffer[:jk_idx + pkt_len]
                                continue
                            else: break

                        elif mb_idx != -1:
                            # 處理 Master 寫入指令 (通常為 11 bytes)
                            mb_len = 11
                            if len(buffer) >= mb_idx + mb_len:
                                yield 0x10, bytes(buffer[mb_idx : mb_idx + mb_len])
                                del buffer[:mb_idx + mb_len]
                                continue
                            else: break
                        
                        else:
                            # 沒標頭，清除無用數據防止 buffer 溢位
                            if len(buffer) > self.buffer_size:
                                buffer = buffer[-500:]
                            break

            except Exception as e:
                logger.error("❌ 傳輸層異常: %s", e)
                time.sleep(5)
            finally:
                if ser: ser.close()

# ... create_transport 保持不變 ...
def create_transport() -> BaseTransport:
    cfg = load_config()
    app_cfg = cfg.get("app", {})
    if bool(app_cfg.get("use_rs485_usb", False)):
        return Rs485Transport(cfg)
    return TcpTransport(cfg)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

class TcpTransport(BaseTransport):
    # TCP 邏輯可比照上述雙協議邏輯，若暫時不用可維持原樣
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        pass
