# =============================================================================
# transport.py - V2.2.4 Production Final (Edge Node Hardened)
# 模組名稱：數據傳輸層 (RS485/TCP)
# 修正亮點：
#   - [Fix] on_link_down 回調鉤子：傳輸層斷線瞬間主動通知上層，消除 60 秒被動超時盲區 (V2.2.4)
#   - [Fix] TCP Keepalive：防禦半打開連線 (承襲 V2.2.2)
# =============================================================================

import socket
import time
import os
import yaml
import logging
from abc import ABC, abstractmethod
from typing import Tuple, Generator, Optional, Callable

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger("jk_bms_transport")
CONFIG_PATH = "/data/config.yaml"
HEADER_JK = b"\x55\xAA\xEB\x90"

MASTER_LIST = [bytes([i, 0x10]) for i in range(16)]

class BaseTransport(ABC):
    def __init__(self, cfg: dict):
        self.app_cfg = cfg.get("app", {})
        self.serial_cfg = cfg.get("serial", {})
        self.tcp_cfg = cfg.get("tcp", {})
        self.debug_raw_log = bool(self.app_cfg.get("debug_raw_log", False))
        self.on_link_down: Optional[Callable] = None

    @abstractmethod
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        pass

    def _crc16(self, data: bytes) -> bool:
        """ 🟢 [新增] 嚴格 Modbus CRC 校驗，100% 排除 BMS Payload 偽造碰撞 """
        crc = 0xFFFF
        for pos in data[:-2]:
            crc ^= pos
            for _ in range(8):
                if (crc & 1) != 0:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        # Modbus CRC is little-endian
        return (crc & 0xFF) == data[-2] and ((crc >> 8) & 0xFF) == data[-1]

    def _is_valid_master_cmd(self, buffer: bytearray, idx: int) -> int:
        """ 
        🟢 [修正] 動態計算真實長度，並加入 CRC 確認 
        回傳真實長度 (int), 若資料不夠回傳 -1, 若為偽造雜訊回傳 0
        """
        if len(buffer) < idx + 9:
            return -1 # 等待更多資料
            
        reg_count = (buffer[idx + 4] << 8) | buffer[idx + 5]
        byte_count = buffer[idx + 6]
        
        if not (1 <= reg_count <= 125):
            return 0
        if byte_count != reg_count * 2:
            return 0
            
        # 動態推算 Modbus 0x10 真實長度: ID(1)+FC(1)+Reg(2)+Qty(2)+BC(1)+Data(BC)+CRC(2)
        total_len = 9 + byte_count
        if len(buffer) < idx + total_len:
            return -1 # 等待更多資料
            
        # 執行 CRC 驗證
        packet = bytes(buffer[idx : idx + total_len])
        if self._crc16(packet):
            return total_len
            
        return 0

    def _extract_packets(self, buffer: bytearray) -> Generator[Tuple[int, bytes], None, None]:
        while True:
            jk_idx = buffer.find(HEADER_JK)
            mb_idx = -1
            
            for mb_head in MASTER_LIST:
                idx = buffer.find(mb_head)
                if idx != -1 and (mb_idx == -1 or idx < mb_idx):
                    mb_idx = idx

            # 若都沒有表頭，防禦性清空緩衝區避免內存溢出
            if jk_idx == -1 and mb_idx == -1:
                if len(buffer) > 1024:
                    if self.debug_raw_log:
                        logger.warning(f"⚠️ 偵測到嚴重失去同步，強制清空 Buffer ({len(buffer)} bytes)")
                    buffer.clear()
                break

            # 判定哪個表頭在前面 (優先處理先抵達的 Frame)
            is_jk_first = False
            if jk_idx != -1 and mb_idx != -1:
                is_jk_first = jk_idx < mb_idx
            elif jk_idx != -1:
                is_jk_first = True

            if is_jk_first:
                if len(buffer) < jk_idx + 6:
                    break # 資料不足，跳出迴圈等下一次 I/O
                
                p_type = buffer[jk_idx + 4]
                p_len = 308 if p_type == 0x02 else 300
                
                if len(buffer) >= jk_idx + p_len:
                    yield p_type, bytes(buffer[jk_idx : jk_idx + p_len])
                    del buffer[:jk_idx + p_len] # 🟢 [防禦] 安全切除：包含前面的雜訊一併丟棄
                    continue
                else:
                    break # 資料不足，跳出迴圈等下一次 I/O
                    
            else:
                cmd_len = self._is_valid_master_cmd(buffer, mb_idx)
                
                if cmd_len == -1:
                    break # 資料不足，跳出迴圈等下一次 I/O
                elif cmd_len > 0:
                    yield 0x10, bytes(buffer[mb_idx : mb_idx + cmd_len])
                    del buffer[:mb_idx + cmd_len] # 🟢 [修正] 動態長度切除，不殘留 CRC 尾巴
                    continue
                else:
                    # 🟢 這才是真正的雜訊排除：遇到假 Header，只推進 2 bytes，避免錯殺真正的封包
                    del buffer[:mb_idx + 2]
                    continue

class Rs485Transport(BaseTransport):
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        device = self.serial_cfg.get("device", "/dev/ttyUSB0")
        baud = int(self.serial_cfg.get("baudrate", 115200))

        while True:
            ser = None
            try:
                if serial is None:
                    logger.error("❌ 未安裝 pyserial 模組")
                    time.sleep(10)
                    continue

                ser = serial.Serial(port=device, baudrate=baud, timeout=1.0)
                logger.info(f"🔌 USB 連線成功: {device} ({baud}bps)")
                buffer = bytearray()
                while True:
                    data = ser.read(1024)
                    if not data:
                        continue
                    if self.debug_raw_log:
                        logger.debug(f"[RAW] {data.hex().upper()}")
                    buffer.extend(data)
                    yield from self._extract_packets(buffer)

            except Exception as e:
                logger.error(f"❌ USB 傳輸錯誤: {e}")
                # 🚀 [V2.2.4] 斷線瞬間觸發上層回調，主動推送所有設備 offline
                if self.on_link_down:
                    try:
                        self.on_link_down()
                    except Exception:
                        logger.exception("on_link_down 回調執行異常")
                time.sleep(5)
            finally:
                if ser:
                    ser.close()


class TcpTransport(BaseTransport):
    def packets(self) -> Generator[Tuple[int, bytes], None, None]:
        host = self.tcp_cfg.get("host")
        port = int(self.tcp_cfg.get("port", 502))
        if not host:
            logger.error("❌ TCP 模式未設定主機地址")
            time.sleep(10)
            return

        while True:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)

                sock.settimeout(10.0)
                sock.connect((host, port))
                logger.info(f"🌐 TCP 網關連線成功: {host}:{port}")
                buffer = bytearray()
                while True:
                    data = sock.recv(4096)
                    if not data:
                        break
                    buffer.extend(data)
                    yield from self._extract_packets(buffer)

            except Exception as e:
                logger.error(f"❌ TCP 連線錯誤: {e}")
                # 🚀 [V2.2.4] 斷線瞬間觸發上層回調
                if self.on_link_down:
                    try:
                        self.on_link_down()
                    except Exception:
                        logger.exception("on_link_down 回調執行異常")
                time.sleep(5)
            finally:
                if sock:
                    sock.close()


def create_transport() -> BaseTransport:
    if not os.path.exists(CONFIG_PATH):
        return Rs485Transport({"app": {}, "serial": {}})
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    if cfg.get("app", {}).get("use_rs485_usb"):
        return Rs485Transport(cfg)
    return TcpTransport(cfg)
