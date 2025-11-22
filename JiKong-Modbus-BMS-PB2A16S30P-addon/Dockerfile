# 使用官方 Python 映像
FROM python:3.11-slim

# 避免 tzdata 互動式設置
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Taipei
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 安裝 tzdata 與 jq
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata jq && \
    rm -rf /var/lib/apt/lists/*

# 複製 requirements，並安裝 Python 相依
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 複製應用程式
COPY app /app

# 複製 run.sh 並加上執行權限
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

CMD ["/usr/local/bin/run.sh"]
