# Standalone Production-Camera Training Capture Plan

> Superseded for normal collection. The selected implementation now pulls the
> existing production worker's unannotated `/video_feed/camera` stream from the
> Admin PC using `production_frame_capture/capture_stream.py`. The standalone
> camera owner below is retained only as historical design material and must not
> be run alongside production.

## 1. Objective

Create one standalone Python script on the SiMa board that captures clean,
unannotated training images from the production Basler camera while the
production inspection pipeline is stopped.

The operator will manually position the object, arrange and switch on the
lighting, then run the script. The script controls only the camera. It does not
communicate with or alter the light controller.

The script will:

1. verify that production is not using the camera;
2. open the production camera directly from the SiMa board;
3. configure and verify the approved camera settings;
4. acquire one image or a requested burst;
5. save lossless, unannotated images and metadata;
6. optionally copy completed artifacts to the Admin PC;
7. release the camera on success, failure, or interruption.

It will not run inference, draw boxes, operate the PLC, call MES/SQL, or start
the production application.

## 2. Production-source evidence

The production Power Board service identifies the camera as:

```text
Basler-a2A4504-5gcBAS-40735904
```

Relevant source:

```text
/home/sima/BITSNew_ui/PowerBoard_simaaisrc.py
```

- line 74 contains the exact camera identity;
- lines 135-137 open it through `aravissrc` and request RGB at 3200 x 2256;
- lines 142-146 receive an unannotated frame before inference;
- the inference, decoder, overlay, Flask, and report paths are unnecessary for
  training capture.

In this production path, the SiMa process owns the camera connection. The Admin
PC starts/stops or calls the board service, but it does not receive the GigE
camera stream first. The standalone script can therefore use the same direct
SiMa-to-camera path after the production owner is stopped.

The camera is capable of a larger sensor image, but version 1 retains the
production 3200 x 2256 view. A full-sensor or different-ROI dataset would be a
separate capture contract and might not match deployed inference geometry.

## 3. Lighting boundary

Lighting is completely manual and outside the script. The operator sets it
before capture and restores or switches it off afterward. The implementation
contains no light-controller or camera-strobe code.

## 4. Isolation and installation

Install only beneath a new board directory:

```text
/home/sima/training-capture/
    capture_training_images.py
    capture-config.json
    outbox/
    logs/
```

Permanent Admin-PC destination:

```text
C:\Users\Admin\TrainingImages\PowerBoard\<YYYY-MM-DD>\
```

Do not write beneath production directories such as:

```text
/home/sima/BITSNew_ui/
/data/simaai/applications/
/tmp/reports/
C:\Users\Admin\Reports\
```

The worker must not import or start any SiMa MLA/CVU/box-decoder component and
must not open a network listening port.

## 5. Preconditions and camera characterization

The production pipeline and every other camera viewer must be stopped before
capture. The worker will check and refuse to acquire when:

- the known production camera process is running;
- port 5001 is owned by the production service;
- the configured camera cannot be discovered;
- the camera model or serial differs from the approved identity;
- available board storage is below the configured minimum;
- the Admin destination is unavailable when `--require-copy` is requested.

The worker will not stop or restart production automatically.

Before implementing the final camera configuration, use the board's installed
Aravis tools with production stopped to record:

```text
DeviceVendorName
DeviceModelName
DeviceSerialNumber
Width, Height, OffsetX, OffsetY
PixelFormat
ExposureAuto, ExposureTime
GainAuto, Gain
AcquisitionMode
TriggerSelector, TriggerMode, TriggerSource, TriggerActivation
```

Store the approved values in `capture-config.json`. The script must set them,
read them back, print them before the first capture, and abort on a mismatch.
Exposure and gain must not be guessed from image appearance.

## 6. Camera-only implementation

Use the board's existing Python GStreamer and Aravis stack. The minimal pipeline
is:

```text
aravissrc name=camera_src
    camera-name=Basler-a2A4504-5gcBAS-40735904
    <validated acquisition/trigger/exposure/gain settings>
! video/x-raw,format=RGB,width=3200,height=2256
! appsink name=frame_sink emit-signals=false max-buffers=1 drop=true sync=false
```

There is no `tee`, display encoder, H.264 path, preprocessing, inference,
box-decoder, overlay, Flask server, or light-control adapter.

### Acquisition mode

The SiMa board's installed `aravissrc` 0.8.26 exposes neither a `trigger`
property nor a `software-trigger` action. Use the same free-running acquisition
mode as the production source: start the pipeline, discard a short stabilization
window, then pull one current sample for each requested capture. Record
`acquisition_mode: free_running` in metadata. Do not claim that this is a camera
hardware trigger.

### Capture sequence

1. Validate arguments and create the dated local outbox.
2. Run production-owner, camera-identity, storage, and destination checks.
3. Build the camera pipeline and move it to `PLAYING`.
4. Apply and read back ROI, pixel format, exposure, gain, and trigger settings.
5. Discard configured warm-up frames if required by the approved mode.
6. Request the next free-running sample.
7. Pull exactly one sample with a bounded timeout.
8. Copy RGB bytes out of the GStreamer buffer before releasing it.
9. Validate width, height, stride, channels, and total byte count.
10. Convert RGB to BGR only for OpenCV encoding.
11. Write a lossless PNG, JSON sidecar, and SHA-256 checksum atomically.
12. Repeat for the requested count using the configured interval.
13. Optionally copy completed artifacts to the Admin PC and verify remote hashes.
14. Set the pipeline to `NULL` in a `finally` block and release the camera.

