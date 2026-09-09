# Migration and Validation: Modular Inspection Platform

## Migration rule

Migrate by contract and measured behavior. Freeze the production baseline, reproduce required outcomes with the parity pipeline, validate in replay and shadow operation, and grant exclusive authority only after the relevant gates pass. The target is a FastAPI/React platform with a fixed Control Supervisor and approved typed Python pipeline packages, not a line-by-line LabVIEW port.

The canonical lifecycle states are `startup_reconciliation`, `idle`, `ready`, `cycle_accepted`, `pipeline_executing`, `result_publication`, `plc_acknowledgement`, `completed`, `held`, `aborted`, and `latched_fault`. The canonical result enum is `PASS`, `FAIL`, `NO_RESULT`, `FAULT`, or `ABORTED`. Tests, logs, APIs, plugins, PLC encoding, reports, and operator screens must use these names and meanings consistently.

## Evidence labels

- **Verified production fact:** observed in production or deployed artifacts.
- **Selected v1 decision:** the initial platform architecture described in `04-replacement-architecture.md`.
- **Commissioning output:** evidence that must be measured, reviewed, and approved before the associated authority is enabled.

The starting verified baseline is direct-SiMa Basler acquisition, the current TCP-port-`5001` board service, `3200 x 2256` source frames, multi-step recipes, an unresolved PLC mapping, and legacy SQL/MES dependencies unavailable to the replacement development environment. SiMa and the existing model remain the v1 baseline. MES is optional and disabled by default.

## Migration sequence

```mermaid
flowchart LR
    P1[1. Freeze baseline] --> P2[2. Approve PLC and safety contracts]
    P2 --> P3[3. Validate SDK and replay]
    P3 --> P4[4. Build atomic SiMa provider]
    P4 --> P5[5. Build parity pipeline]
    P5 --> P6[6. Run correlated shadow mode]
    P6 --> P7[7. Deploy read-only React HMI]
    P7 --> P8[8. Exclusive result-only cutover]
    P8 --> P9[9. Migrate remaining modules]
    P9 --> P10[10. Replace algorithms incrementally]
    P10 --> P11[11. Gate optional integrations]
```

### 1. Restore, document, and freeze the production baseline

Restore and document the physical and logical network topology, including host, PLC, SiMa, camera, lighting, barcode, database, and relevant service paths. Freeze immutable hashes or export versions for:

- the production LabVIEW executable/project and configuration;
- PLC ladder/project and live controller version;
- SiMa board image, port-`5001` service, startup configuration, and deployed application;
- Basler camera identity, firmware, acquisition/trigger settings, and `3200 x 2256` geometry;
- active model, preprocessing, labels, thresholds, and calibration;
- representative recipes, including their multi-step order and timing;
- sanitized SQL/MES contracts and observed availability.

Record time synchronization state and capture good, bad, missing-part, and hardware-failure cycles. Output: a signed baseline manifest, topology diagram, evidence archive, and owner list. No production changes are made in this phase.

### 2. Export behavior and approve the safety/PLC contracts

Export and review PLC ladder and LabVIEW behavior. Correlate named inputs, outputs, interlocks, motion intents, trigger edges, ownership rules, result handling, timeouts, heartbeat loss, and recovery behavior. Controlled packet capture supplements but does not replace ladder and application review.

Define and approve:

- a safety-responsibility matrix showing every PLC-owned safe response;
- a cycle timing contract and maximum response deadlines;
- the dedicated PLC mailbox with request/result sequence IDs, acknowledgements, heartbeat counters, result enum, ready/busy/held/fault state, startup reconciliation, stale-result rejection, wraparound behavior, and PLC-enforced owner fence;
- the physical Modbus address map, directions, encodings, safe defaults, and write permissions.

Output: controls-, safety-, software-, and production-approved commissioning package. Until approved, the replacement has no PLC write authority.

### 3. Define and test the SDK, broker, and replay harness

Implement the typed plugin SDK contracts for immutable `CycleContext`, `InspectionPipeline.execute(context, capabilities)`, capability interfaces, `PipelineResult`, and the immutable package manifest. Implement schema-validated configuration, package compatibility/signature checks, activation only in `idle`, and one-command rollback.

