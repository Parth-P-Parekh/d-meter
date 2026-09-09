---
type: guide
title: Repository Quickstart
description: Fast orientation for safely changing and testing the current inspection-platform replacement.
status: active
tags:
  - quickstart
  - phase-1
---

# Repository quickstart

## What is active

The active goal is Phase 1 of the replacement architecture: an offline Windows-hosted platform kernel that executes owner-approved pipeline packages against deterministic simulated capabilities. Its source is under `src/inspection_platform/`; its focused tests are under `tests/phase1/`.

The Admin PC runs Windows and stores SQLite data and artifacts locally. The SiMa board runs Ubuntu, but Phase 1 does not require it and must not write to PLC, SiMa production services, MES, or other live hardware.

## Read before editing

1. `AGENTS.md`
2. `04-replacement-architecture.md`
3. The relevant page under `openwiki/`
4. The newest entries in `openwiki/CHANGELOG.md`

## Verify Phase 1

From the repository root:

```powershell
.\.venv-phase1\Scripts\python.exe -m pytest -q
```

The pytest configuration in `pyproject.toml` points Python at `src/` and scopes the default suite to `tests/phase1/`.

## Current implemented slices

- Canonical lifecycle, cycle, step, evidence, and result contracts.
- SQLite durability and hash-addressed artifacts.
- Local Argon2id authentication and expiring server-side sessions.
- HTTPS-oriented FastAPI routes with exact-origin and request-token protection.
- Ed25519 package and recipe approvals with the private key protected by Windows.
- Immutable pipeline registration, activation, rollback, and owner-key pinning.
- Immutable recipe registration, JSON Schema validation, activation, and rollback.
- Authenticated Windows named-pipe messages and brokered simulated capabilities.
- One-process worker resource limits and deterministic replay.
- Durable restart-review queue with dismiss, new-cycle rerun, and reset flow.

## Still incomplete in Phase 1

The complete acceptance-to-durable-completion cycle service and its cycle API are not yet implemented. Backup/restore, retention, corruption and disk-full fault injection, full sandbox enforcement, WebSocket events, HTTPS certificate installation, deployment procedures, and several crash/power-loss tests also remain open. See [delivery phases](roadmap/phases.md).

## Documentation duty

Every change must update affected wiki pages and append a record to `openwiki/CHANGELOG.md`. Never rewrite existing changelog entries.
