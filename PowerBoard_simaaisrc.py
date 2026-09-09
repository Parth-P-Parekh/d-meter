#!/usr/bin/env python3

import os
import struct
import glob
import gi
import subprocess
import datetime
import threading
import time
import numpy as np
import cv2

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GLib

from flask import Flask, render_template, Response, jsonify
from queue import Queue, Empty
from training_capture_spool import TrainingCaptureSpool


# ============================================================
# ENVIRONMENT
# ============================================================

os.environ['GST_DEBUG'] = '3'
os.environ['LD_LIBRARY_PATH'] = '/data/simaai/applications/Powerboard_simaaisrc/lib'
os.environ['GST_PLUGIN_PATH'] = '/data/simaai/applications/Powerboard_simaaisrc/lib'

Gst.init(None)


# ============================================================
# REPORT STORAGE
# ============================================================

REPORT_ROOT = "/tmp/reports"
FULL_DIR = f"{REPORT_ROOT}/full"
CROP_DIR = f"{REPORT_ROOT}/cropped"

os.makedirs(FULL_DIR, exist_ok=True)
os.makedirs(CROP_DIR, exist_ok=True)

HOST_USER = "Admin"
HOST_IP = "192.168.1.17"

HOST_FULL_PATH = "C:/Users/Admin/Reports/full"
HOST_CROP_PATH = "C:/Users/Admin/Reports/cropped"


def copy_to_host(frame_path, crops):

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    subprocess.Popen(
        f'scp {frame_path} {HOST_USER}@{HOST_IP}:"{HOST_FULL_PATH}/frame_{timestamp}.png"',
        shell=True
    )

    for i, crop in enumerate(crops):

        subprocess.Popen(
            f'scp {crop} {HOST_USER}@{HOST_IP}:"{HOST_CROP_PATH}/roi_{i}_{timestamp}.png"',
            shell=True
        )


# ============================================================
# APP INIT
# ============================================================

app = Flask(__name__)
training_capture_spool = TrainingCaptureSpool(os.path.basename(__file__))
training_capture_spool.register_routes(app)

CAMERA_NAME = "Basler-a2A4504-5gcBAS-40735904"

LABELS_FILE = "/data/simaai/applications/Powerboard_simaaisrc/share/overlay/labels"

PREPROC_CONFIG = "/data/simaai/applications/Powerboard_simaaisrc/etc/0_preproc.json"
MLA_CONFIG = "/data/simaai/applications/Powerboard_simaaisrc/etc/0_process_mla.json"
BOXDECODER_CONFIG = "/data/simaai/applications/Powerboard_simaaisrc/etc/boxdecoder.json"

BBOX_OUTPUT_PATH = "/tmp/bbox_output_yolov8"


# ============================================================
# LOAD LABELS
# ============================================================

labels = []

