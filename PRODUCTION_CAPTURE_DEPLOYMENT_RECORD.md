# Production Capture Deployment Record

## Deployment state

- Deployed from Admin PC on 2026-09-07.
- SiMa board clock at backup creation: `2026-09-03T19:11:07Z`.
- Target: `/home/sima/BITSNew_ui/PowerBoard_simaaisrc.py`.
- Production worker was stopped and TCP port 5001 was free during deployment.
- The LabVIEW HMI was not started or modified.
- Admin collector started as a separate background process, PID `3584`.
- HMI inspection acceptance test remains operator-controlled and pending.

## Frozen and deployed hashes

| Artifact | SHA-256 |
| --- | --- |
| Original worker | `e5ab4880391c70b5745bdc1609b9c2911029f8aeb3c9eb193e483ab8f65d401f` |
| Deployed worker | `6093713eb65945e6f13e22c7399f3427cd578642d0006212ff141e5acd91a37b` |
| Deployed `training_capture_spool.py` | `23b570441c4d16cc736d42e21d4df0ed982ebfab4b3b2168294863cc56d8801b` |

The deployed worker and helper both passed Python compilation on the board. The
helper imported successfully and reported schema
`production-training-frame-1.0`.

## Backup

The hash-verified original is stored at:

```text
/home/sima/BITSNew_ui/deployment_backups/20260903T191107Z_training_capture/PowerBoard_simaaisrc.py
```

Its post-copy SHA-256 is the original hash shown above.

## Exact deployed integration

The worker change is recorded in:

```text
production_changes/PowerBoard_simaaisrc.py.diff
```

The adjacent deployed helper is recorded in:

```text
production_changes/training_capture_spool.py
```

No camera, GStreamer, inference, bbox, overlay, crop, report, SCP, PLC, MES,
SQL, lighting, door, or LabVIEW code was changed.

## Collector state

Configuration:

```text
C:\Users\Admin\Desktop\custom_infer\production_frame_capture\config.json
```

Runtime output:

```text
C:\Users\Admin\TrainingImages\PowerBoard\collector-status.json
C:\Users\Admin\TrainingImages\PowerBoard\collector-events.jsonl
C:\Users\Admin\TrainingImages\PowerBoard\collector-stdout.log
C:\Users\Admin\TrainingImages\PowerBoard\collector-stderr.log
```

Before the HMI starts the worker, `collector-status.json` is expected to report
a refused connection. After the worker starts, it should transition to
`healthy` and each inspection should produce one PNG and one JSON sidecar.

## File-specific rollback

Rollback requires stopping only `PowerBoard_simaaisrc.py`, restoring the backup
above, verifying the original SHA-256, and removing only the adjacent
`training_capture_spool.py`. Do not delete the captured Admin-PC dataset or use
broad process/filesystem commands.
