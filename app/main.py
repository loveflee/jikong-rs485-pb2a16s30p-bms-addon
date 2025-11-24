#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import yaml
import paho.mqtt.client as mqtt
import serial
import serial_asyncio

# =========================
# Logging 初始化
# =========================

def setup_logging(debug_raw: bool) -> None:
    """
    設定 logging 格式與等級。

    debug_raw = True 時，輸出 DEBUG（包含 raw hexdump）
    否則只顯示 INFO 以上（較乾淨）。
    """
    level = logging.DEBUG if debug_raw else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

logger = logging.getLogger("jk_bms")


# =========================
# Config 載入
# =========================

def load_config(path: str = "/data/config.yaml") -> Dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        logger.critical("❌ 找不到設定檔: %s", path)
        sys.exit(1)
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg or {}


# =========================
# MQTT 客戶端封裝
# =========================

class MqttClient:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.broker = cfg["mqtt"]["broker"]
        self.port = int(cfg["mqtt"]["port"])
        self.username = cfg["mqtt"]["username"]
        self.password = cfg["mqtt"]["password"]
        self.discovery_prefix = cfg["mqtt"]["discovery_prefix"]
        self.topic_prefix = cfg["mqtt"]["topic_prefix"].rstrip("/")
        self.client_id = cfg["mqtt"]["client_id"]
        self.client = mqtt.Client(client_id=self.client_id, clean_session=True)
        if self.username:
            self.client.username_pw_set(self.username, self.password or "")

    def connect(self) -> None:
        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            logger.info("✅ MQTT 已連線: %s:%s (client_id=%s)", self.broker, self.port, self.client_id)
        except Exception as e:
            logger.error("❌ MQTT 連線失敗: %s:%s，錯誤: %s", self.broker, self.port, e)
            raise

    def publish(self, topic_suffix: str, payload: Any, retain: bool = False) -> None:
        topic = f"{self.topic_prefix}/{topic_suffix.lstrip('/')}"
        try:
            self.client.publish(topic, payload=payload, retain=retain)
            logger.debug("📤 MQTT publish: %s => %s", topic, payload)
        except Exception as e:
            logger.error("❌ MQTT 發佈失敗: topic=%s, error=%s", topic, e)


# =========================
# 傳輸層：TCP (Modbus gateway)
# =========================