try:
    with open(LABELS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                labels.append(line)

    print("Loaded labels:", labels)

except Exception as e:
    print("Label loading error:", e)


# ============================================================
# GLOBALS
# ============================================================

pipeline_active = False
display_queue = Queue(maxsize=5)
shared_pipeline = None
latest_detections = []

# ============================================================
# PIPELINE
# ============================================================

class UnifiedGStreamerPipeline:

    def __init__(self):

        self.pipeline = None
        self.loop = None
        self.thread = None
        self.display_appsink = None

        self.last_frame = None
        self.last_frame_lock = threading.Lock()

        os.makedirs(BBOX_OUTPUT_PATH, exist_ok=True)

    def build_pipeline(self):

        pipeline_str = (

            f"aravissrc camera-name={CAMERA_NAME} name=camera_src offset-y=1648 "
            "do-timestamp=true ! "
            "video/x-raw,format=RGB,width=3200,height=2256 ! "
            "videoconvert ! "

            "tee name=early_tee allow-not-linked=true "

            # DISPLAY
            "early_tee. ! "
            "queue max-size-buffers=10 leaky=downstream ! "
            "videoconvert ! video/x-raw,format=BGR ! "
            "appsink name=display_sink emit-signals=true max-buffers=2 drop=true sync=false "

            # INFERENCE
            "early_tee. ! "
            "queue max-size-buffers=10 leaky=downstream ! "
            "videoconvert ! "

            "videoscale ! "
            "video/x-raw,width=1280,height=720 ! "

            "videoconvert ! "
            "video/x-raw,format=NV12 ! "

            "simaaiencoder enc-bitrate=4000 name=encoder ! "
            "h264parse ! "
            "video/x-h264,stream-format=byte-stream,alignment=au ! "

            "simaaidecoder sima-allocator-type=2 name=decoder ! "
            "video/x-raw ! "

            f"simaaiprocesscvu name=simaai_preprocess num-buffers=5 config={PREPROC_CONFIG} ! "
            f"simaaiprocessmla name=simaai_process_mla num-buffers=5 config={MLA_CONFIG} ! "
            f"simaaiboxdecode name=simaai_boxdecode config={BOXDECODER_CONFIG} ! "

            "application/vnd.simaai.tensor ! "
            f"multifilesink location={BBOX_OUTPUT_PATH}/bbox_%03d.bin max-files=10 sync=false"
        )

        self.pipeline = Gst.parse_launch(pipeline_str)

        self.display_appsink = self.pipeline.get_by_name('display_sink')
        self.display_appsink.connect('new-sample', self._on_new_sample)

    def _on_new_sample(self, appsink):

        sample = appsink.emit('pull-sample')

        buf = sample.get_buffer()
        caps = sample.get_caps()

        structure = caps.get_structure(0)
        width = structure.get_value('width')
        height = structure.get_value('height')

        success, map_info = buf.map(Gst.MapFlags.READ)

        if success:

            frame_data = np.ndarray(
                shape=(height, width, 3),
                dtype=np.uint8,
                buffer=map_info.data
            )

            frame = frame_data.copy()

            buf.unmap(map_info)

            with self.last_frame_lock:
                self.last_frame = frame

            if display_queue.full():
                display_queue.get_nowait()

            display_queue.put(frame)

        return Gst.FlowReturn.OK

    def start(self):

        self.build_pipeline()

        self.loop = GLib.MainLoop()

        self.pipeline.set_state(Gst.State.PLAYING)

        self.thread = threading.Thread(target=self.loop.run, daemon=True)
        self.thread.start()

        return True

    def stop(self):

        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)

        if self.loop:
            self.loop.quit()

    def get_current_frame(self):

        with self.last_frame_lock:
            if self.last_frame is not None:
                return self.last_frame.copy()

        return None


# ============================================================
# INFERENCE
# ============================================================

class InferenceProcessor:

    def wait_for_new_bbox_file(self, timeout=3):

        start = time.time()
        existing = set(glob.glob(f"{BBOX_OUTPUT_PATH}/bbox_*.bin"))

        while time.time() - start < timeout:

            current = set(glob.glob(f"{BBOX_OUTPUT_PATH}/bbox_*.bin"))
            new = current - existing

            if new:
                return sorted(list(new))[-1]

            time.sleep(0.1)

        return None

    def parse_bbox_file(self, bbox_file):

        detections = []

        with open(bbox_file, 'rb') as f:
            data = f.read()

        num = struct.unpack_from('<i', data, 0)[0]

        for i in range(num):

            offset = 4 + i * 24

            x, y, w, h = struct.unpack_from('<4I', data, offset)
            cls = struct.unpack_from('<I', data, offset + 20)[0]

            x1 = int(x - w/2)
            y1 = int(y - h/2)
            x2 = int(x + w/2)
            y2 = int(y + h/2)

            detections.append({
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "class_id": cls,
                "class_name": labels[cls] if cls < len(labels) else f"class_{cls}"
            })

        return detections


# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/camera_display', methods=['POST'])
def camera_display():

    global pipeline_active, shared_pipeline

    if pipeline_active:

        shared_pipeline.stop()
        pipeline_active = False
        return jsonify({'status': 'stopped'})

    else:

        shared_pipeline = UnifiedGStreamerPipeline()

        if shared_pipeline.start():

            pipeline_active = True
            return jsonify({'status': 'started'})

        return jsonify({'status': 'error'})


# NEW STOP PIPELINE ROUTE

