# Active SiMa baseline — 2026-09-03

No production source, configuration, process, or PLC state was changed for this
measurement.  The only active operations were 30 existing `POST /run_inference`
calls and read-only SSH inspection of the running process and its bbox-output
directory.

## Active runtime confirmed

```text
python3 /home/sima/BITSNew_ui/PowerBoard_simaaisrc.py
```

The process continuously writes tensors under `/tmp/bbox_output_yolov8`.  The
local source's pipeline is direct camera -> SiMa encoder/decoder -> CVU
preprocessing -> MLA -> box decoder -> `multifilesink`.

## Admin PC -> SiMa API timing

The raw calls are saved in [sima-api-20260903-162408.json](sima-api-20260903-162408.json).

| Statistic | HTTP POST `/run_inference` round trip |
| --- | ---: |
| Calls | 30 / 30 HTTP 200 |
| Minimum | 563.802 ms |
| Median | 685.304 ms |
| Mean | 991.647 ms |
| P95 | 2,189.626 ms |
| P99 | 2,379.584 ms |
| Maximum | 2,444.465 ms |

All responses carried zero detections during this batch.  This metric is not
MLA-only inference time.  The active handler waits for the next bbox file,
parses it, writes annotated JPEG reports, starts asynchronous SCP transfers,
and then sends its HTTP response.  It has no per-frame request/result ID.

## Board tensor-output cadence

A ten-second, read-only directory sample observed the latest tensor file number
change from `58079` to `58123` over 9.081 seconds: 44 tensors, or 4.845 tensors
per second.  Per one-second intervals were +4, +5, +5, +5, +11, +0, +4, +5, and
+5 files.  The zero interval followed by +11 confirms that output cadence is
not uniform during this sample.

## Validity limits

- The board clock was approximately five months behind the Admin-PC clock when
  checked.  Cross-machine timestamp subtraction is invalid until clocks are
  synchronised and their offset uncertainty is recorded.
- Existing code does not expose CVU, MLA, or box-decoder timestamps, so this
  baseline cannot split preprocessing from accelerator inference.
- The current API does not correlate a requested frame with its bbox output;
  do not use the API timing as a product-specific inspection-cycle time.
