# bms_registers.py
# 定義數據類型常量
TYPE_U8  = 'B'  # Unsigned 8-bit
TYPE_U16 = 'H'  # Unsigned 16-bit
TYPE_I16 = 'h'  # Signed 16-bit
TYPE_U32 = 'I'  # Unsigned 32-bit
TYPE_I32 = 'i'  # Signed 32-bit
TYPE_STR = 's'  # String/ASCII
# 單位轉換 Lambda 函數
conv_div1000 = lambda v: round(v / 1000.0, 3)  # mV -> V, mA -> A
conv_div100  = lambda v: round(v / 100.0, 2)   # 0.01V -> V
conv_div10   = lambda v: round(v / 10.0, 1)    # 0.1C -> C, 0.1S -> S
conv_none    = lambda v: v                     # 無需轉換
conv_hex     = lambda v: f"0x{v:08X}"          # 顯示為 HEX
conv_plus1   = lambda v: v + 1                 # 將索引值 +1
# 新增一個常量方便閱讀 (可選)
HA_SENSOR = "sensor"
HA_BINARY = "binary_sensor"
# BMS Register Map
# Tuple 結構擴充為:
# (Name, Unit, Type, Converter, HA_Type, Icon)
# 如果後兩項省略，預設為 HA_SENSOR 和 None
# ---------------------------------------------------------
# BMS Register Map (Full Version)
# Key: Response ID (e.g., 0x01, 0x02)
# Value: Dictionary of {Offset: (Name, Unit, Type, Converter)}
# ---------------------------------------------------------
BMS_MAP = {
    # =====================================================
    # 0x01: Parameter Settings (Base 0x1000) - 讀取保護板設定
    # =====================================================
    0x01: {
        0:   ("进入休眠电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sleep"),
        4:   ("单体欠压保护", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        8:   ("单体欠压保护恢复", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        12:  ("单体过充保护", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        16:  ("单体过充保护恢复电", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        20:  ("触发均衡压差", "V", TYPE_U32, conv_div1000 , HA_SENSOR, "mdi:sine-wave"),
        24:  ("SOC-100%电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        28:  ("SOC-0%电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        32:  ("推荐充电电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        36:  ("浮充电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        40:  ("自动关机电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        44:  ("持续充电电流", "A", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:current-dc"),
        48:  ("充电过流保护延迟", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        52:  ("充电过流保护解除", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        56:  ("持续放电电流", "A", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:current-dc"),
        60:  ("放电过流保护延迟", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        64:  ("放电过流保护解除", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        68:  ("短路保护解除", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        72:  ("最大均衡电流", "mA", TYPE_U32, conv_none, HA_SENSOR, "mdi:current-dc"),
        76:  ("充电过温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        80:  ("充电过温恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        84:  ("放电过温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        88:  ("放电过温恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        92:  ("充电低温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        96:  ("充电低温恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        100: ("MOS过温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        104: ("MOS过温保护恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        108: ("单体数量", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        112: ("充电开关", "Bit", TYPE_U32, conv_none, HA_BINARY, "mdi:battery-charging" ),
        116: ("放电开关", "Bit", TYPE_U32, conv_none, HA_BINARY, "mdi:battery-arrow-down"),
        120: ("均衡开关", "Bit", TYPE_U32, conv_none, HA_BINARY, "mdi:scale-balance"),
        124: ("电池设计容量", "mAH", TYPE_U32, conv_none, HA_SENSOR, "mdi:current-dc"),
        128: ("短路保护延迟", "us", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        132: ("均衡起始电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        # 136 (0x88) - 260 (0x104): Connection Line Resistance (32組)
        # 為了不洗版，這裡僅列出前4組，如果需要全部可解開迴圈
#        136: ("Set: Wire Res 0", "uΩ", TYPE_U32, conv_none),
#        140: ("Set: Wire Res 1", "uΩ", TYPE_U32, conv_none),
#        144: ("Set: Wire Res 2", "uΩ", TYPE_U32, conv_none),
#        148: ("Set: Wire Res 3", "uΩ", TYPE_U32, conv_none),
        # ... 中間省略 Wire Res 4-31 ...
        264: ("设备地址", "Hex", TYPE_U32, conv_hex, HA_SENSOR, "mdi:identifier"),
        268: ("放电预充时间", "S", TYPE_U32, conv_none, HA_BINARY, "mdi:transit-connection-variant"),
        276: ("Func Bits", "Hex", TYPE_U16, conv_hex), # Heating, GPS, etc.
        280: ("智能休眠时间", "H", TYPE_U8, conv_none, HA_SENSOR, "mdi:sleep"),
    },
    # =====================================================
    # 0x02: Realtime Data (Base 0x1200) - 即時監控數據
    # =====================================================
    0x02: {
        # --- Cell Voltages (0x00 - 0x3E) ---
        0:  ("01單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        2:  ("02單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        4:  ("03單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        6:  ("04單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        8:  ("05單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        10: ("06單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        12: ("07單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        14: ("08單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        16: ("09單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        18: ("10單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        20: ("11單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        22: ("12單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        24: ("13單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        26: ("14單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        28: ("15單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        30: ("16單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        # 假設只用到 16 串，若更多可繼續加 ...
        # --- Battery Stats ---
        64: ("电池状态", "Hex", TYPE_U32, conv_hex, HA_BINARY, "mdi:switch"), # Which cells exist
        68: ("平均电压", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        70: ("最大压差", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        72: ("最大單體", "S", TYPE_U8, conv_plus1, HA_SENSOR, "mdi:numeric"),
        73: ("最小單體", "S", TYPE_U8, conv_plus1, HA_SENSOR, "mdi:numeric"), # Offset 72 is U8(Max), next byte is Min

        # --- Balance Wire Resistances (0x4A - 0x88) ---
#        74: ("Wire Res 0", "mΩ", TYPE_U16, conv_none),
#        76: ("Wire Res 1", "mΩ", TYPE_U16, conv_none),
#        78: ("Wire Res 2", "mΩ", TYPE_U16, conv_none),
#        80: ("Wire Res 3", "mΩ", TYPE_U16, conv_none),
        # ... 這裡還有很多組，為版面整潔只列出前幾組 ...

        # --- Temps & Power ---
        138: ("功率板温度", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        140: ("均衡线电阻状态", "Hex", TYPE_U32, conv_hex),
        144: ("电池总电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave"),
        148: ("电池功率", "W", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:lightning-bolt"),
        152: ("电池电流", "A", TYPE_I32, conv_div1000, HA_SENSOR, "mdi:current-dc"),
        156: ("电池温度1", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        158: ("电池温度2", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),

        # --- Alarms & Status ---
        160: ("Alarm Bits 1", "Hex", TYPE_U32, conv_hex , HA_SENSOR, "mdi:switch"), # 包含過壓、過流等報警
        164: ("均衡电流", "mA", TYPE_I16, conv_none, HA_SENSOR, "mdi:current-dc"),
        166: ("均衡状态", "Enum", TYPE_U8, conv_none), # 0:Off, 1:Chg, 2:Dchg
        167: ("剩余电量", "%", TYPE_U8, conv_none, HA_SENSOR, "mdi:battery"),
        168: ("剩余容量", "Ah", TYPE_I32, conv_div1000, HA_SENSOR, "mdi:battery"),
        172: ("电池实际容量", "Ah", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:battery"),
        176: ("循环次数", "N", TYPE_U32, conv_none, HA_SENSOR, "mdi:battery"),
        180: ("循环总容量", "Ah", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:battery"),
        184: ("SOH估值", "%", TYPE_U8, conv_none, HA_SENSOR, "mdi:battery"),
        185: ("预充状态", "Bit", TYPE_U8, conv_none),
        186: ("用户层报警", "Hex", TYPE_U16, conv_hex , HA_BINARY, "mdi:switch"),
        188: ("运行时间", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        192: ("充电状态", "Hex", TYPE_U16, conv_hex, HA_BINARY, "mdi:switch"), # High byte/Low byte mix
        193: ("放电状态", "Hex", TYPE_U16, conv_hex), # High byte/Low byte mix
        194: ("用户层报警2", "Hex", TYPE_U16, conv_hex, HA_BINARY, "mdi:switch"),

        # --- Protection Release Times ---
        196: ("放电过流保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter"),
        198: ("放电短路保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter"),
        200: ("充电过流保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter"),
        202: ("充电短路保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter"),
        204: ("单体欠压保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter"),
        206: ("单体过压保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter"),

        # --- Missing Sensors ---
        208: ("Sensor Status", "Hex", TYPE_U16, conv_hex),
        212: ("应急开关时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter"),

        # --- Calibration/Other ---
        240: ("SysRunTicks", "0.1S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter"),
        248: ("电池温度3", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        250: ("电池温度4", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        252: ("电池温度5", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius"),
        256: ("RTC计数器", "Tick", TYPE_U32, conv_none, HA_SENSOR, "mdi:numeric"),
        264: ("进入休眠时间", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:sleep"),
        268: ("并联限流模块状态", "Bit", TYPE_U8, conv_none, HA_BINARY, "mdi:battery-charging"),
        9001: ("充电开关", None, TYPE_U8, conv_none, HA_BINARY, "mdi:battery-charging"),
        9002: ("放电开关", None, TYPE_U8, conv_none, HA_BINARY, "mdi:battery-arrow-down")
    }
}