@app.route('/stop_pipeline', methods=['POST'])
def stop_pipeline():

    global pipeline_active, shared_pipeline

    if not pipeline_active:
        return jsonify({'status': 'pipeline already stopped'})

    if shared_pipeline:
        shared_pipeline.stop()

    pipeline_active = False
    shared_pipeline = None

    return jsonify({'status': 'pipeline stopped'})


@app.route('/run_inference', methods=['POST'])
def run_inference():

    if not pipeline_active:
        return jsonify({'status': 'start camera first'})

    frame = shared_pipeline.get_current_frame()

    if frame is None:
        return jsonify({'status': 'no camera frame available'}), 503
    training_capture = training_capture_spool.prepare(frame)

    processor = InferenceProcessor()

    bbox_file = processor.wait_for_new_bbox_file()

    global latest_detections

    bbox_list = []

    if bbox_file:
        bbox_list = processor.parse_bbox_file(bbox_file)

    latest_detections = bbox_list 

    result_frame = frame.copy()

    frame_h, frame_w = frame.shape[:2]

    # ============================================================
    # ✅ FIX STARTS HERE (ONLY CHANGE)
    # ============================================================

    INF_W = 1280
    INF_H = 720

    # Small calibration offset (tune if needed)
    Y_OFFSET = 25
    X_OFFSET = 20

    scale_x = frame_w / INF_W
    scale_y = frame_h / INF_H

    # ============================================================
    # ✅ FIX ENDS HERE
    # ============================================================

    crop_paths = []

    frame_path = f"{FULL_DIR}/latest_frame.png"

    for det in bbox_list:

        # ========================================================
        # ✅ APPLY FINAL CORRECTION (NO PADDING)
        # ========================================================

        x1 = det['x1'] + X_OFFSET
        x2 = det['x2'] + X_OFFSET

        y1 = det['y1'] + Y_OFFSET
        y2 = det['y2'] + Y_OFFSET

        # Clamp to avoid negative values
        y1 = max(0, y1)
        y2 = max(0, y2)

        # Scale to original frame
        x1 = int(x1 * scale_x)
        x2 = int(x2 * scale_x)

        y1 = int(y1 * scale_y)
        y2 = int(y2 * scale_y)

        # ========================================================

        label = det["class_name"]

        cv2.rectangle(result_frame, (x1, y1), (x2, y2), (0,255,0), 2)

        cv2.putText(
            result_frame,
            label,
            (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0,255,0),
            2,
            cv2.LINE_AA
        )

        crop = frame[y1:y2, x1:x2]

        crop_path = f"{CROP_DIR}/latest_roi.png"

        if crop.size > 0:

            cv2.imwrite(crop_path, crop)

            crop_paths.append(crop_path)

    cv2.imwrite(frame_path, result_frame)

    copy_to_host(frame_path, crop_paths)

    capture_status, _capture_error = training_capture_spool.publish(training_capture, bbox_list)
    response = jsonify({"detections": bbox_list})
    response.headers['X-Frame-Id'] = training_capture['frame_id']
    response.headers['X-Training-Capture-Status'] = capture_status
    return response

@app.route('/video_feed/camera')
def video_feed_camera():

    def generator():

        while True:

            try:

                frame = display_queue.get(timeout=1)

                ret, buf = cv2.imencode(".png", frame)

                yield (b'--frame\r\n'
                       b'Content-Type: image/png\r\n\r\n' +
                       buf.tobytes() + b'\r\n')

            except Empty:
                continue

    return Response(generator(),
        mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/latest_frame')
def latest_frame():
    try:
        with open(f"{FULL_DIR}/latest_frame.png", "rb") as f:
            return Response(f.read(), mimetype='image/png')
    except:
        return "No image available", 404

@app.route('/get_detections', methods=['GET'])
def get_detections():

    if not latest_detections:
        return jsonify({
            "status": "no data",
            "detections": []
        })

    return jsonify({
        "status": "ok",
        "detections": latest_detections
    })


# ============================================================

if __name__ == '__main__':

    print("YOLO Dashboard Running")

    app.run(
        host='0.0.0.0',
        port=5001,
        debug=False,
        threaded=True
    )
