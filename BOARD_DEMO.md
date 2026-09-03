# Retired Wood Package Parity Experiment

This document records an earlier experiment that used the installed Wood SiMa package. It is not part of the active DIY pipeline. Its service on port 5002 has been stopped, and no production service was changed.

## Installed isolated runtime

Only these files were copied to the Modalix board:

```text
/home/sima/wood-parity-demo/wood_parity_service.py
/home/sima/wood-parity-demo/wood_parity_worker.py
/home/sima/wood-parity-demo/start_wood_parity_service.sh
```

The retired runtime depends read-only on the installed package at `/data/simaai/applications/Wood_simaaisrc`. It did not copy or modify an MPK, use a camera, write PLC/MES/SQL data, or alter the active PowerBoard service on port 5001.

The retired service formerly used `http://192.168.1.20:5002`; it is not running.

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health` | GET | Confirm installed Wood model assets and expected label order. |
| `/contract` | GET | Read supported upload/result contract. |
| `/infer` | POST multipart | Submit `image` and `frame_id`; returns a versioned detection result. |

## Historical findings

- SSH/SCP access works as `sima@192.168.1.20`.
- The installed Wood package is version `1.3.0` and has labels `ChipOff`, `Major`, `Minor`.
- Port 5002 was free before the demo started. Port 5001 remains used by the active PowerBoard service.
- Its `/health` endpoint succeeded before the service was stopped.
- The active PowerBoard endpoint returns live detections, proving the board runtime and accelerator path are operational.

## Recorded-input blocker

The installed Wood manifest is built for a continuous RTSP H.264 source:

```text
rtsp://192.168.1.10:8554/mystream1
→ SiMa hardware decoder
→ CVU preprocessing
→ MLA model
→ box decoder
```

The current demo substitutes a replayed uploaded image while retaining the same SiMa stages. It reaches the MLA/decoder, but does not receive a bbox tensor before the worker deadline. The service returns an explicit error and stays healthy.

This is not a pass/fail or model-accuracy result. Do not use the demo output for production decisions until a continuous recorded-image RTSP source is supplied and a successful response is captured.

## Do not start this service

This retired service must remain stopped while the DIY demo is evaluated. Its commands are retained only as an audit record:

```bash
nohup /home/sima/wood-parity-demo/start_wood_parity_service.sh --host 0.0.0.0 --port 5002 \
  </dev/null >/tmp/wood-parity-demo.log 2>&1 &
```

Confirm health:

```bash
curl -fsS http://127.0.0.1:5002/health
```

Stop only the demo service:

```bash
ps -eo pid,args | grep '/home/sima/wood-parity-demo/wood_parity_service.py' | grep -v grep
kill <displayed-demo-pid>
```

Never use a broad process kill and never stop the PowerBoard process on port 5001.

## Once an RTSP replay source exists

1. Configure the board demo worker to use the replay URL instead of its temporary upload feeder.
2. Send a curated corpus frame through the replay stream with an immutable frame ID.
3. Call the demo service for that frame and save its 200-response JSON as `results/demo/<frame_id>.json`.
4. Repeat for all corpus frames.
5. Run `python tools/run_parity.py --manifest data/corpus/manifest.json --demo-results results/demo --output results/parity`.
6. Treat every unmatched class, count, box, or score as a parity failure.
