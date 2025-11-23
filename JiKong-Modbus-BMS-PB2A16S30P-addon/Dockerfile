# Dockerfile
# 使用官方 Python 映像
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /app

# 安裝 tzdata + jq
# tzdata：處理時區
# jq：從 /data/options.json 讀 HA Add-on 設定
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata jq \
    && rm -rf /var/lib/apt/lists/*

# 複製 requirements，並安裝依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製 Python 程式碼到 image 裡的 /app
COPY app /app

# 複製 run.sh 到 /usr/local/bin，並給予執行權限
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

# 預設時區（仍可以透過容器環境變數覆蓋）
ENV TZ=Asia/Taipei

# 啟動指令：執行 run.sh
CMD ["/usr/local/bin/run.sh"]
