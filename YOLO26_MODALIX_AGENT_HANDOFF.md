# YOLO26 Modalix Experiment — Agent Handoff

## Purpose

Continue the isolated YOLO26n/Modalix integration experiment under `experiments/yolo26_modalix/`. This milestone proves that a COCO-pretrained YOLO26 model can be exported to raw ONNX, compiled for SiMa Modalix, served through an isolated uploaded-image API, and compared with a Windows FP32 reference.

This is integration validation only. It does **not** train or validate a production defect model, detect `ChipOff`, `Major`, or `Minor`, make pass/fail decisions, or integrate with cameras, PLC, MES, or SQL.

## Non-negotiable safety boundary

- Keep every Windows-side change below `experiments/yolo26_modalix/`, except this handoff document.
- Do not modify the repository’s existing production, Wood parity, or DIY pipelines.
- Do not modify files on `D:\`.
- Do not edit or run from `C:\Users\Admin\Sima_Projects\tool-model-to-pipeline`.
- A fresh converter checkout, pinned to the recorded commit, must live under `experiments/yolo26_modalix/build_workspace/vendor/`.
- Never download model weights on the Modalix board.
- Board paths are restricted to `/home/sima/yolo26-inference-demo`, `/tmp/yolo26-inference-demo`, and `/data/simaai/applications/yolo26n_coco_simaaisrc`.
- The board service may listen only on port `5004`.
- Never stop, overwrite, reconfigure, or reuse production port `5001` or DIY port `5003`.
- Do not access cameras, PLC, MES, or SQL from this experiment.
- Preserve unrelated and pre-existing worktree changes.

## Intended end-to-end pipeline

```text
uploaded image
  -> OpenCV BGR decode
  -> forced resize to 1280x720
  -> Modalix appsrc
  -> isolated encoder/decoder allocator bridge
  -> CVU RGB preprocessing and centered black 640x640 letterbox
  -> Modalix MLA INT8 YOLO26n
  -> SiMa detess/dequant
  -> appsink raw float32 tensor (1,84,8400)
  -> Python YOLO26 decode and class-aware NMS
  -> source-coordinate boxes, JSON, and overlay
