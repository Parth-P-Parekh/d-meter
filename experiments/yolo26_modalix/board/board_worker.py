#!/usr/bin/env python3
"""One uploaded 1280x720 frame through CVU -> MLA -> detess/dequant -> appsink."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

PACKAGE_ROOT = Path(os.environ.get("YOLO26_PACKAGE_ROOT", "/data/simaai/applications/yolo26n_coco_simaaisrc"))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
os.environ.setdefault("LD_LIBRARY_PATH", str(PACKAGE_ROOT / "lib"))
os.environ.setdefault("GST_PLUGIN_PATH", str(PACKAGE_ROOT / "lib"))

import cv2  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402
from yolo26_modalix.raw_tensor import parse_raw_float32  # noqa: E402

Gst.init(None)


def quote(value: Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_raw_buffer(data: bytes) -> np.ndarray:
    return parse_raw_float32(data)


def infer(image_path: Path, timeout: float) -> tuple[np.ndarray, float]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (720, 1280):
        raise ValueError("worker input must be a readable 1280x720 BGR image")
    image = np.ascontiguousarray(image)
    preproc = PACKAGE_ROOT / "etc" / "0_preproc.json"
    mla = PACKAGE_ROOT / "etc" / "0_process_mla.json"
    postproc = PACKAGE_ROOT / "etc" / "0_postproc.json"
    for path in (preproc, mla, postproc):
        if not path.is_file():
            raise FileNotFoundError(f"required package configuration is missing: {path}")
    description = (
        "appsrc name=input_source is-live=true block=true format=time do-timestamp=true ! "
        "video/x-raw,format=BGR,width=1280,height=720,framerate=30/1 ! "
        "videoconvert ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 ! "
        "simaaiencoder enc-bitrate=4000 ! h264parse ! "
        "video/x-h264,stream-format=byte-stream,alignment=au ! "
        "simaaidecoder sima-allocator-type=2 name=decoder next-element=CVU ! video/x-raw ! "
        f"simaaiprocesscvu name=simaai_preprocess num-buffers=1 config={quote(preproc)} ! "
        f"simaaiprocessmla multi-pipeline=true name=simaai_process_mla num-buffers=1 config={quote(mla)} ! "
        f"simaaiprocesscvu name=simaai_detessdequant num-buffers=1 config={quote(postproc)} ! "
        "application/vnd.simaai.tensor ! appsink name=raw_sink emit-signals=false max-buffers=1 drop=false sync=false"
    )
    pipeline = Gst.parse_launch(description)
    source = pipeline.get_by_name("input_source")
    sink = pipeline.get_by_name("raw_sink")
    bus = pipeline.get_bus()
    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError("YOLO26 GStreamer pipeline could not enter PLAYING")
    started = time.perf_counter()
    try:
        deadline = time.monotonic() + timeout
        frame_duration = Gst.SECOND // 30
        while time.monotonic() < deadline:
            # The encoder/decoder bridge gives CVU the segmented allocator it
            # requires. Repeat the same uploaded frame until its first tensor
            # arrives, then destroy the entire per-request pipeline.
            buffer = Gst.Buffer.new_allocate(None, image.nbytes, None)
            buffer.fill(0, image.tobytes())
            buffer.duration = frame_duration
            flow = source.emit("push-buffer", buffer)
            if flow != Gst.FlowReturn.OK:
                raise RuntimeError(f"appsrc rejected the frame: {flow.value_nick}")
            sample = sink.emit("try-pull-sample", 100 * Gst.MSECOND)
            if sample is not None:
                output_buffer = sample.get_buffer()
                raw = parse_raw_buffer(output_buffer.extract_dup(0, output_buffer.get_size()))
                return raw, (time.perf_counter() - started) * 1000
            message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message is not None and message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                raise RuntimeError(f"GStreamer error: {error}; {debug or 'no debug detail'}")
        raise TimeoutError(f"no raw YOLO26 tensor received within {timeout:.1f} seconds")
    finally:
        pipeline.set_state(Gst.State.NULL)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    try:
        raw, inference_ms = infer(args.image, args.timeout)
        args.raw_output.parent.mkdir(parents=True, exist_ok=True)
        raw.astype("<f4", copy=False).tofile(args.raw_output)
        print(json.dumps({"raw_output": str(args.raw_output), "inference_ms": inference_ms, "shape": list(raw.shape)}))
        return 0
    except (OSError, RuntimeError, TimeoutError, ValueError, GLib.Error) as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