class TcpTransport:
    def __init__(self, host: str, port: int, timeout: float, buffer_size: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.buffer_size = buffer_size
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        while True:
            try:
                logger.info("🌐 嘗試連線 Modbus Gateway: %s:%s ...", self.host, self.port)
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                logger.info("✅ Modbus Gateway 已連線: %s:%s", self.host, self.port)
                return
            except Exception as e:
                logger.error("❌ 無法連線 Modbus Gateway: %s:%s，錯誤: %s，5 秒後重試...",
                             self.host, self.port, e)
                await asyncio.sleep(5)

    async def read_loop(self):
        """持續從 TCP 讀取 bytes。"""
        while True:
            try:
                data = await self.reader.read(self.buffer_size)
                if not data:
                    logger.warning("⚠️ Modbus Gateway 連線中斷，重新連線中...")
                    await self.connect()
                    continue
                yield data
            except Exception as e:
                logger.error("❌ TCP 傳輸層異常: %s，5 秒後重試...", e)
                await asyncio.sleep(5)
                await self.connect()


# =========================
# 傳輸層：RS485 USB (Serial)
# =========================

class SerialTransport(asyncio.Protocol):
    def __init__(self, on_packet_callback, device: str, baudrate: int, timeout: float) -> None:
        self.on_packet_callback = on_packet_callback
        self.device = device
        self.baudrate = baudrate
        self.timeout = timeout
        self.transport = None
        self.buffer = bytearray()

    def connection_made(self, transport) -> None:
        self.transport = transport
        logger.info("🔌 RS485 Serial 已連線: %s @ %d", self.device, self.baudrate)

    def data_received(self, data: bytes) -> None:
        # 這裡其實可以直接把 data 丟上去，由上層處理 packet 邏輯
        # 為了一致性，包一層 callback
        self.buffer.extend(data)
        logger.debug("📥 Serial 收到 %d bytes", len(data))
        # 這裡 demo 為「直接把收到的整包交給上層」
        # 若有分帧協議，可在此處切包
        if self.buffer:
            self.on_packet_callback(bytes(self.buffer))
            self.buffer.clear()

    def connection_lost(self, exc) -> None:
        logger.warning("⚠️ RS485 Serial 連線中斷: %s", exc)


async def create_serial_transport(loop, on_packet_callback, device: str, baudrate: int, timeout: float):
    while True:
        try:
            logger.info("🔌 嘗試開啟 RS485 Serial 裝置: %s @ %d ...", device, baudrate)
            _, protocol = await serial_asyncio.create_serial_connection(
                loop,
                lambda: SerialTransport(on_packet_callback, device, baudrate, timeout),
                device,
                baudrate=baudrate
            )
            return protocol
        except serial.SerialException as e:
            logger.error("❌ RS485 傳輸層異常: %s，5 秒後重試...", e)
            await asyncio.sleep(5)
        except PermissionError as e:
            logger.critical("❌ RS485 權限錯誤: %s，請確認 HA Add-on 已設定 uart & device 映射", e)
            await asyncio.sleep(10)
        except Exception as e:
            logger.error("❌ RS485 不明錯誤: %s，5 秒後重試...", e)
            await asyncio.sleep(5)


# =========================
# BMS 封包解析與 0x02 綁定邏輯
# =========================

class BmsPacketProcessor:
    """
    負責解析 BMS 封包，實作「0x02 綁定邏輯」與 address 線上狀態。

    流程簡化說明：
    - 收到 0x1200 類型封包（或功能碼 0x02）：
        -> 暫存為 last_realtime_packet
    - 收到 0x1000 類型封包（帶有 address/slave id）：
        -> 取出之前暫存的 last_realtime_packet
        -> 推斷這筆即時資訊屬於哪個 address
        -> 發佈 MQTT，「address X online」與相關數值
    """

    def __init__(self, mqtt_client: MqttClient, packet_expire_time: float) -> None:
        self.mqtt = mqtt_client
        self.packet_expire_time = packet_expire_time
        self.last_realtime_packet: Optional[Tuple[float, bytes]] = None  # (timestamp, data)

    @staticmethod
    def hexdump(data: bytes) -> str:
        return " ".join(f"{b:02X}" for b in data)

    def process_raw(self, data: bytes) -> None:
        """
        入口：外部傳輸層收到 bytes 後，呼叫這裡。
        """
        if not data:
            return

        logger.debug("📦 收到原始封包 (%d bytes): %s", len(data), self.hexdump(data))

        # 根據你的協議，這裡只是示意：
        # 假設：
        #   data[0:2] = header (0x55, 0xAA)
        #   data[2]   = cmd / 功能碼 or high-byte of type
        #   data[3]   = 次級 type
        #
        # 你之前提到 "0x1200 即時資訊"、"0x1000 address"，這裡模擬成：
        #   type = (data[2] << 8) | data[3]
        if len(data) < 4:
            logger.warning("⚠️ 封包長度過短，忽略: %s", self.hexdump(data))
            return

        pkt_type = (data[2] << 8) | data[3]

        if pkt_type == 0x1200:
            self._handle_realtime_packet(data)
        elif pkt_type == 0x1000:
            self._handle_address_packet(data)
        else:
            # 其他型別，有需要再擴充
            logger.debug("ℹ️ 收到其他類型封包 type=0x%04X，略過或日後擴充", pkt_type)

    def _handle_realtime_packet(self, data: bytes) -> None:
        """
        處理 0x1200 即時資訊封包：暫存起來，等待下一個 0x1000 address 封包來綁定。
        """
        now = time.time()
        self.last_realtime_packet = (now, data)
        logger.info("📡 收到 0x1200 即時資訊封包，等待 0x1000 address 封包綁定...")

    def _handle_address_packet(self, data: bytes) -> None:
        """
        處理 0x1000 address 封包：
        - 解析出地址/slave id
        - 若有尚未過期的 0x1200 即時資訊，綁定並發布
        """
        now = time.time()
        if len(data) < 6:
            logger.warning("⚠️ 0x1000 封包長度不足，無法解析地址: %s", self.hexdump(data))
            return

        # ★★★ 根據你的實際協議調整這裡 ★★★
        # 假設 address 在 data[4]
        address = data[4]

        # log 流程簡化：address X online
        logger.info("✅ address %d online (收到 0x1000 封包)", address)
        self.mqtt.publish(f"bms/{address}/status", "online", retain=True)

        # 綁定最近一次 0x1200 即時資訊
        if not self.last_realtime_packet:
            logger.info("ℹ️ 沒有可用的 0x1200 即時資訊可綁定，僅更新 address 線上狀態")
            return

        ts, realtime_data = self.last_realtime_packet
        if now - ts > self.packet_expire_time:
            logger.warning("⚠️ 最近的 0x1200 封包已過期(%.2fs)，不綁定", now - ts)
            self.last_realtime_packet = None
            return

        # 在這裡你可以解析 realtime_data -> 電壓、電流、SOC 等
        # 以下示範，實作時請改成實際解析
        volt_example = 52.3  # 假資料，請替換
        current_example = -5.4
        soc_example = 87

        logger.info(
            "🔗 0x1200 即時資訊綁定到 address %d (V=%.1fV, I=%.1fA, SOC=%d%%)",
            address, volt_example, current_example, soc_example
        )

        # 發佈 MQTT 數據
        self.mqtt.publish(f"bms/{address}/voltage", volt_example)
        self.mqtt.publish(f"bms/{address}/current", current_example)
        self.mqtt.publish(f"bms/{address}/soc", soc_example)

        # 綁定一次後可視需求清空或保留
        self.last_realtime_packet = None


# =========================
# 主程式
# =========================

async def main():
    cfg = load_config()
    app_cfg = cfg.get("app", {})

    debug_raw = bool(app_cfg.get("debug_raw_log", False))
    setup_logging(debug_raw)

    use_modbus_gateway = bool(app_cfg.get("use_modbus_gateway", False))
    use_rs485_usb = bool(app_cfg.get("use_rs485_usb", False))

    if not use_modbus_gateway and not use_rs485_usb:
        logger.warning("⚠️ Modbus Gateway 與 RS485 USB 都未啟用，請在 Add-on 設定中打開其中一種模式")
        # 不直接退出，避免使用者看不到 log，就先 sleep 等他調整
        await asyncio.sleep(60)
        return

    if use_modbus_gateway:
        tcp_cfg = cfg.get("tcp", {})
        host = tcp_cfg.get("host")
        port = int(tcp_cfg.get("port", 502))
        if not host:
            logger.error("❌ 已啟用 use_modbus_gateway，但未設定 modbus_host")
        else:
            logger.info("🌐 啟用 Modbus Gateway 模式: %s:%s", host, port)

    if use_rs485_usb:
        serial_cfg = cfg.get("serial", {})
        device = serial_cfg.get("device")
        baudrate = int(serial_cfg.get("baudrate", 9600))
        if not device:
            logger.error("❌ 已啟用 use_rs485_usb，但未設定 serial_device")
        else:
            logger.info("🔌 啟用 RS485 USB 模式: %s @ %d", device, baudrate)

    # 初始化 MQTT
    mqtt_client = MqttClient(cfg)
    mqtt_client.connect()

    # 初始化 BMS 封包處理器
    processor = BmsPacketProcessor(
        mqtt_client=mqtt_client,
        packet_expire_time=float(app_cfg.get("packet_expire_time", 0.4)),
    )

    loop = asyncio.get_running_loop()

    async def tcp_loop():
        if not use_modbus_gateway:
            return
        tcp_cfg = cfg.get("tcp", {})
        host = tcp_cfg.get("host")
        port = int(tcp_cfg.get("port", 502))
        timeout = float(tcp_cfg.get("timeout", 10))
        buffer_size = int(tcp_cfg.get("buffer_size", 4096))
        if not host:
            return

        transport = TcpTransport(host, port, timeout, buffer_size)
        await transport.connect()

        async for data in transport.read_loop():
            processor.process_raw(data)

    async def serial_loop():
        if not use_rs485_usb:
            return
        serial_cfg = cfg.get("serial", {})
        device = serial_cfg.get("device")
        baudrate = int(serial_cfg.get("baudrate", 9600))
        timeout = float(serial_cfg.get("timeout", 1.0))

        if not device:
            return

        async def on_packet(data: bytes):
            processor.process_raw(data)

        await create_serial_transport(loop, on_packet, device, baudrate, timeout)

        # serial_asyncio create_serial_connection 自己會跑事件，
        # 我們在這裡不需要額外 while loop，只要保持 loop 存活即可
        while True:
            await asyncio.sleep(3600)

    logger.info("🚀 主程式啟動，開始從 transport 收封包...")

    tasks = []
    if use_modbus_gateway:
        tasks.append(asyncio.create_task(tcp_loop()))
    if use_rs485_usb:
        tasks.append(asyncio.create_task(serial_loop()))

    if not tasks:
        logger.error("❌ 沒有啟動任何傳輸模式，程式結束")
        return

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 收到中止訊號，程式結束")
