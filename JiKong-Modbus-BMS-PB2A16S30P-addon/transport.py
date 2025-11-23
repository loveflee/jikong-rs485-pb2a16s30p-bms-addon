# transport.py
# 專門負責「收封包」：不分析、不發 MQTT只輸出 (packet_type, raw_packet_bytes)
# 封包格式：
#   Header: 0x55 0xAA 0xEB 0x90
#   第 5 byte: packet_type (0x01 / 0x02)
#   第 6 byte: 暫不使用
#   0x01: 固定長度 300 bytes
#   0x02: 固定長度 308 bytes
# 封包切割邏輯跟你原本 main.py 裡的 buffer 處理幾乎一樣，只是搬過來獨立成函數。

import socket
import time
from typing import Generator, Tuple, Optional

try:
    import serial  # RS485 to USB 用
except ImportError:
    serial = None  # 如果沒有用到 serial，可以不安裝 pyserial


HEADER = b"\x55\xAA\xEB\x90"


class BaseTransport:
    """通訊層基底類別，定義共用介面。"""

    def open(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def iter_packets(self) -> Generator[Tuple[int, bytes], None, None]:
        """
        連續產生 (packet_type, raw_packet_bytes)。
        子類別負責實作底層 recv。
        """
        raise NotImplementedError


class ModbusGatewayTransport(BaseTransport):
    """透過 TCP Modbus Gateway 收資料。"""

    def __init__(self, host: str, port: int, timeout: float, buffer_size: int):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.buffer_size = buffer_size
        self.sock: Optional[socket.socket] = None

    def open(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))
        print(f"✅ Modbus Gateway 已連線: {self.host}:{self.port}")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _recv_chunk(self, size: int = 1024) -> bytes:
        assert self.sock is not None
        return self.sock.recv(size)

    def iter_packets(self) -> Generator[Tuple[int, bytes], None, None]:
        """
        以你原本的邏輯：
        - 在 buffer 中搜尋 HEADER
        - 根據 pkt_type => 決定 packet_len（0x02: 308, others: 300）
        - 夠長就切出一包並 yield
        """
        if self.sock is None:
            self.open()

        buffer = bytearray()

        while True:
            try:
                chunk = self._recv_chunk(1024)
                if not chunk:
                    print("⚠️ Modbus Gateway 端已斷線（recv=0）")
                    break

                buffer.extend(chunk)

                while True:
                    header_index = buffer.find(HEADER)
                    if header_index == -1:
                        # 如果 buffer 太大，就只保留最後 100 bytes 防止無限成長
                        if len(buffer) > self.buffer_size:
                            buffer = buffer[-100:]
                        break

                    # 至少要有 header + type + len byte
                    if len(buffer) < header_index + 6:
                        break

                    pkt_type = buffer[header_index + 4]
                    packet_len = 308 if pkt_type == 0x02 else 300

                    if len(buffer) >= header_index + packet_len:
                        packet = buffer[header_index: header_index + packet_len]
                        # 從 buffer 移除已處理部分
                        del buffer[:header_index + packet_len]
                        yield pkt_type, bytes(packet)
                    else:
                        # 資料還不夠一包，等待下一輪 recv
                        break

            except socket.timeout:
                # 交給上層去處理 idle 狀態即可（這裡只讓迴圈繼續）
                continue
            except Exception as e:
                print(f"❌ Modbus Gateway 收包異常: {e}")
                break


class Rs485UsbTransport(BaseTransport):
    """直接從 RS485 to USB（Serial）接收資料。"""

    def __init__(self, device: str, baudrate: int, timeout: float, buffer_size: int):
        self.device = device
        self.baudrate = baudrate
        self.timeout = timeout
        self.buffer_size = buffer_size
        self.ser: Optional["serial.Serial"] = None  # type: ignore[name-defined]

    def open(self):
        if serial is None:
            raise RuntimeError("pyserial 尚未安裝，無法使用 RS485 USB 模式。")
        self.ser = serial.Serial(
            port=self.device,
            baudrate=self.baudrate,
            timeout=self.timeout,
        )
        print(f"✅ RS485 USB 已開啟: {self.device} @ {self.baudrate}bps")

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _recv_chunk(self, size: int = 1024) -> bytes:
        assert self.ser is not None
        return self.ser.read(size)

    def iter_packets(self) -> Generator[Tuple[int, bytes], None, None]:
        """
        跟 ModbusGatewayTransport 幾乎一樣，只是底層改成 Serial read。
        """
        if self.ser is None:
            self.open()

        buffer = bytearray()

        while True:
            try:
                chunk = self._recv_chunk(1024)
                if not chunk:
                    # Serial read timeout 返回空 bytes，代表暫時沒資料
                    continue

                buffer.extend(chunk)

                while True:
                    header_index = buffer.find(HEADER)
                    if header_index == -1:
                        if len(buffer) > self.buffer_size:
                            buffer = buffer[-100:]
                        break

                    if len(buffer) < header_index + 6:
                        break

                    pkt_type = buffer[header_index + 4]
                    packet_len = 308 if pkt_type == 0x02 else 300

                    if len(buffer) >= header_index + packet_len:
                        packet = buffer[header_index: header_index + packet_len]
                        del buffer[:header_index + packet_len]
                        yield pkt_type, bytes(packet)
                    else:
                        break

            except Exception as e:
                print(f"❌ RS485 USB 收包異常: {e}")
                break


def create_transport(tcp_cfg: dict, serial_cfg: dict, app_cfg: dict) -> BaseTransport:
    """
    根據 app_cfg 選擇要使用哪一種 transport。
    """
    use_modbus = bool(app_cfg.get("use_modbus_gateway", True))
    use_usb = bool(app_cfg.get("use_rs485_usb", False))

    if use_modbus and use_usb:
        print("⚠️ 同時啟用 modbus_gateway 與 rs485_usb，預設優先使用 modbus_gateway。")

    if use_modbus:
        return ModbusGatewayTransport(
            host=tcp_cfg.get("host", "192.168.106.13"),
            port=int(tcp_cfg.get("port", 502)),
            timeout=float(tcp_cfg.get("timeout", 10)),
            buffer_size=int(tcp_cfg.get("buffer_size", 4096)),
        )

    # 否則就使用 RS485 USB
    return Rs485UsbTransport(
        device=serial_cfg.get("device", "/dev/ttyUSB0"),
        baudrate=int(serial_cfg.get("baudrate", 9600)),
        timeout=float(serial_cfg.get("timeout", 1.0)),
        buffer_size=int(tcp_cfg.get("buffer_size", 4096)),
    )
