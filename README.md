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

JK BMS Modbus 轉 MQTT 橋接 | JK BMS Modbus to MQTT Bridge

這是一個輕量級且高容錯的 Python 程式，用於監聽 JKBMS (嘉康電池管理系統) 透過 Modbus/RS485 轉 TCP Gateway 發送的非標準數據封包，解析出即時數據和設定值，並以 MQTT 格式發佈。同時支援 Home Assistant MQTT Discovery。 This is a lightweight and fault-tolerant Python program that listens to non-standard data packets sent by the JKBMS (JiaKang Battery Management System) via a Modbus/RS485 to TCP Gateway. It parses real-time data and settings and publishes them in MQTT format, while also supporting Home Assistant MQTT Discovery.

本專案旨在解決 JKBMS 數據流的非標準特性，並確保在網路或設備重啟時的穩定性與自動恢復能力。 This project aims to address the non-standard nature of the JKBMS data stream and ensure stability and automatic recovery when the network or device restarts.

✨ 核心特色 | Core Features

自動重連 (高容錯性) | Automatic Reconnection (High Resilience)：內建針對 Modbus Gateway (TCP) 和 MQTT Broker 的自動重連機制。
Modbus/RS485：當 Gateway 斷線或重啟時，程式會自動等待並重新建立 Socket 連線。
Modbus/RS485: When the Gateway disconnects or restarts, the program automatically waits and re-establishes the Socket connection.
MQTT Broker：當 Broker 重啟時，程式會自動在背景執行緒中恢復連線，確保數據不丟失。
MQTT Broker: When the Broker restarts, the program automatically resumes the connection in a background thread, ensuring no data loss.
Home Assistant 整合 | Home Assistant Integration：完整支援 Home Assistant MQTT Discovery，只需幾分鐘即可將所有 BMS 數據 (電壓、溫度、設定值等) 自動轉換為 HA 的 Sensor 和 Binary Sensor 實體。 Full support for Home Assistant MQTT Discovery, allowing all BMS data (voltage, temperature, settings, etc.) to be automatically converted into HA Sensor and Binary Sensor entities in minutes.
JIKONG 特有封包處理 | JIKONG Specific Packet Handling：專門處理 JKBMS 的非標準 0x01 (設定值) 和 0x02 (即時值) 數據封包。 It specializes in handling the non-standard 0x01 (settings) and 0x02 (real-time) data packets of the JKBMS.
使用 0x01 封包中的 ID 來關聯最近收到的 0x02 即時數據，確保每組數據都能正確地發佈到對應的設備 ID 下。
The ID from the 0x01 packet is used to associate the most recently received 0x02 real-time data, ensuring each data set is published correctly under the corresponding device ID.
簡潔 Log 輸出 | Concise Log Output：保持精簡的 Log 輸出，方便在 Home Assistant Add-on Log 頁面快速診斷問題。 Maintains concise log output for quick problem diagnosis on the Home Assistant Add-on Log page.
⚙️ 安裝與部署 (Home Assistant) | Installation and Deployment (Home Assistant)

本專案強烈建議作為 Home Assistant Add-on 運行。 This project is strongly recommended to be run as a Home Assistant Add-on.

環境準備 | Environment Preparation：確保您的 Home Assistant 已安裝並啟用 MQTT Broker 附加元件。 Ensure your Home Assistant has the MQTT Broker add-on installed and enabled.
配置 | Configuration：將您的配置寫入 /data/config.yaml。 Write your configuration to /data/config.yaml.
📐 架構與重連機制 | Architecture and Reconnection Mechanism

本專案遵循明確的職責分離設計，以確保高穩定性： This project adheres to a clear separation of responsibilities design to ensure high stability:

模組	職責 (Chinese / English)	容錯機制 (Chinese / English)
transport.py	建立與維持 Modbus/RS485 連線，接收原始 bytes。 / Establishes and maintains Modbus/RS485 connection, receives raw bytes.	無限重試迴圈：斷線、連線重置或 Gateway 重啟時，自動關閉 Socket，等待 5 秒後重新執行連線。 / Infinite Retry Loop: Automatically closes the Socket, waits for 5 seconds, and attempts to reconnect upon disconnection, connection reset, or Gateway restart.
publisher.py	處理 MQTT 連線、發布數據和 Discovery。 / Handles MQTT connection, data publishing, and Discovery.	Paho-MQTT Loop：啟動後會運行於背景執行緒，自動處理 Broker 斷線後的重連。 啟動時重試：應用程式啟動時，若 Broker 未準備好，會每 5 秒重試連線。 / Paho-MQTT Loop: Runs in a background thread after startup, automatically handling reconnection after Broker disconnects. Startup Retry: Retries connection every 5 seconds if the Broker is not ready upon application startup.
main.py	核心邏輯 (0x02 緩存，等待 0x01 ID 關聯)。 / Core logic (0x02 caching, waiting for 0x01 ID association).	依賴 transport.py 的穩定數據流，本身不處理連線錯誤，保持業務邏輯的純粹。 / Relies on the stable data stream from transport.py, maintains pure business logic by not handling connection errors itself.
🤝 貢獻 | Contribution

歡迎提交 Pull Requests 或開啟 Issue 討論： Welcome to submit Pull Requests or open Issues for discussion:

CRC 校驗 | CRC Checksum: 目前版本尚未實作 JK BMS 封包末端的 CRC 校驗。如果希望提升數據的正確性和防錯能力，可以實作 Checksum 檢查。 The current version does not implement the CRC checksum at the end of the JK BMS packet. Implementing a Checksum check is recommended to improve data accuracy and error prevention.
