# publisher.py
# 引入必要的函式庫
import json          # 用於將 Python 字典轉換為 JSON 字串，MQTT 數據傳輸常用格式
import time          # 用於處理時間和實現延遲 (sleep)
import yaml          # 用於讀取設定檔 (config.yaml)
import os            # 用於檔案系統操作，檢查設定檔是否存在
from typing import Dict, Any # 用於型別提示 (Type Hinting)，增加程式碼可讀性

import paho.mqtt.client as mqtt # 引入 paho-mqtt 函式庫，這是 Python 中常用的 MQTT 客戶端

# 假設這個檔案定義了 BMS 暫存器（Registers）的對應表
from bms_registers import BMS_MAP 


class MqttPublisher:
    """
    MQTT 發佈器類別：
    負責讀取設定檔、建立 MQTT 連線、處理連線/斷線事件，
    並將 BMS 數據以 JSON 格式發佈到 MQTT Broker (含 Home Assistant Discovery)。
    """
    
    def __init__(self, config_path: str = "/data/config.yaml"):
        """
        初始化 MqttPublisher 類別。
        讀取設定檔並建立 MQTT 客戶端實例。
        """
        # 檢查設定檔是否存在，如果不存在則拋出錯誤
        if not os.path.exists(config_path):
            raise FileNotFoundError(config_path)

        # 讀取 YAML 格式的設定檔
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # ------- 解析設定檔參數 -------
        self.mqtt_cfg = cfg.get("mqtt", {})
        self.app_cfg = cfg.get("app", {})
        
        # Home Assistant Discovery 的前綴 (例如: homeassistant)
        self.discovery_prefix = self.mqtt_cfg.get("discovery_prefix", "homeassistant")
        # 數據主題 (Topic) 的前綴 (例如: bms)
        self.topic_prefix = self.mqtt_cfg.get("topic_prefix", "bms")
        # MQTT 客戶端的 ID
        self.client_id = self.mqtt_cfg.get("client_id", "jk_bms_monitor")

        # 取得 Broker 連線資訊
        broker = self.mqtt_cfg.get("broker", "127.0.0.1")
        port = int(self.mqtt_cfg.get("port", 1883))
        username = self.mqtt_cfg.get("username")
        password = self.mqtt_cfg.get("password")

        # ------- 新增：內部連線狀態與連線資訊 -------
        self._connected = False  # 追蹤當前連線狀態
        self._broker = broker    # 儲存 Broker 位址
        self._port = port        # 儲存 Broker 埠號

        # ------- 建立 MQTT Client 實例 -------
        # client_id：客戶端唯一識別碼
        # protocol：使用 MQTTv3.1.1 協議
        # clean_session=True：連線時清除 Broker 上殘留的 session 資訊
        self.client = mqtt.Client(
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        # 設定帳號密碼 (如果設定檔有提供)
        if username:
            self.client.username_pw_set(username=username, password=password)

        # 自動重連延遲設定 (使用 Backoff 機制)
        # 設置重連的最小 (1 秒) 和最大 (30 秒) 延遲時間
        try:
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        except Exception:
            # 舊版 paho 可能不支援此 API，忽略錯誤
            pass

        # ------- 綁定 callback 函式 -------
        # 當連線成功時，呼叫 _on_connect
        self.client.on_connect = self._on_connect
        # 當連線斷開時，呼叫 _on_disconnect
        self.client.on_disconnect = self._on_disconnect

        # ------- 啟動連線與背景迴圈 -------
        try:
            # 使用 connect_async 進行非同步連線
            self.client.connect_async(self._broker, self._port, keepalive=60)
            # 啟動背景網路迴圈，使客戶端能夠自動處理連線/斷線/重連
            self.client.loop_start() 
            print(f"✅ 已嘗試連線到 MQTT {broker}:{port} (client_id={self.client_id})")
        except Exception as e:
            print(f"❌ 啟動 MQTT 連線失敗 {broker}:{port} - {e}")

        # 設定封包發佈節流 (Throttle) 的時間紀錄
        # 用來追蹤每個 device_id 的 settings 數據上次發佈的時間
        self.settings_last_publish: Dict[int, float] = {}
        # 避免重複發送 Home Assistant Discovery 訊息，用集合 (set) 儲存已發送的 key (device_id, packet_type)
        self._published_discovery = set()

    # ---------------- MQTT Callbacks (回呼函式) ----------------

    def _on_connect(self, client, userdata, flags, rc):
        """
        MQTT 連線成功或失敗時觸發
        rc=0 表示成功。
        """
        if rc == 0:
            self._connected = True
            print(f"✅ MQTT 已連線成功: {self._broker}:{self._port}")
        else:
            # rc != 0 代表連線失敗，paho 會依 reconnect_delay 自動重試
            self._connected = False
            print(f"⚠️ MQTT 連線失敗 rc={rc}，將自動重試")

    def _on_disconnect(self, client, userdata, rc):
        """
        MQTT 連線斷開時觸發
        rc=0 表示正常斷開，rc!=0 表示非預期斷開 (例如 Broker 關閉)。
        """
        self._connected = False
        if rc != 0:
            print(f"⚠️ MQTT 非預期斷線 rc={rc}，將自動嘗試重連")
        else:
            print("ℹ️ MQTT 已正常斷線")

    # ---------------- 安全發佈（含簡單重試） ----------------

    def _safe_publish(self, topic: str, payload: str, retain: bool = False, retries: int = 3):
        """
        包一層安全發佈邏輯：
        1. 檢查連線狀態，若未連線則等待 1 秒。
        2. 若發佈失敗 (rc != success)，則進行多次重試。
        """
        for attempt in range(1, retries + 1):
            if not self._connected:
                # MQTT 尚未連線好，稍等一下再試
                time.sleep(1)
            try:
                # 執行發佈操作
                result = self.client.publish(topic, payload=payload, retain=retain)
                
                # 統一處理 paho v1/v2 的回傳結果，取得返回碼 (rc)
                rc = getattr(result, "rc", result[0] if isinstance(result, tuple) else 0)
                
                if rc == mqtt.MQTT_ERR_SUCCESS:
                    # 成功就回傳
                    return True
                else:
                    print(f"⚠️ MQTT publish 失敗 (rc={rc})，第 {attempt}/{retries} 次重試...")
                    time.sleep(1)
            except Exception as e:
                print(f"❌ MQTT publish 發生例外: {e}，第 {attempt}/{retries} 次重試...")
                time.sleep(1)

        print(f"❌ MQTT publish 多次重試仍失敗，topic={topic}")
        return False

    # ---------------- MQTT Discovery (Home Assistant 自動配置) ----------------

    def _make_device_info(self, device_id: int) -> Dict[str, Any]:
        """
        建立 Home Assistant 設備資訊字典，用於 Discovery Payload。
        """
        ident = f"jk_bms_{device_id}"
        return {
            "identifiers": [ident],          # 設備的唯一識別碼
            "manufacturer": "JiKong",        # 製造商
            "model": "PB2A16S30P",           # 型號 (可調整)
            "name": f"JK modbus BMS {device_id}", # 設備名稱
        }

    def _sensor_discovery_topic(self, device_id: int, object_id: str) -> str:
        """
        生成標準感測器 (sensor) 的 Discovery 主題。
        格式: <discovery_prefix>/sensor/<node_id>/<object_id>/config
        """
        node_id = f"jk_bms_{device_id}"
        return f"{self.discovery_prefix}/sensor/{node_id}/{object_id}/config"

    def _binary_sensor_discovery_topic(self, device_id: int, object_id: str) -> str:
        """
        生成二元感測器 (binary_sensor) 的 Discovery 主題 (用於開/關狀態)。
        格式: <discovery_prefix>/binary_sensor/<node_id>/<object_id>/config
        """
        node_id = f"jk_bms_{device_id}"
        return f"{self.discovery_prefix}/binary_sensor/{node_id}/{object_id}/config"

    def publish_discovery_for_packet_type(
        self, device_id: int, packet_type: int, data_map: Dict[int, Any]
    ):
        """
        針對某一特定封包類型 (Realtime/Settings) 發佈 Home Assistant Discovery 訊息。
        """
        key = (device_id, packet_type)
        # 檢查是否已發佈過，如果是，則直接返回，避免重複發送
        if key in self._published_discovery:
            return
        self._published_discovery.add(key)

        device_info = self._make_device_info(device_id)
        
        # 決定狀態主題 (State Topic)，0x02 是實時數據 (realtime)，否則為設定數據 (settings)
        state_topic = (
            f"{self.topic_prefix}/{device_id}/realtime"
            if packet_type == 0x02
            else f"{self.topic_prefix}/{device_id}/settings"
        )

        # 遍歷 BMS 暫存器定義表中的每個項目
        for offset in sorted(data_map.keys()):
            entry = data_map[offset]

            name = entry[0]                         # 暫存器名稱 (例如: 'Total Voltage')
            unit = entry[1]                         # 單位 (例如: 'V')
            # 取得 HA 類型，預設是 sensor
            ha_type = entry[4] if len(entry) > 4 else "sensor" 
            # 取得 HA 圖標 (Icon)
            icon = entry[5] if len(entry) > 5 else None

            # 組合唯一識別碼
            object_id = f"reg_{packet_type}_{offset}"
            unique_id = f"jk_bms_{device_id}_{packet_type}_{offset}"
            value_key = name                        # 在 JSON payload 中取值的 key

            # 建立 Discovery Payload 基礎結構
            payload = {
                "name": name,
                "unique_id": unique_id,
                "state_topic": state_topic,         # 指向發佈數據的 Topic
                "device": device_info,              # 設備資訊
            }
            if icon:
                payload["icon"] = icon

            # 根據 HA 類型調整 Payload 內容
            if ha_type == "binary_sensor":
                # 二元感測器 (開/關) 需要定義開和關的 Payload
                payload["payload_on"] = "1"
                payload["payload_off"] = "0"
                # value_template：將 JSON 數據轉換為 Home Assistant 狀態值 (1 或 0)
                payload[
                    "value_template"
                ] = f"{{{{ 1 if value_json['{value_key}'] in (1, True, '1', 'ON') else 0 }}}}"
                topic = self._binary_sensor_discovery_topic(device_id, object_id)
            else: # 默認為 sensor
                # value_template：直接從 JSON 數據中取出值
                payload["value_template"] = f"{{{{ value_json['{value_key}'] }}}}"
                # 如果有單位且不是特殊類型 (Hex, Bit, Enum)，則加入單位
                if unit and unit not in ("Hex", "Bit", "Enum"):
                    payload["unit_of_measurement"] = unit
                topic = self._sensor_discovery_topic(device_id, object_id)

            try:
                # 發佈 Discovery 訊息，使用 retain=True (保留標記) 確保 HA 重新啟動時能收到配置
                self._safe_publish(topic, json.dumps(payload), retain=True)
            except Exception as e:
                print(f"❌ publish discovery {ha_type} failed: {e}")

    # ---------------- 實際發佈 payload ----------------

    def publish_payload(self, device_id: int, packet_type: int, payload_dict: Dict[str, Any]):
        """
        將 BMS 讀取到的實際數據 (payload_dict) 發佈到 MQTT。
        """
        if packet_type not in BMS_MAP:
            print(f"⚠️ 未知的封包類型: {hex(packet_type)}")
            return

        # Settings 數據 (0x01) 發佈節流 (Throttle)
        if packet_type == 0x01:
            # 取得設定的發佈間隔，預設 1800 秒 (30 分鐘)
            interval = float(self.app_cfg.get("settings_publish_interval", 1800))
            last_time = self.settings_last_publish.get(device_id, 0)
            now = time.time()
            # 如果距離上次發佈的時間小於設定的間隔，則不發佈
            if now - last_time < interval:
                return
            # 更新上次發佈時間
            self.settings_last_publish[device_id] = now

        # 決定數據類型名稱和發佈的主題
        kind = "realtime" if packet_type == 0x02 else "settings"
        state_topic = f"{self.topic_prefix}/{device_id}/{kind}"
        
        # 實際發佈數據
        try:
            # 調用安全發佈函式，將數據字典轉為 JSON 字串發佈
            ok = self._safe_publish(state_topic, json.dumps(payload_dict), retain=False)
            if ok and packet_type == 0x02:
                # 這裡原本可能有一行 log，現已註解掉以保持 log 清潔
                # print(f"📡 BMS {device_id} realtime 更新已發佈到 MQTT")
                pass
        except Exception as e:
            print(f"❌ publish payload failed: {e}")

        # Discovery (只發一次)
        # 取得暫存器定義，並呼叫 Discovery 函式，確保感測器被配置
        register_def = BMS_MAP[packet_type]
        self.publish_discovery_for_packet_type(device_id, packet_type, register_def)


_publisher_instance = None # 模組級別的變數，用於儲存單例實例 (Singleton)


def get_publisher(config_path: str = "/data/config.yaml"):
    """
    提供 MqttPublisher 的單例 (Singleton) 模式存取。
    確保整個應用程式中只會有一個 MqttPublisher 實例。
    """
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = MqttPublisher(config_path)
    return _publisher_instance
