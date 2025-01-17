import json
import base64
from concurrent import futures

import grpc
import gesture_pb2
import gesture_pb2_grpc
import time
import cv2
import math
import numpy as np
from datetime import datetime
import traceback
from vision.ssd.mobilenetv1_ssd import create_mobilenetv1_ssd, create_mobilenetv1_ssd_predictor
from vision.utils.misc import Timer
from config import settings


net_type = settings.net_type
model_path = settings.weights
label_path = settings.label_path


class_names = [name.strip() for name in open(label_path).readlines()]
num_classes = len(class_names)

net = create_mobilenetv1_ssd(len(class_names), is_test=True)
net.load(model_path)

predictor = create_mobilenetv1_ssd_predictor(net, candidate_size=200)

timer = Timer()
frame_index = 0


class GestureRecognitionService(gesture_pb2_grpc.GestureRecognitionServicer):
    def __init__(self):
        # 建立 TensorFlow 操作，確保它在 GPU 上運行
        # self.random_tensor = tf.random.uniform((1000, 1000))
        self.recognition_times = []
        self.recognition_count = 0

    def Recognition(self, request, context):
        global frame_index
        try:
            imgString = base64.b64decode(request.image)
            nparr = np.fromstring(imgString, np.uint8)
            # nparr = np.frombuffer(request.image, np.uint8)  # 將二進制數據轉換成 numpy 陣列
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)  # 將二進制數據轉換成圖像
            # print("Image decoded.")

            w, h = settings.img_resize_w, settings.img_resize_h  # 影像尺寸
            img = cv2.resize(img, (w, h))  # 縮小尺寸，加快處理效率
            img2 = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)  # 轉換成 RGB 色彩
            # print("Image resize and converted to RGB.")

            timer.start()
            boxes, labels, probs = predictor.predict(img2, settings.top_k, settings.conf_thres)
            interval = timer.end()
            print('Time: {:.2f}s, Detect Objects: {:d}.'.format(interval, labels.size(0)))

            frame_index += 1
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            action = json.dumps({'Left':"", 'Right':""})

            text = {'Left': "", 'Right': ""}
            for i in range(boxes.size(0)):
                box = boxes[i, :]
                label = f"{class_names[labels[i]]}: {probs[i]:.2f}"
                label_name = class_names[labels[i]]

                if label_name == "hand_0":
                    text["Right"] = '0'
                    text["Left"] = '0'
                elif label_name == "hand_1":
                    text["Right"] = '1'
                    text["Left"] = '1'
                elif label_name == "hand_2":
                    text["Right"] = '2'
                    text["Left"] = '2'
                elif label_name == "hand_3":
                    text["Right"] = '3'
                    text["Left"] = '3'
                elif label_name == "hand_4":
                    text["Right"] = '4'
                    text["Left"] = '4'
                elif label_name == "hand_5":
                    text["Right"] = '5'
                    text["Left"] = '5'
                elif label_name == "hand_6":
                    text["Right"] = '6'
                    text["Left"] = '6'
                elif label_name == "hand_7":
                    text["Right"] = '7'
                    text["Left"] = '7'
                # elif label_name == "rhand_8":
                #     text["Right"] = '8'
                # elif label_name == "rhand_9":
                #     text["Right"] = '9'
                # elif label_name == "lhand_0":
                #     text["Left"] = '0'
                # elif label_name == "lhand_1":
                #     text["Left"] = '1'
                # elif label_name == "lhand_2":
                #     text["Left"] = '2'
                # elif label_name == "lhand_3":
                #     text["Left"] = '3'
                # elif label_name == "lhand_4":
                #     text["Left"] = '4'
                # elif label_name == "lhand_5":
                #     text["Left"] = '5'
                # elif label_name == "lhand_6":
                #     text["Left"] = '6'
                # elif label_name == "lhand_7":
                #     text["Left"] = '7'
                # elif label_name == "lhand_8":
                #     text["Left"] = '8'
                # elif label_name == "lhand_9":
                #     text["Left"] = '9'

            action = json.dumps(text)
            print("frame_index: {}, action: {}".format(frame_index, action))
            return gesture_pb2.RecognitionReply(
                frame_index=frame_index,
                timestamp=timestamp,
                action=action
                )
        except Exception as e:
            print(f"Error in Recognition: {e}")
            traceback.print_exc()
            return gesture_pb2.RecognitionReply(
                frame_index=0,
                timestamp="",
                action=json.dumps({'Left':"",'Right':""}))


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    gesture_pb2_grpc.add_GestureRecognitionServicer_to_server(
        GestureRecognitionService(), server
    )
    # save_dir = "recognized_gestures"
    # os.makedirs(save_dir, exist_ok=True)
    server.add_insecure_port("[::]:" + settings.gRPC_port)
    server.start()
    print("Server started, listening on " + settings.gRPC_port)
    server.wait_for_termination()


if __name__ == "__main__":
    try:
        serve()  # run gRPC server
    except KeyboardInterrupt:
        print("Gesture gRPC server stop!")
