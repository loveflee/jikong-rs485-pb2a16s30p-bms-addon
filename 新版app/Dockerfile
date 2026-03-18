# =============================================================================
# Dockerfile - JK-BMS Gateway V2.2.1
# =============================================================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Taipei

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 🟢 [修正] 將當前目錄下的所有檔案複製到容器的 /app 內
COPY . /app/

RUN mkdir -p /data

CMD ["python", "main.py"]
