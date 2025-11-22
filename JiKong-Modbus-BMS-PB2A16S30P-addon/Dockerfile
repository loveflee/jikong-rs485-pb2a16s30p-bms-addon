ARG BUILD_FROM=ghcr.io/hassio-addons/base-python:16.2.0
FROM ${BUILD_FROM}

# 讓 log 立即 flush
ENV PYTHONUNBUFFERED=1

# 工作目錄
WORKDIR /app

# 複製需求
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 複製程式碼
COPY app /app

# 複製啟動腳本
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

# 啟動指令：執行 run.sh
CMD ["/usr/local/bin/run.sh"]
