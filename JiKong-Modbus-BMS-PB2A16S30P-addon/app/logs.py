# logs.py
#
# 獨立的「除錯監聽程式」，你可以在 HA Add-on 裡
# 另外開一個 command 或手動執行：
#   python3 /app/logs.py
#
# 目前只支援 TCP Modbus Gateway，直接讀 /data/config.yaml 的 tcp 設定。

import socket
import yaml
import os
import sys


CONFIG_PATH = "/data/config.yaml"


def load_tcp_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 找不到設定檔 {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    tcp = cfg.get("tcp", {})
    host = tcp.get("host", "192.168.106.13")
    port = int(tcp.get("port", 502))
    timeout = float(tcp.get("timeout", 10))
    return host, port, timeout


def hexdump(prefix: str, data: bytes):
    hex_str = " ".join(f"{b:02X}" for b in data)
    print(f"{prefix} RAW ({len(data)} bytes): {hex_str}")


def main():
    host, port, timeout = load_tcp_config()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((host, port))
    print(f"✅ 已連線到 {host}:{port}，開始監聽所有數據...")

    try:
        while True:
            data = sock.recv(1024)
            if not data:
                print("⚠️ 對端關閉連線")
                break
            hexdump("[LOG]", data)
    except KeyboardInterrupt:
        print("🛑 停止監聽")
    except Exception as e:
        print(f"❌ logs 監聽異常: {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
