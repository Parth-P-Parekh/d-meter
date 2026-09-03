# Production Pipeline Timing Plan

## Goal

Measure one inspection cycle from image acquisition through the final PLC write,
without confusing network, queueing, processing, and physical-actuation time.
This plan applies to the existing Demeter / PowerBoard SiMa pipeline on port 5001,
not the isolated services in this repository.

Every event must carry the same immutable `cycle_id` and, where an image is
involved, the same `frame_id`.  Generate `cycle_id` on the Admin PC when the PLC
or camera cycle begins.  Do not use a wall-clock timestamp or a "latest result"
endpoint as the identifier: both can associate a result with the wrong product.

## Prerequisite: comparable clocks

Use a monotonic clock for elapsed durations inside each process.  To calculate a
duration across the Admin PC, SiMa board, camera, and PLC, synchronise their
wall clocks with PTP where supported; otherwise use NTP/chrony and record the
measured offset and uncertainty before and after every test batch.

Keep both fields in every event:

```json
{
  "monotonic_ns": 948372918273,
  "utc": "2026-09-03T08:20:11.713829Z"
}
```

`monotonic_ns` is authoritative for durations within a host.  Cross-host
durations are valid only when clock uncertainty is smaller than the timing being
reported.  For a 1 ms network claim, for example, an NTP offset of +/- 4 ms is
not adequate.

## Required event stream

Write newline-delimited JSON (`timing-events.jsonl`) on each component.  Retain
the raw logs and merge them only after the run.  This event set covers the active
paths listed in `01-current-system-architecture.md`.

| Boundary / component | Event names to add | What it measures |
| --- | --- | --- |
| PLC -> Admin PC | `plc.trigger_received`, `pc.trigger_handled` | PLC event delivery and PC scheduling delay |
| Camera | `camera.trigger_sent`, `camera.exposure_start`, `camera.exposure_end`, `camera.frame_available` | Trigger-to-frame time; exposure is reported separately |
| Camera -> Admin PC | `camera.frame_available`, `pc.camera_frame_received` | Frame-transfer latency (only with comparable clocks) |
| Admin PC preprocess | `pc.preprocess_start`, `pc.preprocess_end` | Host-side Bayer/colour/resize/encode time, if this path is active |
| Admin PC -> SiMa | `pc.board_request_first_byte`, `pc.board_request_last_byte`, `board.request_first_byte`, `board.request_complete` | Upload/transport time and board-side request buffering |
| SiMa input path | `board.frame_accepted`, `board.decode_start`, `board.decode_end` | Input selection, queue wait, H.264 decoding where used |
| SiMa CVU preprocess | `board.preprocess_start`, `board.preprocess_end` | CVU resize, letterbox, colour conversion, normalization, quantization |
| SiMa MLA | `board.inference_submit`, `board.inference_complete` | Accelerator inference only; excludes surrounding queues and decoding |
| SiMa postprocess | `board.postprocess_start`, `board.postprocess_end` | Box decode, NMS, result serialisation / business-rule work |
| SiMa -> Admin PC | `board.response_first_byte`, `board.response_last_byte`, `pc.board_response_first_byte`, `pc.board_response_complete` | Result transport time and client receive buffering |
| Admin PC decision | `pc.decision_start`, `pc.decision_end` | Recipe/region judgement, SQL/MES work if it blocks the cycle |
| Admin PC -> PLC | `pc.plc_write_start`, `pc.plc_write_complete`, `plc.result_received`, `plc.output_changed` | Modbus transaction, PLC scan/logic time, and physical output command time |

Include `host`, `pid`, `thread`, `cycle_id`, `frame_id`, `event`, `monotonic_ns`,
`utc`, and `sequence` in every record.  Include `bytes` for transfers,
`source`/`destination` for network events, `camera_timestamp` for camera events,
and `status`/`error` for terminal events.  The `sequence` number must increase
per producer so dropped or reordered events are detectable.

Example event:

```json
{"schema":"inspection-timing-1.0","host":"sima-192.168.1.20","pid":7421,"thread":"gst-stream-0","cycle_id":"20260903T082011.701Z-004281","frame_id":"cam0-948372","sequence":66,"event":"board.inference_complete","monotonic_ns":948375100020,"utc":"2026-09-03T08:20:11.716201Z","status":"ok"}
```

## Where to instrument the active service

The active service is identified in the existing notes as
`PowerBoard_simaaisrc.py` on SiMa port 5001.  Add probes to that service or its
GStreamer pipeline; do not alter the retired port-5002 or DIY port-5003 services.

1. At the HTTP/SSH request handler, record request-first-byte, request-complete,
   queue-enter, queue-leave, response-first-byte, and response-complete.
2. Propagate `cycle_id` and `frame_id` with the request and attach them to the
   GStreamer buffer metadata.  If the live source is RTSP, bind the ID to the
   selected decoded frame; if it is direct GigE, bind it at the camera callback.
3. Add GStreamer pad probes immediately before and after
   `simaaiprocesscvu`, `simaaiprocessmla`, and `simaaiboxdecode`.  The pair around
   `simaaiprocessmla` is the board-only inference time requested here.