Build the Hardware Capability Broker and supervised pipeline-worker boundary. Prove that plugins cannot directly open PLC/Modbus, camera, serial, filesystem, database, SSH, or arbitrary network resources. Enforce cancellation, per-step/cycle timeouts, artifact bounds, CPU/memory limits, and deterministic cleanup.

Build an offline replay harness that substitutes recorded deterministic capability responses for hardware. Output: versioned SDK, conformance suite, sample package, threat-model evidence, and replay corpus.

### 4. Implement the atomic SiMa `VisionProvider`

Adapt SiMa as the v1 camera/inference owner. One request returns one atomic frame/result pair correlated by `cycle_id`, `vision_request_id`, and `frame_id`, including timestamps, source geometry, versions/checksums, confidence/detections, coordinate definition, artifacts, and explicit status/fault.

Create a non-consuming, bounded preview fan-out from published frames. The shadow backend and HMI must not open their own camera connection or consume the existing MJPEG inspection queue. Slow or multiple preview clients cannot delay or steal an inspection frame. SSH remains limited to deployment and lifecycle management.

Output: versioned provider contract, implementation, correlation tests, geometry proof, load test, and recoverability evidence.

### 5. Build the reference parity pipeline

Create an immutable, typed reference pipeline package that reproduces required legacy production outcomes with new code modules. Model the necessary multi-step sequence through motion, settling, lighting, vision, preprocessing/classical vision, inference, aggregation, and evidence capabilities. Do not preserve legacy implementation structure when the observable contract is sufficient.

Pin package, configuration, model, and preprocessing versions. Output: approved parity package, manifest, replay report, expected step results, timing budget, and known limitations.

### 6. Run the backend in correlated shadow mode

Run FastAPI, the Control Supervisor, Durable Result Store, broker, and parity pipeline without PLC write or machine-control authority. Feed shadow execution the exact same SiMa frame/result pairs used by the production cycle, correlated by IDs; do not capture an independent image and do not infer from an unrelated latest frame.

For every cycle compare LabVIEW and parity-pipeline step outcomes, final result, evidence, timing, and fault classification. Investigate and disposition all disagreements. Output: traceable cycle-by-cycle comparison and an approved parity report.

### 7. Deploy the read-only React HMI

Deploy authenticated, role-aware pages for lifecycle state, health, exact correlated images/overlays, selected and available approved pipeline versions, step/final results, alarms, events, and audit history. The HMI cannot start cycles at this phase and cannot edit or build pipelines in v1.

Validate authorized REST/WebSocket behavior, reconnect, multi-browser fan-out, CSRF/origin protection, redaction, and accessibility/operator usability. Output: operator acceptance and security test evidence.

### 8. Perform an exclusive result-only PLC cutover

During a controlled outage, stop LabVIEW control, acquire the PLC-enforced owner fence, reconcile durable/backend/PLC state, and enable only the approved mailbox result path. Never permit concurrent LabVIEW and replacement ownership. The backend publishes sequenced results; the PLC retains physical machine and reject control.

Keep a tested cold LabVIEW rollback: release replacement ownership, verify safe PLC state, stop replacement writes, restore the frozen LabVIEW version, acquire its permitted owner state, and reconcile before production resumes. Define trial duration, staffing, rollback time, and stop criteria before cutover.

Immediate stop/rollback criteria include unexpected PLC state, ownership conflict, stale or duplicated result, missed acknowledgement, heartbeat loss, result deadline violation, frame/result mismatch, unsafe recovery, or an agreed parity/quality threshold breach.

### 9. Migrate remaining workflows as independent modules

Migrate manual controls, barcode, recipes, reports, retention, and other required workflows in separately scoped releases. Manual commands remain role-restricted and use the Hardware Capability Broker and PLC interlocks. Recipe/configuration changes tune approved code and never inject scripts. Each module requires its own contract, acceptance evidence, training, operational procedure, and rollback before its legacy counterpart is retired.

### 10. Replace algorithms and models one module at a time

After the parity platform is stable, replace individual preprocessing steps, classical-vision methods, decision functions, models, inference providers, or cameras without changing the Control Supervisor, pipeline SDK, or HMI contracts. For each replacement, run deterministic offline replay, quality/timing analysis, failure injection, shadow validation, approval, idle-only activation, and rollback testing.

### 11. Add optional integrations only after core stability

