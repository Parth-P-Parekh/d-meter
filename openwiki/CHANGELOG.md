---
type: log
title: Append-Only Decisions and Changes
description: Permanent chronological record of repository decisions, implementation changes, reasoning, verification, and compatibility effects.
status: active
tags:
  - changelog
  - decisions
  - audit
---

# Append-only decisions and changes

This file is append-only. Existing entries must never be edited, removed, reordered, or squashed. Corrections are new entries at the end.

## 2026-09-08T00:00:00+05:30 — Reconstructed approved Phase 1 foundation

- Category: Decision record and implementation baseline.
- Decision/change: Recorded the approved deployment facts and core technology choices: Windows Admin PC, Ubuntu SiMa board, SQLite authoritative storage on the Admin PC at `C:\ProgramData\InspectionPlatform`, one preconfigured local owner account, Windows named pipes, FastAPI `/api/v1`, platform `0.1.0`, and contract `1.0`.
- Reasoning: Phase 1 must work offline with minimal operating dependencies while retaining durable state and stable later-phase contracts.
- Affected components: `src/inspection_platform/settings.py`, `version.py`, `api.py`, `auth.py`, `ipc.py`, `storage.py`, and architecture documents.
- Verification: Source and Phase 1 tests inspected during wiki initialization.
- Backward compatibility: Establishes the initial v1 compatibility baseline; later phases consume rather than replace it.

## 2026-09-08T00:01:00+05:30 — Safety and phase dependency boundary

- Category: Safety decision.
- Decision/change: Phase 1 proceeds without known Modbus meanings using simulated capabilities only. No live PLC writes, ownership, or result publication are permitted until ladder logic, LabVIEW behavior, address meanings, timing, interlocks, safe defaults, and controlled commissioning evidence are approved. All later phases ultimately depend on Phase 0 terminology and logical contracts.
- Reasoning: Raw project binaries and packet observations cannot safely establish PLC semantics or safe physical behavior.
- Affected components: `04-replacement-architecture.md`, all Phase 1 broker/replay and recovery behavior.
- Verification: Architecture dependency and commissioning sections reviewed.
- Backward compatibility: Keeps Phase 1 contracts usable while preventing premature hardware coupling.

## 2026-09-08T00:02:00+05:30 — Authentication, signing, and API policy

- Category: Security decision and implementation.
- Decision/change: Selected Argon2id local password hashing, 15-minute inactivity and 8-hour absolute sessions, exact browser origin `https://DEMETER`, Ed25519 release signatures, Windows-protected private key storage, secure cookies, request-token protection, and disabled built-in API documentation/schema endpoints.
- Reasoning: The local system still needs strong credential storage, revocable sessions, browser mutation protection, and one explicit owner trust root without depending on an external identity service.
- Affected components: `auth.py`, `signing.py`, `api.py`, `app.py`, API tests.
- Verification: Authentication, signing, API-origin, request-token, and hidden-schema tests pass.
- Backward compatibility: Security behavior is part of `/api/v1`; weakening it is incompatible.

## 2026-09-08T00:03:00+05:30 — Worker isolation and bounded communication

- Category: Runtime decision and implementation.
- Decision/change: Selected Windows Job Objects and AppContainer direction instead of containers. Implemented one-process, 2 GB memory, 25 percent CPU worker limits; 120-second cycle and 60-second step deadlines; authenticated named-pipe JSON envelopes; 1 MB IPC messages; and bounded events, diagnostics, artifacts, and worker output.
- Reasoning: Containers may be unnecessarily heavy on the Admin PC and SiMa board; native Windows isolation provides a lighter local boundary. Bounded messages and deadlines prevent a package from exhausting the supervisor.
- Affected components: `worker_limits.py`, `worker_runner.py`, `worker_process.py`, `ipc.py`, `output_limits.py`, `limits.py`.
- Verification: Worker, IPC, correlation, timeout, cancellation, and output-limit tests pass.
- Backward compatibility: The contract envelope remains version `1.0`; AppContainer enforcement remains incomplete and must be additive.

## 2026-09-08T00:04:00+05:30 — Immutable pipeline package trust chain

- Category: Package decision and implementation.
- Decision/change: Pipeline releases use signed ZIP archives up to 2 GB with `manifest.json`, `pipeline/`, `config.schema.json`, optional models/preprocessing, and a `module:class` entry point. Only the configured owner key is trusted. Approval refuses destination overwrite; activation and rollback are idle-only and audited.
- Reasoning: Exact immutable bytes, compatible contract declarations, and owner approval are required to reproduce and safely roll back execution behavior.
- Affected components: `package_format.py`, `packages.py`, `package_registry.py`, `trusted_packages.py`, `api_packages.py`.
- Verification: Package validation, tampering, signer trust, registration, activation, rollback, and worker-loading tests pass.
- Backward compatibility: Package format is pinned to the platform `0.1.x` compatibility range used by current tests.

## 2026-09-08T00:05:00+05:30 — Deterministic Phase 1 replay result

- Category: Functional decision and implementation.
- Decision/change: The safe sample replay pipeline always produces `NO_RESULT`; broker outcomes are exactly `SUCCEEDED`, `REJECTED`, `TIMED_OUT`, `CANCELLED`, and `FAULTED`.
- Reasoning: Offline replay must exercise the full path without creating a false product-quality decision or physical side effect.
- Affected components: `broker.py`, `replay.py`, `contracts.py`, `worker_runner.py`, worker integration tests.
- Verification: The signed sample worker executes through the named pipe and returns `NO_RESULT`.
- Backward compatibility: Result meanings and broker outcome names are canonical contract values.

