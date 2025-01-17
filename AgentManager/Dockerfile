# 使用官方 Python slim 版作為基礎映像
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /app

# 將當前目錄的內容複製到容器的 /app 目錄中
COPY . /app

RUN mkdir -p /logs

# 安裝必要的 Python 套件
RUN pip install --no-cache-dir fastapi==0.115.4 uvicorn==0.32.0 requests==2.32.3 paramiko==3.5.0 websockets==13.1

# 執行應用程式
CMD ["python3"]