4. Record queue depth and buffer PTS at every probe.  A pad-to-pad span includes
   any queue or hardware wait between probes; report it as such instead of
   labelling it pure inference.
5. Instrument the LabVIEW (or replacement Admin-PC) camera callback, image
   transform, board client, decision logic, and Modbus read/write VIs.  Add an
   application acknowledgement from the PLC program so that a TCP response is
   not mistaken for an applied machine result.
6. Add camera hardware timestamps and PLC input/output timestamps where their
   APIs make them available.  A Modbus acknowledgement proves delivery to the
   PLC protocol stack, not the timing of the physical output.

Vendor GStreamer latency tracing may help validate pad probes, but it cannot by
itself provide product correlation or separate MLA time from upstream queueing.

## Metrics to report

For every `cycle_id`, calculate these spans.  Report milliseconds to three decimal
places, and retain the event IDs used for each calculation.

| Metric | Start -> end | Interpretation |
| --- | --- | --- |
| Camera acquisition | `camera.trigger_sent` -> `camera.frame_available` | Includes sensor exposure/readout; expose subspans separately |
| Camera transfer | `camera.frame_available` -> `pc.camera_frame_received` or `board.frame_accepted` | Network/driver transfer; requires synchronized clocks |
| PC preprocessing | `pc.preprocess_start` -> `pc.preprocess_end` | PC work only |
| PC-to-board upload | `pc.board_request_first_byte` -> `board.request_complete` | Request transport plus board ingress; report payload bytes |
| Board queue wait | `board.request_complete` -> `board.frame_accepted` | Waiting for a selected pipeline buffer |
| Board decode | `board.decode_start` -> `board.decode_end` | Only when H.264/other decode exists |
| Board preprocessing | `board.preprocess_start` -> `board.preprocess_end` | CVU preprocessing only |
| SiMa inference | `board.inference_submit` -> `board.inference_complete` | MLA accelerator only |
| Board postprocessing | `board.postprocess_start` -> `board.postprocess_end` | Decode/NMS/serialisation only |
| Board service time | `board.request_complete` -> `board.response_last_byte` | Entire server-side request, including queue delay |
| Board-to-PC response | `board.response_first_byte` -> `pc.board_response_complete` | Return transport and client receive time |
| PC-to-PLC command | `pc.plc_write_start` -> `plc.result_received` | Modbus transport/protocol time |
| PLC apply time | `plc.result_received` -> `plc.output_changed` | PLC scan and output update; needs PLC-side logging |
| End-to-end digital | `camera.trigger_sent` -> `plc.output_changed` | Full correlated cycle, excluding uninstrumented mechanics |

Never calculate "packet transfer time" as a single value from a packet capture
unless both capture points share a validated clock.  A PC capture remains useful
for packet size, retransmissions, TCP stalls, and request/response ordering.

## Packet capture plan

Capture only during controlled, shadow-mode test batches and write rotating PCAP
files.  Capture at each endpoint or at a switch SPAN/TAP for these channels:

| Channel | Capture / verify |
| --- | --- |
| Admin PC <-> SiMa | HTTP port 5001 and SSH only if it carries pipeline control |
| Camera <-> SiMa or Admin PC | GigE Vision/UDP or RTSP TCP/UDP, depending on the active path |
| Admin PC <-> PLC | Modbus TCP port 502 |
| Admin PC <-> MES / SQL | Include only if the calls are synchronous in the inspection decision path |

Record interface name, capture host, UTC offset estimate, filter, start/end UTC,
and SHA-256 of each PCAP in the batch manifest.  Do not log image payloads or
credentials in general-purpose application logs.  Restrict packet captures
because they may contain images, PLC commands, session tokens, and credentials.

## Test procedure

1. Confirm the real active camera path and service route.  The current documents
   describe several historical paths, so do not combine their timings.
2. Synchronise clocks, record offsets, and validate with a short two-way timing
   check.  Reject a batch whose clock error exceeds the required resolution.
3. Add logging in shadow/read-only mode.  Do not issue PLC motion or production
   pass/fail writes merely to collect timings.
4. Run a warm-up of at least 20 cycles, then record at least 100 normal cycles
   per recipe/camera configuration.  Keep slow cycles; do not average them away.
5. Check every cycle has one terminal result and a complete, monotonic event
   sequence.  Mark missing, duplicate, stale, or mismatched frame IDs invalid.
6. Report count, min, median, p95, p99, max, mean, standard deviation, and the
   breakdown table above.  Keep queue depth and retransmission counts alongside
   the percentile report.

## Current workspace boundary

This workspace contains a retired port-5002 parity service and a port-5003 DIY
demo.  It does not contain Demeter's active service source, the LabVIEW VIs,
camera application, PLC program, or switch/board capture configuration.  The
following are therefore blocked until their owners provide access or explicitly
approve a production instrumentation change:

- inserting GStreamer probes in `PowerBoard_simaaisrc.py`;
- adding correlation fields to the active request/result API;
- logging the Admin-PC/LabVIEW and PLC events;
- enabling endpoint or SPAN/TAP packet capture.

Do not replace the above with synthetic timings from the port-5002/5003 services:
they do not use the active camera, PLC, or original inference service.