## 7. Command-line contract

```bash
python3 /home/sima/training-capture/capture_training_images.py \
  --part-id PB-000123 \
  --count 5 \
  --interval-ms 500 \
  --require-copy
```

Defaults:

```text
--count             1
--interval-ms       500
--timeout-seconds   10
--config            /home/sima/training-capture/capture-config.json
--local-root        /home/sima/training-capture/outbox
--admin-host        192.168.1.17
--admin-user        Admin
--admin-root        C:/Users/Admin/TrainingImages/PowerBoard
```

`--part-id` is required and restricted to safe filename characters. Every name
contains a UTC timestamp with microseconds and a random capture ID, so reruns
cannot overwrite previous images.

Example:

```text
PB-000123_20260907T101530.123456Z_a81f09c2.png
PB-000123_20260907T101530.123456Z_a81f09c2.json
PB-000123_20260907T101530.123456Z_a81f09c2.sha256
```

## 8. Metadata

Each JSON sidecar will include:

```json
{
  "schema_version": "1.0",
  "part_id": "PB-000123",
  "capture_id": "a81f09c2...",
  "received_utc": "2026-09-07T10:15:30.123456Z",
  "camera_name": "Basler-a2A4504-5gcBAS-40735904",
  "camera_model": "a2A4504-5gcBAS",
  "camera_serial": "40735904",
  "width": 3200,
  "height": 2256,
  "pixel_format": "RGB8",
  "offset_x": 656,
  "offset_y": 1648,
  "exposure_us": 100000.0,
  "gain": 0.0,
  "acquisition_mode": "free_running",
  "image_sha256": "...",
  "copy_status": "verified"
}
```

Exposure, gain, offsets, and acquisition mode are actual readbacks, not
placeholders. `received_utc` is the board receipt time unless validated camera
chunk timestamps are deliberately enabled.

## 9. Persistence and failure behavior

- Write artifacts to temporary names, flush them, then rename atomically.
- Never overwrite an existing capture.
- Keep the board copy until the Admin-PC copy and hash are verified.
- Retain local artifacts after transfer failure.
- Treat timeout, malformed size/stride, corrupt encoding, duplicate frame, or
  camera-setting mismatch as capture failure.
- Return nonzero unless every requested image was captured and every required
  copy was verified.
- Release the camera on normal exit, timeout, Ctrl+C, and exceptions.

## 10. Manual operating procedure

1. Put the machine in its approved safe/manual state.
2. Stop the production inspection/camera owner and verify it is stopped.
3. Position the object under the camera.
4. Manually configure and switch on the desired lighting; let it stabilize.
5. Run the standalone capture command.
6. Verify the requested number of clean images and metadata files.
7. Repeat positioning and capture as required.
8. Finish the script and verify the camera has been released.
9. Manually switch off or restore the lighting.
10. Restore production and perform its normal camera/health check.

## 11. Implementation and validation phases

### Phase 1: read-only characterization

- Confirm the active production process and direct SiMa camera path.
- Record the installed `aravissrc` version, supported caps, and trigger support.
- Record camera identity, ROI, format, exposure, gain, and acquisition settings.
- Record that the installed 0.8.26 plugin lacks software-trigger support and
  validate free-running capture on the installed stack.

### Phase 2: offline implementation

- Add the standalone script, configuration example, deployment helper, and unit
  tests under a new repository directory.
- Keep GStreamer access behind a small camera adapter.
- Unit-test argument validation, naming, metadata, atomic storage, checksums,
  timeout handling, and transfer behavior without camera hardware.
- Include no light-controller implementation or dependency.

### Phase 3: controlled board test

- Deploy only to `/home/sima/training-capture`.
- Capture one image locally and confirm it is unannotated and 3200 x 2256.
- Capture five images and verify unique content, names, metadata, and hashes.
- Inject timeout and Ctrl+C failures and confirm the camera becomes available
  immediately afterward.
- Enable Admin transfer and verify local and remote hashes match.

### Phase 4: collection handoff

- Provide one operator command and a short checklist.
- Collect representative good and defective parts with consistently documented
  manual lighting.
- Back up the dataset before labelling or training.

## 12. Acceptance criteria

1. The script does not start production inspection, inference, PLC, MES, SQL,
   Flask, or light-control components.
2. It refuses to run while the production camera owner is active.
3. It validates camera model and serial before acquisition.
4. It sets and reads back the approved camera configuration.
5. Every PNG is unannotated and exactly 3200 x 2256 RGB-derived pixels.
6. Each requested capture produces exactly one distinct image.
7. Metadata contains actual camera settings and acquisition mode.
8. A requested burst yields the exact count with unique names and hashes.
9. Required Admin-PC copies are permanent and hash-identical.
10. Transfer failure retains a recoverable board copy and returns failure.
11. The camera is released after success, timeout, Ctrl+C, and exceptions.
12. The code performs no automatic light operation of any kind.
13. Production files and package checksums remain unchanged.
14. Production can be restored and passes its normal camera check.

## 13. Explicit non-goals

- Running inference or drawing boxes.
- Calling production `/run_inference`, `/camera_display`, or video-feed routes.
- Operating the conveyor, PLC, MES, database, or LabVIEW Continue action.
- Capturing while production owns the camera.
- Automatically stopping or restarting production.
- Controlling, reading, switching, or synchronizing the light source.
- Calling the PNG output "sensor raw." It is an unannotated, losslessly encoded
  RGB image; Bayer sensor dumps would be a different acquisition contract.
