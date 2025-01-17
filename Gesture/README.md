以下僅記錄包裝手勢辨識之container的方法
1. 使用指令$wget https://minio.pdc.tw/gesture-detection-model/mb1-ssd-best.pth 來下載手勢辨識的model
2. 使用指令$ sudo docker build -t {tag_name} . 即可將手勢辨識服務包裝成container