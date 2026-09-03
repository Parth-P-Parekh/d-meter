# DIY Board Inference Demo

The primary project is a clean-room, shadow-only inference demo that does not use an MPK, any SiMa application package, SiMa GStreamer plugins, a camera, PLC, SQL Server, MES, or production service. Its board runtime is documented in `DIY_PIPELINE.md`.

## Current state

The DIY runtime is staged at `/home/sima/diy-inference-demo` and listens on port `5003`. It accepts a recorded image, performs a fully explicit preprocessing/detection path, returns JSON, and saves an overlay under its own `/tmp/diy-inference-demo` directory.

The older Wood/MPK parity files are retained as reference material only. Their service is stopped and is not part of the DIY workflow.

## DIY workflow

1. Follow the Windows commands in `WINDOWS_QUICKSTART.md`.

2. Confirm the isolated board service:

   ```powershell
   curl http://192.168.1.20:5003/health
   ```

3. Submit a recorded image from Windows PowerShell:

   ```powershell
   python tools\run_diy_image.py --image "C:\path\to\image.png" --frame-id demo-001
   ```

4. Open `results\diy\demo-001.png` to inspect the mapped detection box.

5. Tune and learn from `detect_candidates()` in `diy_runtime/diy_inference_service.py`. Preserve the surrounding upload, transform, JSON, and overlay contract when replacing this temporary detector with a real inference engine.

## Future native MPK parity work

The native MPK build is intentionally blocked until the following assets are staged under `assets/wood/`:

- post-surgery ONNX model;
- production calibration images;
- class-label file;
- known-good MPK and generated production configuration;
- `bundle-manifest.json` with SHA-256 hashes.

The SiMa SDK/project referenced in the system notes is not available on this workstation, so no vendor MPK syntax has been invented here. `tools/build_native_mpk.py` is a guarded build gateway: it validates the staged bundle, then runs the vendor command supplied explicitly through `--build-command`.

## Archived MPK parity workflow

1. Copy model inputs into `assets/wood/` and complete `assets/wood/bundle-manifest.example.json` as `bundle-manifest.json`.
2. Validate all staged files and checksums:

   ```powershell
   python tools/validate_staged_bundle.py --bundle assets/wood
   ```

3. Create `data/corpus/manifest.json` from the supplied example. Each image has an immutable production-baseline result JSON.
4. Copy the MPK parity runtime to an isolated board directory using `tools/deploy_board_runtime.py` only when that separate evaluation is resumed.
5. Submit one recorded image at a time to the board service. Save each returned JSON as `results/demo/<frame_id>.json`.

   ```powershell
   python tools/submit_corpus.py --manifest data/corpus/manifest.json --output results/demo
   ```
5. Produce comparisons and overlays:

   ```powershell
   python tools/run_parity.py --manifest data/corpus/manifest.json --demo-results results/demo --output results/parity
   ```

7. Run the unit tests:

   ```powershell
   python -m unittest discover -s tests -v
   ```

## Acceptance rule

Each frame must have the same number of detections as its production baseline. Every detection must have the same class and match exactly one baseline detection with IoU at least `0.90` and confidence difference no greater than `0.05`.

The result is detection parity only. No recipe-region evaluation or pass/fail decision is calculated in this phase.

The JSON handoff format is shown in `data/corpus/result.example.json`. The board service accepts a recorded source image, reproduces the existing wrapper's forced 1280 x 720 inference-input resize, and records both that resize and the 640 x 640 model letterbox transform.

## Safety boundary

All generated files remain under this repository. Do not deploy, activate, stop, or overwrite a board pipeline using this project. Board installation is a separate future change requiring explicit authorization and an isolated target directory.

See `DIY_PIPELINE.md` for the active DIY service. `BOARD_DEMO.md` documents the retired package-dependent experiment and its recorded-input blocker.
