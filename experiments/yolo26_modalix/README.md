# Isolated YOLO26n Modalix Pipeline

This experiment proves COCO-pretrained YOLO26 integration mechanics only. It does not inspect `ChipOff`, `Major`, or `Minor`, make a defect decision, read cameras, or contact PLC, MES, or SQL systems. Everything created on Windows is below this directory. The board HTTP service is fixed to `/home/sima/yolo26-inference-demo`, `/tmp/yolo26-inference-demo`, and port `5004`.

## Current evidence

- The official `yolo26n.pt` was downloaded on Windows from the Ultralytics assets release and exported with Ultralytics `8.4.142`.
- The checked ONNX graph has fixed input `(1,3,640,640)`, one-to-many raw output `(1,84,8400)`, opset 13, batch one, `dynamic=False`, `simplify=False`, and `nms=None`.
- The shared CPU decoder implements confidence `0.1`, class-aware NMS IoU `0.3`, and a maximum of 100 detections.
- A local recorded image produced one matching PT/ONNX COCO detection with IoU `0.99999845` and confidence delta `0.00000137`.
- Unit tests cover letterboxing, inverse mapping, decoding, class names, NMS, empty tensors, raw board buffers, upload failures, corrupt images, saved routes, and parity rejection.

The MPK and its checksum are intentionally unfinished. Compilation cannot begin until 100 representative calibration frames and 25 distinct held-out evaluation images are supplied. A valid Modalix SDK run and physical-board testing are also still required; no MPK or service has been deployed by this implementation.

## Layout

```text
artifacts/                 manifest plus ignored PT/ONNX/MPK binaries
board/                     raw detess/dequant worker and port-5004 service
build_workspace/           isolated gen2 compile and no-box-decode configuration
corpus/                    ignored calibration/evaluation pixels and hashed manifest
src/yolo26_modalix/        common geometry, preprocessing, decoding, API, and parity
tests/                     hardware-independent tests
tools/                     corpus, graph, build, staging, parity, and benchmark tools
windows/                   export, CPU service, and upload client
```

Use [WINDOWS_COMMANDS.md](WINDOWS_COMMANDS.md), then [UBUNTU_BUILD_COMMANDS.md](UBUNTU_BUILD_COMMANDS.md), then [MODALIX_COMMANDS.md](MODALIX_COMMANDS.md). Do not skip the corpus, graph-equivalence, generated-pipeline, checksum, or protected-port gates.

## Contracts

The source image is decoded with OpenCV, forcibly resized to the established 1280×720 pipeline input, converted to RGB, centered in a black 640×640 letterbox, transposed to NCHW float32, and divided by 255. Modalix uses the matching CVU configuration. Its appsrc path uses an isolated encoder/decoder bridge solely to obtain the segmented memory allocator required by CVU, then runs CVU → MLA → detess/dequant → appsink. Python decodes raw `xywh + 80 class scores`; no YOLOv8 box decoder is permitted.

The upload service provides `GET /health`, `GET /contract`, `POST /infer` (`image`, optional `frame_id`), `GET /runs/<run_id>/result`, and `GET /runs/<run_id>/overlay`. Each detection contains `class_id`, `class_name`, `confidence`, `box_model`, and `box_source`. Health and contract expose PT, ONNX, and MPK checksums; board health returns 503 until all three are finalized.

## Acceptance

PT versus FP32 ONNX must have equal counts/classes, one-to-one IoU at least `0.99`, and confidence delta at most `0.001`. Modalix INT8 versus FP32 ONNX must have equal counts/classes, one-to-one IoU at least `0.90`, and confidence delta at most `0.05`. Evaluation requires 20 held-out pipeline frames plus five user-asserted recognizable-COCO recordings, none used for calibration.

Board acceptance additionally requires a valid MPK, healthy port 5004, JSON and overlays, 50 repeated timed requests, repeatable parity, and identical port-5001/5003 listener PIDs and health status before and after testing. Latency is reported as measured; this experiment defines no production SLA.

Ultralytics code and models are distributed under AGPL-3.0 and Enterprise licensing options. Review licensing before any closed-source or production use.