```

The encoder/decoder bridge exists only because SiMa CVU requires segmented-memory buffers. The model output must never be sent through the existing YOLOv8 six-tensor box decoder.

## Required model contract

- Model version: `yolo26n-coco-ultralytics-8.4.142`.
- Ultralytics: `8.4.142`.
- Export: `imgsz=640`, `opset=13`, `dynamic=False`, `simplify=False`, `nms=None`, batch one.
- Input: `(1,3,640,640)` float32 NCHW RGB divided by 255.
- Output: one-to-many raw `(1,84,8400)`.
- Padding: centered black.
- Confidence: `0.1`.
- Class-aware NMS IoU: `0.3`.
- Maximum detections: `100`.
- Detection fields: `class_id`, `class_name`, `confidence`, `box_model`, and `box_source`.

## Already implemented

### Environment and model artifacts

- Windows venv: `experiments/yolo26_modalix/.venv/`.
- Dependency pins: `requirements-windows.txt`.
- Complete realized environment: `artifacts/model/pip-freeze.txt`.
- Export entry point: `windows/export_model.py`.
- PT and ONNX are ignored by Git but exist locally in `artifacts/model/`.
- Provenance, versions, sizes, URLs, export options, and checksums are in `artifacts/artifact-manifest.json`.
- The ONNX graph passed `onnx.checker` with the required fixed shapes.

Current artifacts:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `yolo26n.pt` | 5,544,453 bytes | `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef` |
| `yolo26n.onnx` | 9,884,025 bytes | `4e4cbfcc0ce631cfbe9e694c2d244c7887cc07b6ae23fca64fceb49be1c053fd` |
| `project.mpk` | not built | unavailable |

### Shared Python implementation

`src/yolo26_modalix/` implements:

- source -> 1280x720 -> 640x640 geometry;
- exact centered black letterboxing and inverse mapping;
- BGR-to-RGB conversion and `/255` normalization;
- raw YOLO26 decoding and class-aware NumPy NMS;
- canonical 80-class COCO mapping;
- PT and ONNX runners with identical pre/postprocessing;
- one-to-one parity comparison;
- overlay generation;
- shared Flask routes and artifact metadata.

### HTTP contract

Both reference and board services provide:

- `GET /health`
- `GET /contract`
- `POST /infer` with multipart `image` and optional `frame_id`
- `GET /runs/<run_id>/result`
- `GET /runs/<run_id>/overlay`

Results include model/pipeline versions, PT/ONNX/MPK checksums, image identity, transforms, detections, timings, and saved artifact routes. Board health intentionally fails until the MPK checksum is finalized and the isolated package configurations are installed.

### Board runtime

`board/` contains:

- `board_service.py`: isolated port-5004 service;
- `board_worker.py`: per-request GStreamer worker;
- `start_yolo26_service.sh`: guarded launcher;
- `verify_guardrails.sh`: before/after port-5001 and port-5003 PID/health comparison.

The worker accepts a readable 1280x720 BGR image, runs the allocator bridge and CVU -> MLA -> detess/dequant chain, then rejects anything other than exactly 705,600 finite little-endian float32 values.

### Build workspace

`build_workspace/` contains:

- `yolo26_modalix.yaml`: Modalix configuration with `no_box_decode: true`;
- `compile_model.py`: direct ModelSDK import, quantization, and gen2 compilation;
- `run_packaging_stage.py`: allows only pipeline-create and MPK-create from an isolated pinned converter checkout;
- `coco80.txt`: canonical labels.

Compilation is batch one, Modalix/gen2, asymmetric per-tensor INT8 activations, and symmetric per-channel INT8 weights. It requires exactly 100 calibration PNGs. Guessed or automatic graph surgery is prohibited.

If direct import/compilation fails, preserve:

```text
build_workspace/result/modalix/unsupported-operator-report.json
```

Only compiler-reported graph changes may be attempted. Every revision must pass `tools/validate_graph_revision.py` against five held-out frames before replacing the original compiler input.

### Corpus, parity, and deployment tooling

`tools/` contains:

- `prepare_corpus.py`: deterministic SHA-256 selection/staging;
- `validate_corpus.py`: checksum, count, uniqueness, and overlap gates;
- `validate_pt_onnx.py`: held-out PT/ONNX comparison;
- `validate_graph_revision.py`: ONNX checker and numerical equivalence;
- `compare_results.py`: PT or Modalix result comparison;
- `submit_evaluation.py`: submits all 25 evaluation frames;
- `benchmark_board.py`: exactly 50 requests with timings and repeatability;
- `validate_generated_pipeline.py`: rejects an active box decoder and requires one detess/dequant tensor;
- `finalize_manifest.py`: records a real MPK size and checksum;
- `deploy_board_runtime.py`: stages service files but never installs an MPK or starts/stops processes.

### Command sheets

- Overview: `experiments/yolo26_modalix/README.md`
- Windows: `experiments/yolo26_modalix/WINDOWS_COMMANDS.md`
- Ubuntu/SiMa SDK: `experiments/yolo26_modalix/UBUNTU_BUILD_COMMANDS.md`
- Modalix: `experiments/yolo26_modalix/MODALIX_COMMANDS.md`

Follow these documents instead of reconstructing commands from memory.

## Verification completed

- 19 experiment tests pass.
- All 9 pre-existing repository tests pass.
- `pip check` reports no broken isolated-environment dependencies.
- Python compile checks pass for experiment code and tools.
- Manifest hashes match the local PT and ONNX files.
- The ONNX graph passes `onnx.checker` with `(1,3,640,640) -> (1,84,8400)`.
- A real FP32 ONNX service smoke request returned valid JSON, a result route, and an overlay route.
- On repository image `2.png`, PT and ONNX each returned one `cell phone` detection:
  - IoU: `0.9999984502792358`
  - confidence delta: `0.0000013709068298339844`

The smoke image proves integration only; it is not the required held-out corpus.

No MPK was compiled, installed, or deployed. No board service was started. Existing pipelines and production services were not changed.

## Remaining work and blockers

### 1. Obtain and stage the corpus

`corpus/manifest.json` does not exist because the source images have not been supplied.

Required disjoint sets:

- 100 representative production/pipeline calibration frames;
- 20 other held-out pipeline frames;
- 5 other recorded images visibly containing recognizable COCO objects.

Run `prepare_corpus.py`, then `validate_corpus.py`. The tooling rejects smaller, duplicate, corrupt, or overlapping sets. Do not substitute synthetic or reused images.

### 2. Complete held-out PT/ONNX parity

Run `validate_pt_onnx.py` after corpus validation.

Required per frame:

- equal detection counts and class IDs;
- one-to-one IoU >= `0.99`;
- confidence difference <= `0.001`.

Any failure blocks later acceptance.

### 3. Compile in Ubuntu SiMa ModelSDK

- Mount/copy this experiment as `/home/docker/sima-cli/yolo26_modalix`.
- Clone `https://github.com/sima-ai/tool-model-to-pipeline.git` below `build_workspace/vendor/`.
- Detach at commit `c784d26a96898031c70e9c25e67aab7ac3673c6f`.
- Confirm that checkout is clean.
- Run `build_workspace/compile_model.py` from the experiment, not the converter checkout.
- Preserve an unsupported-operator report if the SDK cannot import/compile the graph.
- Never patch the installed SiMa project.

