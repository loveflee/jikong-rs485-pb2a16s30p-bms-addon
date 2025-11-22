import socket

# -----------------------------
# 配置
# -----------------------------
TCP_HOST = "192.168.106.13"  # 監聽的 BMS TCP 端口
TCP_PORT = 502               # Modbus TCP / USR 透傳端口

# -----------------------------
# 監聽主程式
# -----------------------------
def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((TCP_HOST, TCP_PORT))
    print(f"✅ 已連線到 {TCP_HOST}:{TCP_PORT}，開始監聽所有數據...")

    try:
        while True:
            data = sock.recv(1024)
            if not data:
                break
            # 以 HEX 顯示所有收到的數據
            hex_str = " ".join(f"{b:02X}" for b in data)
            print(f"RAW ({len(data)} bytes): {hex_str}")

    except KeyboardInterrupt:
        print("🛑 停止監聽")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
