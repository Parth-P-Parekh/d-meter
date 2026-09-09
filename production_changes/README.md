# Production worker capture candidate

These are reviewed candidate artifacts only. Nothing here has been copied to
the SiMa board or applied to the LabVIEW source repository.

| Worker | Integration diff | Frozen original SHA-256 |
| --- | --- | --- |
| `PowerBoard_Screws.py` | `PowerBoard_Screws.py.diff` | `5ae242b0b855ed2b01013083e9f9ccf2ff2c1fd080f6835b66282b8803a16df0` |
| `PowerBoard_simaaisrc.py` | `PowerBoard_simaaisrc.py.diff` | `e5ab4880391c70b5745bdc1609b9c2911029f8aeb3c9eb193e483ab8f65d401f` |

The selected diff imports `training_capture_spool.py`, registers its additive
HTTP routes, snapshots the exact unannotated handler `frame`, and publishes it
only after the existing report/crop/SCP calls. The existing
`{"detections": ...}` response body is unchanged. Only diagnostic response
headers are added.

Deployment therefore consists of exactly two candidate files placed together:

- the selected worker after applying its matching `.diff`;
- `training_capture_spool.py` adjacent to that worker.

The helper owns only `/tmp/production-training-capture`. It provides the
health, ordered index, PNG, sidecar, and checksum-gated acknowledgement routes.
It caps pending data at 32 frames and 1 GiB and never evicts an unacknowledged
frame. Capture faults are observable but do not alter inspection results during
the shadow rollout.

The Windows diff has been checked against the frozen LabVIEW-repository source.
The deployed board source was re-hashed unchanged. No deployment or worker
restart has been performed. Before an approved rollout, make a timestamped
backup, recheck the source hash, syntax-check a candidate copy in the board
environment, record the candidate hash, and use the machine owner's exact
file-specific restart/rollback procedure.
