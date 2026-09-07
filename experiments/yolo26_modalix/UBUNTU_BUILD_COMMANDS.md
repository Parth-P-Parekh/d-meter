# Ubuntu / SiMa SDK command sheet

Run these yourself on the Ubuntu SDK host. Do not use or edit `C:\Users\Admin\Sima_Projects\tool-model-to-pipeline`; create the separate checkout below. Ensure the experiment is mounted in both SDK containers as `/home/docker/sima-cli/yolo26_modalix`.

## Prepare the isolated converter checkout

```bash
cd /home/docker/sima-cli/yolo26_modalix
python3 tools/validate_corpus.py
git clone https://github.com/sima-ai/tool-model-to-pipeline.git build_workspace/vendor/tool-model-to-pipeline
git -C build_workspace/vendor/tool-model-to-pipeline checkout --detach c784d26a96898031c70e9c25e67aab7ac3673c6f
git -C build_workspace/vendor/tool-model-to-pipeline status --short
```

The final command must print nothing. Do not patch that checkout to make YOLO26 compile.

## Direct gen2 import and compilation

Enter the ModelSDK container using the SDK version compatible with the board, then run:

```bash
cd /home/docker/sima-cli/yolo26_modalix
python3 build_workspace/compile_model.py \
  --onnx artifacts/model/yolo26n.onnx \
  --calibration corpus/calibration/images \
  --output build_workspace/result/modalix
```

This directly imports the raw-output graph for Modalix/gen2 and requests batch-one INT8 with asymmetric per-tensor activations and symmetric per-channel weights. If import or compilation fails, preserve `build_workspace/result/modalix/unsupported-operator-report.json`. Do not apply guessed graph surgery. For every compiler-requested revision, run:

```bash
python3 tools/validate_graph_revision.py \
  --original artifacts/model/yolo26n.onnx \
  --revised artifacts/model/yolo26n-revised.onnx \
  --images corpus/evaluation/images \
  --report artifacts/logs/graph-equivalence.json
```

Only a checker-valid, numerically equivalent revision may replace the compiler input. Stop and report an SDK/operator blocker if equivalence cannot be shown.

## Pipeline and MPK

Enter the Palette MPK container. Install the isolated checkout into an isolated environment with SDK packages visible, then run only the packaging stages:

```bash
cd /home/docker/sima-cli/yolo26_modalix
python3 -m venv --system-site-packages build_workspace/.package-venv
. build_workspace/.package-venv/bin/activate
python3 -m pip install --no-deps -e build_workspace/vendor/tool-model-to-pipeline
python3 build_workspace/run_packaging_stage.py pipelinecreate
python3 tools/validate_generated_pipeline.py --project yolo26n_coco_simaaisrc
python3 build_workspace/run_packaging_stage.py mpkcreate
python3 tools/validate_generated_pipeline.py --project yolo26n_coco_simaaisrc --require-mpk
mkdir -p artifacts/package/yolo26n_coco_simaaisrc
cp -p yolo26n_coco_simaaisrc/project.mpk artifacts/package/yolo26n_coco_simaaisrc/project.mpk
python3 tools/finalize_manifest.py --mpk artifacts/package/yolo26n_coco_simaaisrc/project.mpk
sha256sum artifacts/model/yolo26n.pt artifacts/model/yolo26n.onnx artifacts/package/yolo26n_coco_simaaisrc/project.mpk
```

The generated active GStreamer path must contain detess/dequant and must not contain `simaaiboxdecode` or `genericboxdecode`.

