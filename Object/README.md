以下僅記錄如何將物件辨識服務包裝成container
1. 使用指令 $wget https://minio.pdc.tw/object-detection-base-image/3d-torchserve.tar 將包裝物件辨識服務所需之base image下載下來
2. 使用指令 $sudo docker load -i 3d-torchserve.tar 把壓縮檔load成物件辨識服務所需之image
3. 使用指令 $sudo docker image ls 應該要能看到名稱為3d-torchserve:latest的image
4. 使用指令 $cd torchserve/model_store 進入torchserve/model_store資料夾
5. 使用指令 $wget https://minio.pdc.tw/object-detection-base-image/models-1.mar 將物件辨識服務所需之模型下載下來
6. 使用指令 $cd .. 回到torchserve資料夾中
7. 使用指令 $sudo docker build -t {tag_name} . 即可將物件辨識服務包裝成container