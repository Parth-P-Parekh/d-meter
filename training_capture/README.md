# Camera-only training capture

> This camera-owning utility is superseded by
> `production_frame_capture/capture_stream.py`, which reuses the production
> worker's unannotated frames and does not compete for the Basler camera.

This directory contains an isolated SiMa-board utility that opens the production
Basler camera, saves unannotated PNG images, and then releases the camera. It has
no inference, web server, PLC, MES, SQL, or light-controller integration.

Do not run it until the production camera owner and every camera viewer have
been stopped. Lighting is entirely manual.

## Before first capture

On the board, verify the installed camera stack and characterize the live camera
without modifying production files:

```bash
gst-inspect-1.0 aravissrc
arv-tool-0.8
```

Confirm the exact model/serial, ROI, pixel format, exposure, and gain. The live
board currently has `aravissrc` 0.8.26 without software-trigger support, so the
provided configuration deliberately uses the same free-running mode as the
production source. The live production camera is
`Basler-a2A4504-5gcBAS-40735904` and its source contract is RGB at 3200 x 2256.
Live characterization reported 100000 microseconds exposure and 0 dB gain, so
the provided configuration pins those values for reproducibility.

## Run

```bash
python3 /home/sima/training-capture/capture_training_images.py \
  --part-id PB-000123 \
  --count 5 \
  --interval-ms 500
```

Add `--require-copy` only after key-based SSH/SCP to the configured Admin-PC
destination has been tested. A failed copy leaves the board artifacts intact.

The deployment helper stages files but deliberately starts nothing:

```powershell
python tools/deploy_training_capture.py
```
