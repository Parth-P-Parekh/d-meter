# Automatic production inference capture

`collector.py` is the Admin-PC watcher for the final capture architecture. It
does not call `/run_inference`, open the camera, or participate in inspection.
Every inference initiated by the existing LabVIEW HMI is staged by the modified
worker and then discovered through `/training_frames`.

For each pending frame, the collector:

1. downloads the immutable JSON sidecar and lossless PNG;
2. validates the schema, frame ID, byte count, PNG structure/dimensions, and
   SHA-256;
3. saves both files atomically under
   `C:\Users\Admin\TrainingImages\PowerBoard\YYYY-MM-DD`;
4. re-hashes the permanent image; and
5. acknowledges it so the board can delete its temporary pair.

Existing files are never overwritten. A restart reuses matching permanent
files and retries acknowledgement. A hash mismatch, corrupt PNG, filename
collision, incompatible API, or low-disk condition leaves the board entry
unacknowledged.

## Run visibly for validation

The checked-in `config.json` targets the confirmed board URL and Admin dataset
root. Review its free-space threshold, then run:

```powershell
.\tools\run_production_collector.ps1
```

Drain once and exit with:

```powershell
.\tools\run_production_collector.ps1 -Once
```

Status is written to `collector-status.json`; append-only events are written to
`collector-events.jsonl`. Check them with:

```powershell
.\tools\get_production_collector_status.ps1
```

Only after the worker change and controlled visible validation should the task
be installed:

```powershell
.\tools\install_production_collector_task.ps1
```

The installer is provided but has not been run.

## Local protocol simulation

Run `python -m tools.fake_production_capture_server`, POST to
`http://127.0.0.1:5051/fake/enqueue`, point a copied config at port 5051, and
run the collector with `-Once`.

`capture_inference.py` and `capture_stream.py` remain diagnostic prototypes.
They are not used by this automatic collector.
