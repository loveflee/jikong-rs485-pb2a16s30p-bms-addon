# 🚀 [V2.2.0 Fix] 更安全的緩衝區清理策略
else:
    if len(buffer) > 1024:
        logger.warning(f"⚠️ 偵測到 RS485 雜訊，執行溫和截斷 (現存: {len(buffer)} bytes)")
        del buffer[:512] # 不全清，保留後段可能有用的數據
    break
