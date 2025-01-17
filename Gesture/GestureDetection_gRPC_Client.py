import cv2
import copy
import base64
import grpc
import gesture_pb2
import gesture_pb2_grpc

from config import settings

def cv2_base64(image):
    base64_str = cv2.imencode('.jpg', image)[1].tostring()
    base64_str = base64.b64encode(base64_str)

    return base64_str


def run():
    frame_count = 0  # count no of frames

    if settings.source.isnumeric():
        cap = cv2.VideoCapture(int(settings.source))  # pass video to videocapture object
    else:
        cap = cv2.VideoCapture(settings.source)  # pass video to videocapture object

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.cap_img_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.cap_img_height)

    if (cap.isOpened() == False):  # check if videocapture not opened
        print('Error while trying to read video. Please check path again')
        raise SystemExit()

    else:
        frame_width = int(cap.get(3))  # get video frame width
        frame_height = int(cap.get(4))  # get video frame height

    while (cap.isOpened):  # loop until cap opened or video not complete

        print("Frame {} Processing".format(frame_count + 1))

        ret, frame = cap.read()  # get frame and success from video capture

        if ret:  # if success is true, means frame exist
            raw_image = frame  # store frame
            im0 = copy.deepcopy(frame)

            encoded_string = cv2_base64(frame)  # 將frame轉成bytearray

            # build connect with grpc server 50051 is the default port used for grpc
            channel = grpc.insecure_channel("localhost:"+settings.gRPC_port)
            stub = gesture_pb2_grpc.GestureRecognitionStub(channel)
            response = stub.Recognition(gesture_pb2.RecognitionRequest(image=encoded_string))
            print("Client received: " + response.action)

        cv2.imshow('annotated', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break


if __name__ == "__main__":
    run()
