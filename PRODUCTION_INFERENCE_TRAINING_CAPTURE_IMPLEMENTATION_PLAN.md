# Automatic Production-Inference Training Capture — Implementation Plan

## 1. Objective

Every successful inspection initiated by the existing LabVIEW HMI should also
produce one permanent, unannotated training-candidate image on the Admin PC.

The image must be the exact `frame` array obtained inside that request's
`/run_inference` handler before rectangles or labels are drawn. LabVIEW remains
the owner of door, lighting, and inspection sequencing. The collector is a
separate Admin-PC process and never opens the camera.

```text
LabVIEW HMI on Admin PC
    -> POST /run_inference
SiMa production worker
    -> snapshot exact unannotated frame
    -> run existing detection/report path unchanged
    -> stage lossless PNG + metadata under unique frame_id
    -> return existing LabVIEW JSON unchanged
Admin-PC collector (separate process)
    -> poll pending-frame index
    -> download image + metadata
    -> verify frame_id, byte count, dimensions, and SHA-256
    -> save permanent image/sidecar atomically
    -> acknowledge verified storage
SiMa production worker
    -> delete only the acknowledged temporary spool entry
```

These are lossless, unannotated RGB-derived images, not Bayer sensor dumps and
not automatically labelled ground truth.

## 2. Confirmed production behavior

Two production-source variants were inspected read-only:

| Worker | Source frame | Existing display stream | Inspected SHA-256 |
| --- | --- | --- | --- |
| `PowerBoard_Screws.py` in the LabVIEW repository | 2000×2000 BGR copy | JPEG | `5ae242b0b855ed2b01013083e9f9ccf2ff2c1fd080f6835b66282b8803a16df0` |
| `/home/sima/BITSNew_ui/PowerBoard_simaaisrc.py` | 3200×2256 BGR copy | PNG | `e5ab4880391c70b5745bdc1609b9c2911029f8aeb3c9eb193e483ab8f65d401f` |

Both workers do the following in `/run_inference`:

1. Call `shared_pipeline.get_current_frame()`.
2. Copy that unannotated array to `result_frame`.
3. Draw detections only on `result_frame`.
4. Crop detections from the original unannotated `frame`.
5. Write and transfer an annotated `latest_frame` report.

The existing `/video_feed/camera` is not suitable for automatic training
capture: it consumes a shared queue, has no inference frame ID, and cannot prove
which preview belongs to a particular `/run_inference` request.

The GStreamer inference branch also runs continuously. The selected artifact is
exactly the handler's overlay/crop source frame; the existing production design
does not prove that it is the same camera buffer that generated the newly found
bbox file. That stronger accelerator correlation is a separate pipeline change.

## 3. Fixed decisions

| Decision | Implementation |
| --- | --- |
| Inspection controller | Existing LabVIEW HMI, unchanged |
| Camera owner | Existing SiMa production pipeline only |
| Capture trigger | Every successful LabVIEW-triggered `/run_inference` |
| Collector | Separate long-running process on Admin PC |
| Image | Exact pre-annotation handler frame, lossless PNG |
| Permanent storage | Admin PC only |
| Board storage | Temporary acknowledged spool |
| Discovery | Collector polls a pending-frame JSON endpoint |
| Deletion | Board deletes only after checksum-matched acknowledgement |
| Existing detection JSON | Byte-compatible JSON structure; no required fields added |
| LabVIEW changes | None |
| Existing reports/crops/SCP | Preserved during the first rollout |
| Dataset deletion | Never automatic on Admin PC |

## 4. Safety and compatibility boundaries

- Do not edit either production source in place during implementation.
- Develop and test changes against a candidate copy.
- Preserve the original source hash, timestamped backup, and exact textual diff.
- Do not change camera, preprocessing, model, bbox parsing, overlay, crop, PLC,
  MES, SQL, door, lighting, or LabVIEW behavior.