### 4. Build and validate the MPK

Inside the Palette MPK container:

- install the isolated converter checkout in an isolated venv;
- run only pipeline-create;
- validate detess/dequant and the absence of an active box decoder;
- run MPK-create;
- validate a non-empty MPK;
- stage it at `artifacts/package/yolo26n_coco_simaaisrc/project.mpk`;
- run `finalize_manifest.py`.

Never fabricate or manually enter an MPK checksum.

### 5. Deploy only the isolated application

- Run `deploy_board_runtime.py` only after MPK finalization.
- Execute every pre-deployment check in `MODALIX_COMMANDS.md`.
- Snapshot ports 5001/5003 with `verify_guardrails.sh snapshot`.
- Deploy only `yolo26n_coco_simaaisrc` using the Palette `mpk` CLI.
- Confirm `0_preproc.json`, `0_process_mla.json`, and `0_postproc.json` exist in the new package.
- Confirm board Python already has `cv2`, `flask`, `numpy`, and `gi`; if not, stop with a compatibility blocker rather than changing system Python.
- Start only the isolated service on port 5004.

### 6. Modalix parity and API validation

- Confirm `/health` and `/contract` on port 5004.
- Submit exactly 25 evaluation frames with `submit_evaluation.py`.
- Compare board INT8 results against FP32 ONNX.
- Inspect JSON and overlays.

INT8 acceptance per frame:

- equal counts and class IDs;
- one-to-one IoU >= `0.90`;
- confidence difference <= `0.05`.

### 7. Benchmark and prove isolation

- Run exactly 50 requests with `benchmark_board.py`.
- Record preprocessing, inference, accelerator-pipeline, postprocessing, and total latency.
- Require repeatable detections.
- Report measured performance without inventing a production SLA.
- Run `verify_guardrails.sh compare` afterward.
- Port-5001 and port-5003 PIDs and health HTTP status must match the original snapshot.

## Final success criteria

The experiment is complete only when:

- PT/ONNX identities remain unchanged;
- the 100/25 corpus is valid and disjoint;
- held-out PT/ONNX parity passes;
- a valid MPK exists with a recorded checksum;
- the installed application uses raw detess/dequant output;
- isolated port 5004 is healthy;
- all 25 evaluation requests produce valid JSON and overlays;
- INT8/FP32 parity passes;
- the 50-run latency/repeatability report completes;
- ports 5001 and 5003 remain unchanged;
- no production pipeline or integration is affected.

## Recommended first actions for the next agent

1. Read this handoff and all four experiment documents.
2. Inspect the worktree and preserve unrelated changes; experiment files may still be uncommitted.
3. Recheck the two ignored model binaries against the manifest.
4. Ask for the three corpus source directories if unavailable.
5. Do not compile with placeholder calibration images.
6. Run full PT/ONNX parity before entering the SiMa SDK flow.
7. Keep an evidence log for every compiler, package, deployment, parity, latency, and protected-port check.

## References and licensing

- Ultralytics 8.4.142: https://github.com/ultralytics/ultralytics/releases/tag/v8.4.142
- YOLO26 ONNX export: https://docs.ultralytics.com/integrations/onnx
- Official weights: https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt
- SiMa converter: https://github.com/sima-ai/tool-model-to-pipeline.git

Review Ultralytics code/model licensing before closed-source production use. This experiment does not resolve production licensing.
