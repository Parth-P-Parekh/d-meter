#!/usr/bin/env python3
"""Single-image native SiMa worker, launched only by wood_parity_service.py."""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

PACKAGE_ROOT = Path("/data/simaai/applications/Wood_simaaisrc")
INPUT_WIDTH, INPUT_HEIGHT = 1280, 720
os.environ.setdefault("LD_LIBRARY_PATH", str(PACKAGE_ROOT / "lib"))
os.environ.setdefault("GST_PLUGIN_PATH", str(PACKAGE_ROOT / "lib"))

import gi  # noqa: E402
import cv2  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def labels() -> list[str]:
    return [line.strip() for line in (PACKAGE_ROOT / "share" / "overlay" / "labels").read_text().splitlines() if line.strip()]


def parse_buffer(data: bytes, class_labels: list[str]) -> list[dict[str, object]]:
    if len(data) < 4:
        raise ValueError("bbox tensor is shorter than the detection-count header")
    count = struct.unpack_from("<i", data, 0)[0]
    if count < 0 or count > 100 or len(data) < 4 + count * 24:
        raise ValueError(f"invalid bbox tensor: count={count}, bytes={len(data)}")
    output = []
    for index in range(count):
        offset = 4 + index * 24
        center_x, center_y, width, height = struct.unpack_from("<4I", data, offset)
        score = struct.unpack_from("<f", data, offset + 16)[0]
        class_id = struct.unpack_from("<I", data, offset + 20)[0]
        if not 0 <= score <= 1 or class_id >= len(class_labels):
            raise ValueError(f"invalid detection at index {index}")
        output.append({
            "class_name": class_labels[class_id],
            "confidence": score,
            "box_input": {
                "x": center_x - width / 2,
                "y": center_y - height / 2,
                "width": width,
                "height": height,
            },
        })
    return output


def infer(image_path: Path, timeout_seconds: float) -> list[dict[str, object]]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("image cannot be decoded as BGR pixels")
    image = image.copy(order="C")
    source_height, source_width = image.shape[:2]
    preproc = PACKAGE_ROOT / "etc" / "0_preproc.json"
    mla = PACKAGE_ROOT / "etc" / "0_process_mla.json"
    decoder = PACKAGE_ROOT / "etc" / "boxdecoder.json"
    description = (
        "appsrc name=input_source is-live=true block=true format=time do-timestamp=true ! "
        f"video/x-raw,format=BGR,width={source_width},height={source_height},framerate=30/1 ! "
        "videoconvert ! tee name=early_tee allow-not-linked=true "
        "early_tee. ! queue max-size-buffers=10 leaky=downstream ! fakesink sync=false "
        "early_tee. ! queue max-size-buffers=10 leaky=downstream ! "
        f"videoscale ! video/x-raw,width={INPUT_WIDTH},height={INPUT_HEIGHT} ! "
        "videoconvert ! video/x-raw,format=NV12 ! simaaiencoder enc-bitrate=4000 ! h264parse ! "
        "video/x-h264,stream-format=byte-stream,alignment=au ! "
        "simaaidecoder sima-allocator-type=2 name=decoder ! video/x-raw ! "
        f"simaaiprocesscvu name=simaai_preprocess num-buffers=5 config={quote(str(preproc))} ! "
        f"simaaiprocessmla name=simaai_process_mla num-buffers=5 config={quote(str(mla))} ! "
        f"simaaiboxdecode name=simaai_boxdecode config={quote(str(decoder))} ! application/vnd.simaai.tensor ! "
        "appsink name=bbox_sink emit-signals=false max-buffers=1 drop=true sync=false"
    )
    pipeline = Gst.parse_launch(description)
    source = pipeline.get_by_name("input_source")
    sink = pipeline.get_by_name("bbox_sink")
    source.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,format=BGR,width={source_width},height={source_height},framerate=30/1"
    ))
    bus = pipeline.get_bus()
    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError("pipeline could not enter PLAYING")
    deadline = time.monotonic() + timeout_seconds
    frame_duration = Gst.SECOND // 30
    frame_index = 0
    try:
        while time.monotonic() < deadline:
            buffer = Gst.Buffer.new_allocate(None, image.nbytes, None)
            buffer.fill(0, image.tobytes())
            buffer.duration = frame_duration
            frame_index += 1
            flow = source.emit("push-buffer", buffer)
            if flow != Gst.FlowReturn.OK:
                raise RuntimeError(f"appsrc rejected frame with flow result {flow.value_nick}")
            sample = sink.emit("try-pull-sample", 100 * Gst.MSECOND)
            if sample is not None:
                buffer = sample.get_buffer()
                return parse_buffer(buffer.extract_dup(0, buffer.get_size()), labels())
            message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message is not None:
                if message.type == Gst.MessageType.ERROR:
                    error, debug = message.parse_error()
                    raise RuntimeError(f"GStreamer error: {error}; {debug or 'no debug detail'}")
                raise RuntimeError("pipeline reached EOS without a bbox tensor")
            time.sleep(1 / 30)
        raise TimeoutError(f"no bbox tensor within {timeout_seconds:.1f} seconds")
    finally:
        pipeline.set_state(Gst.State.NULL)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    try:
        print(json.dumps({"detections": infer(args.image, args.timeout)}))
        return 0
    except (OSError, RuntimeError, TimeoutError, ValueError, GLib.Error) as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
