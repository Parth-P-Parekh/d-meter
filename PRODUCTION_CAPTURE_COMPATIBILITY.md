# Production frame-capture compatibility record

## Production files inspected read-only

No production file was modified.

| Source | Observed camera frame | Stream part |
| --- | --- | --- |
| `D:\Source Code\Inspection_Web\AOI\Resources\PY Files\BITS_ui\BITS_ui\PowerBoard_Screws.py` | RGB 2000×2000, converted to BGR in `display_sink` | `/video_feed/camera`, JPEG |
| `/home/sima/BITSNew_ui/PowerBoard_simaaisrc.py` on `sima@192.168.1.20` | RGB 3200×2256, converted to BGR in `display_sink` | `/video_feed/camera`, PNG |

The local production Python file had SHA-256
`5ae242b0b855ed2b01013083e9f9ccf2ff2c1fd080f6835b66282b8803a16df0`
when inspected on 2026-09-07.

The board production Python file had SHA-256
`e5ab4880391c70b5745bdc1609b9c2911029f8aeb3c9eb193e483ab8f65d401f`
when inspected read-only on the same date.

Both workers place a copied, unannotated BGR frame in `display_queue` before any
box drawing. Their `/latest_frame` route is unsuitable because it reads the
annotated report written by `/run_inference`.

## Selected exact-inference-frame integration

`production_frame_capture/capture_inference.py` runs on the Admin PC. It:

- calls `POST /run_inference`;
- requires the production response's `X-Frame-Id` header;
- fetches `GET /source_frame/<frame-id>` and verifies the returned ID;
- accepts both known JPEG and PNG production variants;
- validates encoded dimensions;
- preserves the received bytes without recompression;
- creates unique UTC/random-ID names and refuses overwrites;
- writes the image and SHA-256 JSON metadata atomically on the Admin PC.

## Required production diff (not applied)

Exact unified diffs are stored at:

- `production_changes/PowerBoard_Screws.py.diff` for the 2000×2000 JPEG worker;
- `production_changes/PowerBoard_simaaisrc.py.diff` for the 3200×2256 PNG worker.

No production file has been edited. The diffs add a bounded two-frame raw-array
cache, attach a unique lowercase hexadecimal `X-Frame-Id` header to the existing
inference response, and add an exact-frame GET route. The existing inference JSON
body remains `{"detections": [...]}` for LabVIEW compatibility. Existing overlay,
crop, SCP, camera, inference, and detection behavior remains unchanged.

The cache retains the already-copied `frame` array and performs lossless PNG
encoding only when the Admin PC fetches it. This keeps encoding out of the
existing bbox-wait/overlay path. PNG is deliberate for both workers: the exact
in-memory BGR pixels survive transfer without JPEG loss, even when the worker's
display/report path uses JPEG. Two 3200×2256 BGR frames require at most about
43.3 MB before Python/container overhead.

## Why the production diff is necessary

`display_queue` is a Python `Queue`, and every `/video_feed/camera` client calls
`get()`. Two simultaneous clients therefore divide frames instead of each
receiving every frame. The stream also has no frame ID tying a preview frame to
the snapshot obtained by `/run_inference`.

The unmodified production contract exposes only an uncorrelated consuming display
queue and an annotated `/latest_frame`. Neither can prove that a downloaded clean
image is the exact `frame` snapshot read by `/run_inference`. The documented diff
is therefore required for the selected exact-frame requirement.

## Meaning of “exact frame” in the current production architecture

The captured artifact is exactly the unannotated `frame` returned by
`shared_pipeline.get_current_frame()` inside the corresponding
`/run_inference` request. This is the array currently copied to `result_frame`
for drawing and used as the source of ROI crops.

The production GStreamer inference branch runs continuously in parallel.
`/run_inference` does not submit this array to the accelerator; it snapshots the
display branch and then waits for a new bbox file. Consequently, the existing
production code itself does not prove that this display snapshot and the bbox
tensor came from the same camera buffer. Achieving that stronger, accelerator-
correlated guarantee would require a larger production pipeline correlation
change and is outside these documented additive routes.

## Verification completed without changing production

- All repository unit tests pass, including exact frame-ID matching, missing-ID
  rejection, mismatched-ID rejection, PNG/JPEG validation, atomic persistence,
  and overwrite refusal.
- Both documented diffs passed a dry-run against their respective untouched
  sources.
- A patched candidate generated in memory from the local production source
  passed Python syntax compilation.
- Neither production source hash changed during this work.
