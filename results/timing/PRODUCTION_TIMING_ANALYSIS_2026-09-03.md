# Production timing analysis — 2026-09-03

## Scope and safety record

The active Demeter/SiMa application source and configuration were **not edited**.
The active `PowerBoard_simaaisrc.py` process was not restarted.  The only active
production calls were the approved existing `POST /run_inference` requests; they
created the service's normal runtime reports and SCP transfers.

PC packet capture was limited to TCP/5001 (SiMa HTTP), TCP/22 (the board's SCP
report copies), and TCP/502 (Modbus).  It recorded no packet loss and all Packet
Monitor filters were removed immediately after capture.

To make inter-host time comparable, these approved *system time-service* changes
were made; none are application-code changes:

- The Admin PC is synchronized to `time.windows.com` and serves NTP on UDP/123
  only to `192.168.1.20`.
- The SiMa board uses `192.168.1.17` as its NTP server and now reports `System
  clock synchronized: yes`.
- The board's `RootDistanceMaxSec` is 10 seconds.  This is needed because the
  Windows NTP service currently advertises a 7.77-second root-distance estimate,
  which exceeds systemd-timesyncd's default 5-second acceptance limit.

A final ten-sample NTP check measured a median board-to-PC offset of 1.998 ms
(the board was behind) and a median NTP round trip of 0.371 ms.  That is suitable
for interpreting the hundreds-of-milliseconds service times below, but it is not
adequate to label a sub-millisecond packet interval as an exact one-way delay.

## Active path confirmed

The board runs:

```text
python3 /home/sima/BITSNew_ui/PowerBoard_simaaisrc.py
```

Its configured live path is:

```text
Basler camera 192.168.1.10
  -> aravissrc on SiMa board (2000 x 2000 RGB)
  -> videoscale / NV12
  -> SiMa H.264 encoder + decoder
  -> simaaiprocesscvu
  -> simaaiprocessmla
  -> simaaiboxdecode
  -> /tmp/bbox_output_yolov8/bbox_*.bin
```

The service is continuous-camera, not request-per-frame.  In particular,
`/run_inference` gets the latest display frame, waits for a newly named bbox file
(up to three seconds), parses it, writes a JPEG report, starts SCP copies, and
then returns JSON.  Neither the request nor the bbox file carries a frame ID.
Therefore, no current measurement can prove that the response belongs to the
frame selected by that request.

## Measured results

### Admin PC ↔ SiMa service

30 sequential `POST http://192.168.1.20:5001/run_inference` calls were captured.
All returned HTTP 200 and an 18-byte JSON response with zero detections.

| Metric | Result |
| --- | ---: |
| Service-call round-trip minimum | 501.133 ms |
| Service-call round-trip median | 538.928 ms |
| Service-call round-trip mean | 720.645 ms |
| Service-call round-trip P95 | 1,437.830 ms |
| Service-call round-trip P99 | 1,676.570 ms |
| Service-call round-trip maximum | 1,742.388 ms |
| Observed HTTP connections | 30 |
| TCP SYN → SYN/ACK round-trip median | 0.162 ms (14 observable handshakes) |
| TCP SYN → SYN/ACK maximum | 1.040 ms |

The 0.162 ms TCP handshake median shows that the LAN transport is not the source
of the 0.5–1.7 second API result.  The service handler's file wait, image work,
and report/SCP activity dominate that measured duration.  Do not call this
number "MLA inference time."

### Board → Admin-PC report transfer

The same 30 API calls produced 30 observed board-to-PC SCP connections.

| Metric | Result |
| --- | ---: |
| Board → PC captured SCP payload | 16,939,560 bytes (about 16.15 MiB) |
| Average board → PC payload per API call | 564,652 bytes |
| PC → board SCP protocol payload | 49,260 bytes |
| Observed SCP payload-transfer span, minimum | 56.633 ms |
| Observed SCP payload-transfer span, median | 345.120 ms |
| Observed SCP payload-transfer span, maximum | 488.265 ms |

The service launches SCP asynchronously, so the transfer overlaps the HTTP API
call rather than necessarily extending its response time.  This is nevertheless
substantial concurrent traffic on the Admin-PC ↔ SiMa channel and should be
included in capacity and cycle-time analysis.

### Camera → SiMa board

The board sees the camera at `192.168.1.10`.  A read-only ten-second sample of
the newest bbox file advanced from `bbox_58079.bin` to `bbox_58123.bin` over
9.081 seconds: 44 completed tensors, or **4.845 tensors/s**.  The one-second
increments were +4, +5, +5, +5, +11, +0, +4, +5, and +5, showing a non-uniform
output cadence.

