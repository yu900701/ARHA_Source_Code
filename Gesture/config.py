from pydantic.v1 import BaseSettings
import numpy as np


class Settings(BaseSettings):
    source: str = '0'
    device: str = '0'  # device arugments 使用GPU填裝置索引值'0'  使用CPU填'cpu'
    weights: str = 'mb1-ssd-best.pth'

    gRPC_port: str = '50051'

    net_type = 'mb1-ssd'
    label_path = 'voc-model-labels.txt'

    # 相機影像初始設定
    cap_img_width: int = 540  # 從source取得的影像寬度
    cap_img_height: int = 310  # 從source取得的影像高度

    # 影像前處理設定
    img_resize_w: int = 540  # 預設 1280
    img_resize_h: int = 310  # 預設 720

    # 演算法參數
    conf_thres: float = 0.40
    iou_thres: float = 0.65
    top_k: int = 1
    # classes: int = None

    # 除錯用設定
    is_debug: bool = True
    view_img: bool = True  # display results
    out_raw_video_name: str = 'raw'
    out_keypoint_video_name: str = 'keypoint'
    save_txt: bool = False   # 目前沒有用到
    save_txt_name: str = 'Logs'
    save_conf: bool = True  # save confidence in txt writing (--save-txt labels) 目前沒用到
    no_trace: bool = False   # 目前沒用到

    # bounding box顯示設定
    line_thickness: int = 3  # bounding box thickness (pixels)
    hide_labels: bool = False  # bounding box hide label
    hide_conf: bool = False  # bounding box hide conf

    # class Config:
    #    env_file = "env.txt"


settings = Settings()