Keep MES disabled and report it as `not_applicable`; it creates no alarms, readiness dependency, inspection delay, or network calls. MES tests are out of scope for initial platform acceptance.

If production later requests MES, introduce a separate integration acceptance gate covering an idempotent transactional outbox, duplicate-safe destination contract, authentication/secret rotation, retry/dead-letter behavior, observability, data governance, and proof that loss or slowness never enters the time-critical lifecycle. Apply the same pattern to other optional integrations.

## Validation gates

No phase grants authority merely because implementation is complete. The following evidence is mandatory where applicable.

### Contract and replay gate

- Run contract tests against every plugin, capability interface, provider, adapter, manifest version, and persisted schema.
- Replay recorded production cycles deterministically without hardware, including good, bad, missing-part, boundary-confidence, timeout, and hardware-failure cases.
- Confirm repeat runs produce the same capability sequence and `PipelineResult`, apart from explicitly normalized time/trace fields.
- Reject undeclared capabilities, invalid or executable configuration, incompatible versions, altered checksums, and unapproved/unsigned releases.

### Isolation and failure-containment gate

- Inject plugin exceptions, process crashes, infinite loops/hangs, malformed results, ignored cancellation, excessive event/artifact output, and CPU/memory exhaustion.
- Confirm the supervised worker is terminated/replaced, capabilities reach deterministic cleanup, the hardware-owner process remains healthy, and the supervisor enters the correct `aborted` or `latched_fault` path.
- Confirm plugin code cannot establish direct device, filesystem, database, SSH, or arbitrary network access.

### Vision and preview gate

- Prove exact `cycle_id`/`vision_request_id`/`frame_id` correlation, source geometry, coordinate mapping, timestamps, model/preprocessing identity, and atomic frame/result storage.
- Reject missing, stale, duplicated, mismatched, wrong-geometry, and out-of-order frames/results.
- Run multi-client preview fan-out with slow/disconnected clients and prove inspection capture is non-consuming and unaffected.
- Test SiMa restart, provider timeout, malformed payload, camera disconnect, and inference failure with explicit fault outcomes.

### PLC and concurrency gate

- Test sequence duplication, stale values, defined wraparound, lost/delayed acknowledgements, heartbeat loss/recovery, and backend-only, PLC-only, and simultaneous restart.
- Attempt competing LabVIEW/replacement ownership, two browsers, concurrent API requests, repeated triggers, and worker retries; prove none can create overlapping cycles or owners.
- Verify startup reconciliation from each persisted lifecycle state and ambiguous mailbox state.
- Prove PLC safe behavior for backend, network, vision, and power failure and confirm all physical motion/reject interlocks remain PLC-enforced.

### Parity and cutover gate

- Compare LabVIEW and the reference parity pipeline across representative products, known good/bad parts, missing parts, recipe steps, confidence boundaries, and every induced hardware failure.
- Approve discrepancies explicitly with quality, controls, vision, software, and production owners; unexplained disagreements block cutover.
- Demonstrate result-only owner transfer and cold LabVIEW rollback under the approved procedure.

### Package lifecycle gate

- Prove activation succeeds only in `idle`, is transactional/audited, and pins the package through a cycle and restart.
- Reject hot reload and incompatible, corrupt, altered, unsigned, or unapproved packages/configurations.
- Demonstrate one-command rollback to the previous approved version and successful replay/smoke validation after rollback.

### Persistence, security, and recovery gate

- Test transactional completion and reconciliation around failure before/after result commit, PLC publication, and PLC acknowledgement.
- Test disk-full/low-space behavior, quotas, retention, log rotation, database corruption/recovery, backup restore, and artifact/hash consistency.
- Test service-account startup, least privilege, secret rotation, removal of plaintext credentials, RBAC, WebSocket authorization, CSRF/origin controls, and audit integrity.
- Test orderly restart, process crash, OS restart, and abrupt power loss with synchronized-clock loss and recovery.

## Required acceptance record

Each gate records build/package/model checksums, configuration versions, environment identity, test corpus, expected and actual outcomes, deviations, approvers, date, and linked evidence. Approval is required from controls, safety, vision, software, and production owners for their respective responsibilities and jointly for production cutover.

Physical Modbus addresses and machine timing remain commissioning outputs until the PLC/LabVIEW review is approved. The documentation alone authorizes no code, PLC, board, network, or production change.