This is an output-throughput observation, not camera-to-board packet latency or
MLA inference time.  The board lacks `tcpdump`/`dumpcap`, and this PC is not on a
switch mirror port for the camera-to-board unicast GigE Vision stream.  A capture
at the camera endpoint or a managed-switch SPAN/TAP is required to measure that
transfer directly.

### Admin PC ↔ PLC

`Inspection.exe` was running, but a 60-second passive TCP/502 capture contained
**zero Modbus packets**.  No PLC timing is reported because no real PLC
transaction was observed.  No test command, register write, or motion action was
sent by this work.

## Requested timings: status

| Requested timing | Status | Evidence / limitation |
| --- | --- | --- |
| SiMa accelerator inference only | Not measurable without application instrumentation | No MLA submit/complete timestamps exist. |
| SiMa preprocessing only | Not measurable without application instrumentation | No CVU entry/exit timestamps exist. |
| Full live SiMa service call | Measured | 538.928 ms median; includes output-file wait and reporting. |
| Admin PC ↔ SiMa TCP setup | Measured | 0.162 ms median observed handshake RTT. |
| Board → PC report transfer | Measured at PC NIC | 345.120 ms median observed payload span. |
| Camera → SiMa transfer | Not measurable from current capture point | Needs camera-side or switch-SPAN capture. |
| Admin PC ↔ PLC Modbus | Not observed | PLC channel idle during the 60-second window. |
| Trigger-to-result-to-PLC-output cycle | Not measurable from current interfaces | No shared cycle/frame ID and no PLC-output acknowledgement timestamp. |

## Why internal SiMa timings were not forced

The board includes GStreamer’s `latency` tracer, but a safe synthetic test showed
that latency records appear only at `GST_DEBUG=GST_TRACER:7`.  The protected
production source assigns `GST_DEBUG=3` before GStreamer initializes.  Restarting
the live service with only `GST_TRACERS=latency` would therefore interrupt it
without producing stage-latency records.  Raising that debug setting or adding
pad probes would require changing the production source, which was explicitly
out of scope.

## Next data-collection window

For remaining figures without changing the existing production code:

1. Run a normal, operator-authorized inspection cycle while TCP/502 is captured
   passively.  This will provide Admin-PC ↔ PLC packet counts, transactions, and
   observed TCP round trips; it will not prove physical actuator timing.
2. Mirror the camera and board switch ports to a capture host, or capture at the
   camera endpoint.  Synchronize that capture host with the board to calculate
   camera-stream packet timing.
3. To obtain per-stage CVU/MLA/postprocessing timing and a defensible full
   product cycle, the service must emit correlated buffer/frame timestamps.  The
   current architecture cannot reconstruct them from external packets or bbox
   filenames alone.

## HMI overlay-display check — added 2026-09-03

The SiMa-to-Admin-PC overlay transfer is **not blocked at the HTTP boundary**.
The existing `GET /latest_frame` endpoint returned a valid 11.98 MB JPEG to this
PC.  Visual inspection confirms that it contains a clean PCB image with green
`Screw_Presence` overlays.  The received copy is saved as
`board-latest-frame.jpg` alongside this report.

The HMI issue is instead likely in the separate local-report/display path:

- Board source writes `latest_frame.jpg` and invokes SCP to copy files named
  `frame_<timestamp>.jpg` into `C:/Users/Admin/Reports/full`.
- The expected Admin-PC folders `C:\Users\Admin\Reports\full` and `cropped`
  existed but were empty when checked.
- The LabVIEW `ReportINS.vi` statically depends on `Read PNG File.vi`, as well
  as `Latest file update.vi` and `Set Image in center.vi`.  A PNG reader cannot
  directly consume the JPEG that the board produces/copies.

The screenshot is also geometrically consistent with a square 2000×2000 camera
image rendered in a wide, short display pane without a fit-to-window transform:
the top dark fixture is visible while the PCB and its lower-frame overlays lie
outside the visible crop.  This is a display-fit issue, separate from network
delivery.

No change was made to resolve either issue.  The required fix decision is whether
the HMI should load the verified board HTTP JPEG directly, or whether its report
reader and the SCP handoff should be made format/path-consistent (PNG versus
JPEG) and use an atomic completed-file handoff.

## Evidence files

- `sima-api-network-capture-20260903.json` — all 30 service-call timings.
- `sima-plc-20260903.pcapng` — filtered SiMa HTTP/SCP and Modbus PC capture.
- `sima-plc-20260903-network-summary.json` — parsed network summary.
- `plc-observation-20260903.pcapng` — 60-second Modbus-only passive capture.
- `plc-observation-20260903-network-summary.json` — confirms zero observed
  Modbus packets.
- `tools/measure_live_sima_api.py` and `tools/analyse_pcapng.py` — read-only
  collection/analysis utilities created in this workspace.
