# jikong-rs485-pb2a16s30p-bms-addon
JiKong rs485 modbus bms  mqtt to home assistant</br>
How to connect the wires
https://github.com/phinix-org/Multiple-JK-BMS-by-Modbus-RS485</br>
Listening mode How to do
https://github.com/jean-luc1203/jkbms-rs485-addon/tree/main

usb to RS485 ch340 cp2102
can use
  - /dev/ttyUSB0
  - /dev/ttyUSB1
  - /dev/serial/by-id/usb-1a86_USB_Serial-if00-port


a > a | b > b 

輕量級 Python 程式，用於監聽 JK BMS (極空保護板bms) 透過 Modbus/RS485 轉 TCP Gateway 
發送的非標準數據封包 (或usb to rs485)
解析出即時數據和設定值，並以 MQTT json 格式發佈，支援 Home Assistant MQTT Discovery。

✨ 核心特色

自動重連 (High Resilience)：內建針對 Modbus Gateway (TCP) 和 MQTT Broker 的自動重連機制。 Modbus/RS485：當 Gateway 斷線或重啟時，程式會自動等待並重新建立 Socket 連線。 MQTT Broker：當 Broker 重啟時，程式會自動在背景執行緒中恢復連線，確保數據不丟失。 Home Assistant 整合：完整支援 Home Assistant MQTT Discovery，只需幾分鐘即可將所有 BMS 數據 (電壓、溫度、設定值等) 自動轉換為 HA 的 Sensor 和 Binary Sensor 實體。 JIKONG 特有封包處理：專門處理 JKBMS 的非標準 0x01 (設定值) 和 0x02 (即時值) 數據封包。 使用 0x01(數據類型1) 封包中的 ID 來關聯最近收到的 0x02(數據類型2) 即時數據，確保每組數據都能正確地發佈到對應的設備 ID 下。
簡潔 Log 輸出：保持精簡的 Log 輸出，方便在 Home Assistant Add-on Log 頁面快速診斷問題。 ⚙️ 安裝與部署 (Home Assistant)

Home Assistant Add-on 運行

環境準備：確保您的 Home Assistant 已安裝並啟用 MQTT Broker 附加元件
需要在 Add-on 設定中指定以下參數：

```
----------------------------------------------------
傳輸層設定 (選擇 TCP Gateway 或 RS485 to USB)
----------------------------------------------------
app:
啟用 Modbus Gateway (TCP) 模式 {默認 modbus gateway}
禁用 RS485 to USB 模式

獲取設備設定值(數據類型1)與即時資訊(數據類型2)的連動時間 默認既可(packet_expire_time)
packet_expire_time: 0.35
Modbus TCP Gateway 設定
tcp: host: 192.168.1.100 # 您的 Modbus Gateway IP 地址
port: 502 # Modbus TCP 預設端口
timeout: 10 # Socket 讀取超時時間

RS485 序列埠設定 (如果使用 RS485 模式)
serial:
device: /dev/ttyUSB0
baudrate: 115200
----------------------------------------------------
MQTT 服務設定 (通常使用 Home Assistant 內建 Broker)
----------------------------------------------------
mqtt: broker: 127.0.0.1 # HA Add-on 內部的 MQTT Broker 地址 port: 1883 username: your_mqtt_user password: your_mqtt_password
Home Assistant MQTT Discovery 前綴
discovery_prefix: homeassistant
數據發布的主題前綴 (State Topic)
topic_prefix: bms
設定值(數據類型1)的發布間隔 (秒)，減少寫入次數
settings_publish_interval: 60 # 1 分鐘發布一次設定值
```