- Do not make training capture part of the PLC pass/fail decision.
- Never silently discard an unacknowledged frame.
- Do not overwrite an existing Admin-PC image or metadata file.
- Do not deploy or restart production without the machine owner's maintenance
  procedure and rollback test.

## 5. Production-worker changes to prepare as an exact diff

The production change is additive and must be prepared independently for the
confirmed authoritative worker.

### 5.1 Capture record

At the start of `/run_inference`, immediately after validating that `frame` is
not `None`, create:

```text
frame_id             32 lowercase UUID4 hexadecimal characters
captured_utc         timezone-aware UTC timestamp
source_width         frame.shape[1]
source_height        frame.shape[0]
pixel_format         BGR8
source_frame         existing unannotated frame reference
```

The handler and background capture code must treat `source_frame` as immutable.
Current production code reads it but does not modify it; annotations remain on
`result_frame = frame.copy()`.

### 5.2 When an entry becomes publishable

Publish the entry only after the existing detection, annotated report, crop,
and `copy_to_host()` logic has completed. This lets the sidecar include the
request's returned detection JSON and prevents a collector from treating a
failed handler as a completed inspection capture.

Before returning the existing JSON:

1. Losslessly encode `source_frame` as PNG.
2. Calculate SHA-256 over the exact PNG bytes.
3. Write PNG and JSON sidecar to temporary names.
4. Flush both files and atomically rename them into the ready spool.
5. Add `X-Frame-Id` and `X-Training-Capture-Status: ready` response headers.
6. Return the existing `{"detections": [...]}` body unchanged.

Response headers are diagnostic; the collector discovers captures from the
pending index because LabVIEW is not being changed to forward headers.

### 5.3 Temporary spool

Use an isolated directory, initially:

```text
/tmp/production-training-capture/
    ready/
        <frame_id>.png
        <frame_id>.json
    staging/
    events.jsonl
```

`/tmp` avoids modifying the installed MPK or source directories. It does not
survive a board reboot; this is acceptable only because inference cannot run
without the Admin PC, the collector starts before collection, and normal
acknowledgement should happen immediately. The rollout test must still prove
recovery when the collector is restarted.

Set explicit limits after measuring real PNG size and inspection rate. Initial
validation values:

```text
maximum pending frames: 32
maximum pending bytes: 1 GiB
warning age: 30 seconds
critical age: 120 seconds
```

Never evict the oldest entry to admit a new one. A full spool must create a
visible capture fault event and `X-Training-Capture-Status: spool-full`. The
inspection response remains unchanged during shadow rollout; promotion to a
fail-closed capture gate requires a separate owner decision after timing and
reliability evidence exists.

### 5.4 Additive HTTP contract

Add the following routes to the production Flask worker:

| Route | Method | Purpose |
| --- | --- | --- |
| `/training_capture/health` | GET | Capture status, counts, bytes, oldest age, last error |
| `/training_frames` | GET | Ordered metadata for all ready, unacknowledged frames |
| `/training_frames/<frame_id>.png` | GET | Exact lossless unannotated frame |
| `/training_frames/<frame_id>` | GET | Exact immutable JSON sidecar |
| `/training_frames/<frame_id>/ack` | POST | Delete spool pair only after digest verification |

The acknowledgement body is:

```json
{
  "image_sha256": "64 lowercase hexadecimal characters"
}
```

The board compares it with the staged sidecar under a lock. Unknown IDs return
`404`; malformed IDs or hashes return `400`; a digest mismatch returns `409` and
retains the entry. A repeated acknowledgement for an already acknowledged ID
should return an idempotent success record while it remains in a small recent-ID
cache.

### 5.5 Sidecar schema

Use schema `production-training-frame-1.0`:

```json
{
  "schema_version": "production-training-frame-1.0",
  "frame_id": "0123456789abcdef0123456789abcdef",
  "captured_utc": "2026-09-07T10:15:30.123456Z",
  "pipeline": "PowerBoard_simaaisrc.py",
  "image": {
    "file": "0123456789abcdef0123456789abcdef.png",
    "media_type": "image/png",
    "width": 3200,
    "height": 2256,
    "pixel_format": "BGR8",
    "sha256": "...",
    "bytes": 12345678
  },
  "detections": [],
  "training_label_status": "unannotated"
}
```

Do not infer pass/fail, product, defect truth, recipe, door state, or lighting
state from unavailable data. Those fields may be added only when an authoritative
source is identified. A collector session name supplied by the operator can be
stored separately on the Admin PC.

## 6. Separate Admin-PC collector

Replace the current trigger-based `capture_inference.py` workflow with a watcher
that never calls `/run_inference`.

Add:

```text
production_frame_capture/
    collector.py
    config.example.json
tools/
    run_production_collector.ps1
    install_production_collector_task.ps1
tests/
    test_production_collector.py
```

### 6.1 Collector algorithm

1. Acquire a single-instance lock on the Admin PC.
2. Validate the output root and minimum free-space threshold.
3. Poll `/training_capture/health`; refuse readiness if incompatible.
4. Poll `/training_frames` every 250 ms while pending entries exist and every
   second while idle.
5. Process pending entries oldest first.
6. Download sidecar and PNG to unique `.part` files.
7. Validate schema, frame ID, media type, dimensions, byte count, and SHA-256.
8. Decode the PNG and confirm its decoded dimensions.
9. Atomically move both files to permanent storage.
10. Re-read the permanent image and verify SHA-256.
11. POST acknowledgement containing that digest.
12. Record success in an append-only collector event log.

The collector must be restart-safe and idempotent. If a permanent file already
exists with the expected digest, it acknowledges without overwriting. If the
name exists with different content, it stops and reports a collision.

### 6.2 Permanent Admin-PC layout

Default layout:

```text
C:\Users\Admin\TrainingImages\PowerBoard\
    2026-09-07\
        images\<frame_id>.png
        metadata\<frame_id>.json
    collector-events.jsonl
    collector-status.json
```

No retention deletion is implemented. The collector warns below a configurable
free-space threshold and stops acknowledging new frames before storage becomes
unsafe. Dataset backup, selection, labelling, and deletion remain separate
operator-controlled workflows.

### 6.3 Running alongside the HMI

First validate with a visible PowerShell process. After acceptance, register the
collector through Windows Task Scheduler for the Admin user:

- trigger at user logon;
- restart on failure with bounded backoff;
- do not require LabVIEW modification;
- write logs/status under the dataset root;
- provide explicit start, stop, and status commands;
- never stop or start LabVIEW automatically.

The operator checklist must verify collector health before opening a capture
session. Because LabVIEW and the production service cannot operate without this
Admin PC, no separate offline-board collection mode is needed.

## 7. Concurrency and failure rules

- Protect the spool index and acknowledgement operation with one lock.
- Serve immutable ready files without holding the index lock during network I/O.
- Stage using unique temporary filenames and `os.replace()`.
- Never acknowledge before the permanent Admin-PC copy is hash-verified.
- A collector timeout retains the board entry and retries with bounded backoff.
- A corrupt or mismatched download retains the board entry and records an error.
- An Admin-PC restart rediscovers all unacknowledged board entries.
- A board restart may lose `/tmp` entries; record this limitation explicitly in
  the acceptance report.
- Training capture failures must not silently masquerade as successful saves.
- During shadow rollout, capture faults do not change detections or PLC results.

## 8. Implementation phases

### Phase 0 — freeze evidence

- Reconfirm which worker LabVIEW starts for the target PowerBoard recipe.
- Record SHA-256, size, and timestamp of the Windows source and deployed board
  source.
- Obtain and test the exact production restart/rollback procedure.
- Preserve copies outside the production path before any approved deployment.

### Phase 1 — collector and protocol tests

- Implement sidecar validation, atomic persistence, restart idempotency,
  acknowledgement, collision refusal, and free-space checks.
