FROM 3d-torchserve:latest

# 複製專案目錄到容器內的 /app 資料夾
COPY . /app

# 設定工作目錄
WORKDIR /app

# 開啟 TorchServe 服務
CMD ["torchserve", "--start", "--ts-config", "./config.properties", "--model-store", "./model_store", "--models", "models-1.mar"]
