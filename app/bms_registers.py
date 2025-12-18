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

# 新增一個常量方便閱讀
HA_SENSOR = "sensor"
HA_BINARY = "binary_sensor"

# BMS Register Map
# Tuple 結構: (中文名稱, 單位, 類型, 轉換函數, HA類型, 圖標, 英文Key)
# ---------------------------------------------------------

BMS_MAP = {
    # =====================================================
    # 0x10: Master Command (新增：用於追蹤控制行為)
    # =====================================================
    0x10: {
        0: ("目標從機ID", None, TYPE_U8, conv_none, HA_SENSOR, "mdi:target-variant", "target_slave_id"),
        2: ("操作寄存器", "Hex", TYPE_U16, conv_hex, HA_SENSOR, "mdi:memory", "control_register"),
        7: ("指令數值", "Hex", TYPE_U16, conv_hex, HA_SENSOR, "mdi:numeric", "control_value"),
    },

    # =====================================================
    # 0x01: Parameter Settings (Base 0x1000) - 讀取保護板設定
    # =====================================================
    0x01: {
        0:   ("进入休眠电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sleep", "sleep_voltage"),
        4:   ("单体欠压保护", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_uvp"),
        8:   ("单体欠压保护恢复", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_uvp_recovery"),
        12:  ("单体过充保护", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_ovp"),
        16:  ("单体过充保护恢复电", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_ovp_recovery"),
        20:  ("触发均衡压差", "V", TYPE_U32, conv_div1000 , HA_SENSOR, "mdi:sine-wave", "balance_trigger_diff"),
        24:  ("SOC-100%电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "soc_100_voltage"),
        28:  ("SOC-0%电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "soc_0_voltage"),
        32:  ("推荐充电电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "rec_charge_voltage"),
        36:  ("浮充电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "float_charge_voltage"),
        40:  ("自动关机电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "auto_shutdown_voltage"),
        44:  ("持续充电电流", "A", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:current-dc", "cont_charge_current"),
        48:  ("充电过流保护延迟", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "charge_ocp_delay"),
        52:  ("充电过流保护解除", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "charge_ocp_release"),
        56:  ("持续放电电流", "A", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:current-dc", "cont_discharge_current"),
        60:  ("放电过流保护延迟", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "discharge_ocp_delay"),
        64:  ("放电过流保护解除", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "discharge_ocp_release"),
        68:  ("短路保护解除", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "sc_release"),
        72:  ("最大均衡电流", "mA", TYPE_U32, conv_none, HA_SENSOR, "mdi:current-dc", "max_balance_current"),
        76:  ("充电过温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "charge_otp"),
        80:  ("充电过温恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "charge_otp_recovery"),
        84:  ("放电过温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "discharge_otp"),
        88:  ("放电过温恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "discharge_otp_recovery"),
        92:  ("充电低温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "charge_utp"),
        96:  ("充电低温恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "charge_utp_recovery"),
        100: ("MOS过温保护", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "mos_otp"),
        104: ("MOS过温保护恢复", "°C", TYPE_I32, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "mos_otp_recovery"),
        108: ("单体数量", None, TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "cell_count"),
        112: ("充电开关", "Bit", TYPE_U32, conv_none, HA_BINARY, "mdi:battery-charging", "charge_switch"),
        116: ("放电开关", "Bit", TYPE_U32, conv_none, HA_BINARY, "mdi:battery-arrow-down", "discharge_switch"),
        120: ("均衡开关", "Bit", TYPE_U32, conv_none, HA_BINARY, "mdi:scale-balance", "balance_switch"),
#       124: ("电池设计容量", "mAH", TYPE_U32, conv_none, HA_SENSOR, "mdi:battery", "design_capacity"),
        128: ("短路保护延迟", "us", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "sc_delay"),
        132: ("均衡起始电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "balance_start_voltage"),
        # 136 (0x88) - 260 (0x104): Connection Line Resistance (32組)
#       136: ("Set: Wire Res 0", "uΩ", TYPE_U32, conv_none, HA_SENSOR, None, "wire_res_0"),
#       140: ("Set: Wire Res 1", "uΩ", TYPE_U32, conv_none, HA_SENSOR, None, "wire_res_1"),
#       144: ("Set: Wire Res 2", "uΩ", TYPE_U32, conv_none, HA_SENSOR, None, "wire_res_2"),
#       148: ("Set: Wire Res 3", "uΩ", TYPE_U32, conv_none, HA_SENSOR, None, "wire_res_3"),
        264: ("设备地址", "Hex", TYPE_U32, conv_hex, HA_SENSOR, "mdi:identifier", "device_address"),
#       268: ("放电预充时间", "S", TYPE_U32, conv_none, HA_BINARY, "mdi:transit-connection-variant", "precharge_time"),
#       276: ("Func Bits", "Hex", TYPE_U16, conv_hex, HA_SENSOR, None, "func_bits"), # Heating, GPS, etc.
        280: ("智能休眠时间", "H", TYPE_U8, conv_none, HA_SENSOR, "mdi:sleep", "smart_sleep_time"),
    },

    # =====================================================
    # 0x02: Realtime Data (Base 0x1200) - 即時監控數據
    # =====================================================
    0x02: {
        # --- Cell Voltages (0x00 - 0x3E) ---
        0:  ("01單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_01_voltage"),
        2:  ("02單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_02_voltage"),
        4:  ("03單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_03_voltage"),
        6:  ("04單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_04_voltage"),
        8:  ("05單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_05_voltage"),
        10: ("06單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_06_voltage"),
        12: ("07單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_07_voltage"),
        14: ("08單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_08_voltage"),
        16: ("09單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_09_voltage"),
        18: ("10單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_10_voltage"),
        20: ("11單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_11_voltage"),
        22: ("12單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_12_voltage"),
        24: ("13單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_13_voltage"),
        26: ("14單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_14_voltage"),
        28: ("15單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_15_voltage"),
        30: ("16單體電壓", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "cell_16_voltage"),
        
        # --- Battery Stats ---
#       64: ("电池状态", "Hex", TYPE_U32, conv_hex, HA_BINARY, "mdi:switch", "battery_status"),
        68: ("平均电压", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "avg_voltage"),
        70: ("最大压差", "V", TYPE_U16, conv_div1000, HA_SENSOR, "mdi:sine-wave", "max_diff_voltage"),
        72: ("最大單體", None, TYPE_U8, conv_plus1, HA_SENSOR, "mdi:format-list-numbered", "max_cell_index"),
        73: ("最小單體", None, TYPE_U8, conv_plus1, HA_SENSOR, "mdi:format-list-numbered", "min_cell_index"),

        # --- Balance Wire Resistances ---
#       74: ("Wire Res 0", "mΩ", TYPE_U16, conv_none, HA_SENSOR, None, "wire_res_0"),
#       76: ("Wire Res 1", "mΩ", TYPE_U16, conv_none, HA_SENSOR, None, "wire_res_1"),
#       78: ("Wire Res 2", "mΩ", TYPE_U16, conv_none, HA_SENSOR, None, "wire_res_2"),
#       80: ("Wire Res 3", "mΩ", TYPE_U16, conv_none, HA_SENSOR, None, "wire_res_3"),

        # --- Temps & Power ---
        138: ("功率板温度", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "power_board_temp"),
#       140: ("均衡线电阻状态", "Hex", TYPE_U32, conv_hex, HA_SENSOR, None, "wire_res_status"),
        144: ("电池总电压", "V", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:sine-wave", "total_voltage"),
        148: ("电池功率", "W", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:lightning-bolt", "power_watts"),
        152: ("电池电流", "A", TYPE_I32, conv_div1000, HA_SENSOR, "mdi:current-dc", "current"),
        156: ("电池温度1", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "temp_sensor_1"),
        158: ("电池温度2", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "temp_sensor_2"),

        # --- Alarms & Status ---
#       160: ("Alarm Bits 1", "Hex", TYPE_U32, conv_hex , HA_SENSOR, "mdi:switch", "alarm_bits_1"),
        164: ("均衡电流", "mA", TYPE_I16, conv_none, HA_SENSOR, "mdi:current-dc", "balance_current"),
        166: ("均衡:1充2放", "Enum", TYPE_U8, conv_none, HA_SENSOR, "mdi:scale-balance", "balance_action"),
        167: ("剩余电量", "%", TYPE_U8, conv_none, HA_SENSOR, "mdi:battery", "soc_percent"),
        168: ("剩余容量", "Ah", TYPE_I32, conv_div1000, HA_SENSOR, "mdi:battery", "remaining_capacity_ah"),
        172: ("电池实际容量", "Ah", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:battery", "actual_capacity_ah"),
        176: ("循环次数", "N", TYPE_U32, conv_none, HA_SENSOR, "mdi:battery", "cycle_count"),
        180: ("循环总容量", "Ah", TYPE_U32, conv_div1000, HA_SENSOR, "mdi:battery", "total_cycle_capacity"),
#       184: ("SOH估值", "%", TYPE_U8, conv_none, HA_SENSOR, "mdi:battery", "soh"),
#       185: ("预充状态", "Bit", TYPE_U8, conv_none, HA_SENSOR, None, "precharge_status"),
#       186: ("用户层报警", "Hex", TYPE_U16, conv_hex , HA_BINARY, "mdi:switch", "user_alarms"),
        188: ("运行时间", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "runtime_seconds"),
        192: ("充电状态", "Hex", TYPE_U16, conv_hex, HA_BINARY, "mdi:switch", "charge_status_hex"),
#       193: ("放电状态", "Hex", TYPE_U16, conv_hex, HA_SENSOR, None, "discharge_status_hex"),
#       194: ("用户层报警2", "Hex", TYPE_U16, conv_hex, HA_BINARY, "mdi:switch", "user_alarms_2"),

        # --- Protection Release Times ---
        196: ("放电过流保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter", "discharge_ocp_release_time"),
        198: ("放电短路保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter", "discharge_sc_release_time"),
        200: ("充电过流保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter", "charge_ocp_release_time"),
        202: ("充电短路保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter", "charge_sc_release_time"),
        204: ("单体欠压保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter", "cell_uvp_release_time"),
        206: ("单体过压保护解除时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter", "cell_ovp_release_time"),

        # --- Missing Sensors ---
#       208: ("Sensor Status", "Hex", TYPE_U16, conv_hex, HA_SENSOR, None, "sensor_status"),
        212: ("应急开关时间", "S", TYPE_U16, conv_none, HA_SENSOR, "mdi:counter", "emergency_switch_time"),

        # --- Calibration/Other ---
#       240: ("SysRunTicks", "0.1S", TYPE_U32, conv_none, HA_SENSOR, "mdi:counter", "sys_run_ticks"),
        248: ("电池温度3", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "temp_sensor_3"),
        250: ("电池温度4", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "temp_sensor_4"),
        252: ("电池温度5", "°C", TYPE_I16, conv_div10, HA_SENSOR, "mdi:temperature-celsius", "temp_sensor_5"),
#       256: ("RTC计数器", "Tick", TYPE_U32, conv_none, HA_SENSOR, "mdi:numeric", "rtc_counter"),
        264: ("进入休眠时间", "S", TYPE_U32, conv_none, HA_SENSOR, "mdi:sleep", "sleep_time_seconds"),
#       268: ("并联限流模块状态", "Bit", TYPE_U8, conv_none, HA_BINARY, "mdi:battery-charging", "parallel_limiter_status"),
#       9001: ("充电开关", None, TYPE_U8, conv_none, HA_BINARY, "mdi:battery-charging", "charge_mos"),
#       9002: ("放电开关", None, TYPE_U8, conv_none, HA_BINARY, "mdi:battery-arrow-down", "discharge_mos")
    }
}