- Build a local fake board service implementing the exact new routes.
- Test multiple queued frames, retries, corrupt payloads, digest mismatch,
  duplicate IDs, duplicate acknowledgement, and collector restart.
- Confirm the collector never calls camera, PLC, MES, SQL, or `/run_inference`.

### Phase 2 — candidate worker implementation

- Create a candidate copy of the authoritative production worker.
- Add only the spool, health/index/download/ack routes, and response headers.
- Generate an exact unified diff against the frozen hash.
- Syntax-check and unit-test the candidate without replacing production.
- Verify the existing `/run_inference` JSON body and all existing routes against
  captured baseline responses.

The current files under `production_changes/` are prototypes for one directly
triggered frame. They must be superseded by the complete spool/index/ack diff and
must not be applied as the final implementation.

### Phase 3 — isolated performance test

- Measure lossless PNG encoding time and size using representative 2000×2000 and
  3200×2256 production frames.
- Measure `/run_inference` latency before and after staging.
- Verify collector throughput exceeds the maximum inspection rate.
- Select final pending-frame and byte limits from evidence rather than estimates.

### Phase 4 — controlled shadow rollout

- Use an approved maintenance window and known rollback command.
- Start the Admin collector and prove healthy state.
- Deploy only the reviewed candidate worker.
- Run ten controlled HMI inspections across representative lighting/door states.
- Require ten unique unannotated PNGs, ten sidecars, ten matching checksums, and
  zero unacknowledged entries afterward.
- Verify annotated reports, crops, detections, HMI behavior, and PLC results are
  unchanged.
- Stop the collector for several inspections within spool capacity, restart it,
  and prove complete ordered drain with no loss or overwrite.

### Phase 5 — acceptance and routine operation

- Install the collector as the approved separate startup task.
- Publish one operator health/status check and recovery procedure.
- Monitor disk usage, capture errors, pending age, and count.
- Keep raw captures immutable; label or curate only in derived dataset folders.

## 9. Acceptance criteria

1. LabVIEW source and behavior are unchanged.
2. Existing production detection JSON and routes remain compatible.
3. Every successful controlled HMI inference produces one unique frame ID.
4. Every ID produces exactly one lossless, unannotated Admin-PC PNG and sidecar.
5. The saved PNG checksum matches the board sidecar and acknowledgement.
6. The saved image is the exact pre-annotation handler `frame` used for the
   report/crop path.
7. Existing annotated report and ROI output remain unchanged.
8. Collector restart drains all unacknowledged frames without duplication.
9. Corrupt, incomplete, mismatched, or colliding artifacts are never
   acknowledged or overwritten.
10. Spool limits never silently evict training frames.
11. Capture status and failures are observable on the Admin PC.
12. Board and Admin-PC storage remain within measured limits.
13. Detection correctness and inspection latency remain within owner-approved
    measured bounds.
14. The exact production diff, before/after hashes, deployment steps, and rollback
    steps are recorded even without Git.

## 10. Rollback

Rollback must be file-specific and hash-verified:

1. Stop only the production worker using the approved exact PID/procedure.
2. Restore the timestamped original worker copy.
3. Verify its SHA-256 matches the frozen baseline.
4. Start it using the proven production command.
5. Confirm existing health, camera, inference, report, and HMI behavior.
6. Stop or disable only the separate Admin collector task.
7. Preserve collected Admin-PC images and logs; rollback never deletes data.

Do not use broad `pkill`, `killall`, recursive deletion, or unverified source
replacement during deployment or rollback.

## 11. Deliverables

- Separate restart-safe Admin-PC collector and configuration.
- Fake-board integration test server and protocol tests.
- Candidate production worker generated from a frozen source hash.
- Exact human-readable unified diff; no direct production edits.
- Before/after API and latency evidence.
- Ten-run and collector-restart acceptance report.
- Admin collector startup/status/recovery commands.
- File-specific production deployment and rollback procedure approved by the
  machine owner.