## 2026-09-08T00:06:00+05:30 — Conservative restart review and rerun

- Category: Recovery decision and implementation.
- Decision/change: Interrupted cycles are never resumed. Startup records a durable review and enters `latched_fault`. The owner may dismiss or request an offline rerun, but either action still requires a separate authenticated reset. Rerun creates a new cycle ID linked to the original and preserves the pinned part, recipe, and package identity.
- Reasoning: After a crash, the platform cannot prove where the old worker or future hardware stopped. New identity prevents duplicate or ambiguous continuation, and explicit review keeps the operator in control.
- Affected components: `recovery.py`, `api_recovery.py`, `app.py`, recovery tests.
- Verification: Restart discovery, repeat discovery, dismiss, rerun, reset gating, route authentication, and new-cycle linkage tests pass.
- Backward compatibility: The recovery queue is usable by the future HMI; live-machine reruns remain disabled until Phase 5 safeguards.

## 2026-09-08T00:07:00+05:30 — Separate signed recipe releases

- Category: Configuration decision, dependency approval, and implementation.
- Decision/change: Recipe settings are separate signed immutable JSON releases rather than embedded mutable settings. They are limited to 512 KB, pin one exact pipeline name/version/checksum, validate against that package's `config.schema.json`, activate only in idle, and support rollback. Approved and pinned `jsonschema 4.26.0` with its resolved dependencies.
- Reasoning: Settings can change without repackaging code, while signatures, schemas, exact code binding, audit history, and rollback prevent unreviewed behavior drift. The size leaves space inside the approved 1 MB IPC boundary.
- Affected components: `recipes.py`, `api_recipes.py`, `app.py`, `requirements-phase1.lock`, recipe tests.
- Verification: Valid, invalid, foreign-signer, non-idle, duplicate-command, oversized-upload, activation, lookup, and rollback tests pass.
- Backward compatibility: Adds recipe contracts without changing pipeline package format or existing API routes.

## 2026-09-08T00:08:00+05:30 — OpenWiki-style repository documentation and governance

- Category: Documentation and process decision.
- Decision/change: Added root `AGENTS.md`, `.openwikiignore`, an Open Knowledge Format-style linked `openwiki/` documentation tree, strict same-change wiki maintenance requirements, and this append-only decision/change record.
- Reasoning: Future agents need durable architectural context and must keep documentation synchronized with implementation and owner decisions.
- Affected components: `AGENTS.md`, `.openwikiignore`, `openwiki/`.
- Verification: Links, source paths, approved decisions, and current Phase 1 status were checked against repository source, tests, `04-replacement-architecture.md`, and OpenWiki's current repository conventions.
- Backward compatibility: Documentation-only; establishes mandatory maintenance procedure for all subsequent changes.
## 2026-09-08T12:05:00+05:30 — Official OpenWiki Codex integration installed

- Category: Development tooling installation.
- Decision/change: Confirmed that OpenWiki is not present in the OpenAI curated skill catalog, then installed Node.js LTS `24.19.0`, OpenWiki CLI `0.5.0`, and OpenWiki's official user-level Codex integration. The integration installed its skill at `C:\Users\Admin\.agents\skills\openwiki` and registered an MCP server in `C:\Users\Admin\.codex\config.toml`.
- Reasoning: The owner requested installation if an OpenWiki Codex skill or plugin existed. The official OpenWiki integration is preferable to an unofficial third-party plugin and uses the existing Codex model session without a separate provider key.
- Affected components: User-level Node.js, npm global packages, Codex skill directory, Codex MCP configuration, and `openwiki/guides/development.md`.
- Verification: `openwiki integrations list` reports `codex installed`; the installed `SKILL.md` and bounded OpenWiki MCP configuration block were inspected. A Codex restart is still required before the new skill and MCP tools become available to a session.
- Backward compatibility: No inspection-platform runtime dependency changed. The integration affects documentation tooling only.

## 2026-09-08T12:06:00+05:30 — Changelog append encoding recovery

- Category: Documentation correction.
- Decision/change: Recreated the pending changelog append from the intact backup using explicit UTF-8 decoding after validation found that PowerShell's default decoding had altered em-dash characters in a temporary generated copy.
- Reasoning: The append-only history must preserve all existing text. Verification occurred before backup deletion, so the damaged copy was never accepted as the maintained changelog.
- Affected components: `openwiki/CHANGELOG.md` only; temporary recovery copies are removed after line-by-line validation.
- Verification: Every pre-existing line is compared with the intact backup using explicit UTF-8 decoding before cleanup.
- Backward compatibility: Documentation encoding repair only; no runtime or contract effect.

## 2026-09-08T17:40:49+05:30 — Live worker-output draining enforced

- Category: Runtime safety implementation.
- Decision/change: Replaced post-exit stdout/stderr collection with concurrent draining through a shared 1 MB budget. Output beyond the limit is discarded while draining continues, and the worker result is rejected with a fault. Added an integration test whose signed pipeline writes more than 1 MB before returning.
- Reasoning: Windows process pipes have finite buffers. Waiting until process exit to read them could block a noisy worker before the approved output limit was checked. Continuous draining removes that deadlock path while preserving the existing limit.
- Affected components: `src/inspection_platform/worker_output_capture.py`, `src/inspection_platform/worker_runner.py`, `tests/phase1/test_worker_output_capture.py`, security and development wiki pages.
- Verification: Five focused worker/output tests passed; the complete Phase 1 suite passed 55 tests with two pre-existing warnings.
- Backward compatibility: No contract, API, limit, or result meaning changed. This enforces the previously approved 1 MB worker-output rule more reliably.
