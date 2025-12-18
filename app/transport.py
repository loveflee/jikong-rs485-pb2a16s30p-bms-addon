import socket
import time
import os
import yaml
import logging
from abc import ABC, abstractmethod
from typing import Tuple, Generator

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger("jk_bms_transport")
CONFIG_PATH = "/data/config.yaml"
HEADER_JK = b"\x55\xAA\xEB\x90"
# 根據你的 Log 定義 Master 指令的 ID 與功能碼 0x10 (Write Multi Registers)
MASTER_LIST = [0x0F, 0x01, 0x02, 0x03]

class BaseTransport(ABC):
    def __init__(self, cfg: dict):
        self.serial_cfg = cfg.get("serial", {})
        self.app_cfg = cfg.get("app", {})
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        pass

class Rs485Transport(BaseTransport):
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baud = int(self.serial_cfg.get("baudrate", 115200))
        timeout = float(self.serial_cfg.get("timeout", 1.0))

        while True:
            ser = None
            try:
                ser = serial.Serial(port=device, baudrate=baud, timeout=timeout)
                logger.info(f"🔌 連線成功: {device} (監聽 Master 指令 & JK BMS)")
                buffer = bytearray()

                while True:
                    data = ser.read(1024)
                    if not data: continue
                    if self.debug_raw_log:
                        logger.debug(f"[DEBUG RX] {data.hex(' ').upper()}")
                    
                    buffer.extend(data)

                    while True:
                        # 搜尋 JK 標頭與 Master 標頭
                        jk_idx = buffer.find(HEADER_JK)
                        mb_idx = -1
                        for mid in MASTER_LIST:
                            idx = buffer.find(bytes([mid, 0x10]))
                            if idx != -1 and (mb_idx == -1 or idx < mb_idx):
                                mb_idx = idx

                        if jk_idx != -1 and (mb_idx == -1 or jk_idx < mb_idx):
                            # 處理 JK 封包
                            if len(buffer) < jk_idx + 6: break
                            p_type = buffer[jk_idx + 4]
                            p_len = 308 if p_type == 0x02 else 300
                            if len(buffer) >= jk_idx + p_len:
                                yield p_type, bytes(buffer[jk_idx : jk_idx + p_len])
                                del buffer[:jk_idx + p_len]
                                continue
                            else: break
                        elif mb_idx != -1:
                            # 處理 Master Modbus 指令 (固定 11 bytes)
                            if len(buffer) >= mb_idx + 11:
                                yield 0x10, bytes(buffer[mb_idx : mb_idx + 11])
                                del buffer[:mb_idx + 11]
                                continue
                            else: break
                        else:
                            if len(buffer) > 2048: buffer = buffer[-500:]
                            break
            except Exception as e:
                time.sleep(5)
            finally:
                if ser: ser.close()

def create_transport():
    with open(CONFIG_PATH, 'r') as f:
        cfg = yaml.safe_load(f)
    return Rs485Transport(cfg)
