from __future__ import annotations

MODEL_VERSION = "yolo26n-coco-ultralytics-8.4.142"
PIPELINE_NAME = "yolo26n_coco_simaaisrc"
PIPELINE_VERSION = "0.1.0"
MODEL_WIDTH = 640
MODEL_HEIGHT = 640
PIPELINE_WIDTH = 1280
PIPELINE_HEIGHT = 720
NUM_CLASSES = 80
RAW_OUTPUT_SHAPE = (1, 84, 8400)
CONFIDENCE_THRESHOLD = 0.1
NMS_IOU_THRESHOLD = 0.3
MAX_DETECTIONS = 100
PORT = 5004

COCO_NAMES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
    "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
)

