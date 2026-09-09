---
type: concept
title: Persistence and Restart Recovery
description: SQLite durability, immutable artifacts, completion transactions, and restart review behavior.
status: active
tags:
  - sqlite
  - recovery
  - durability
---

# Persistence and restart recovery

The Admin PC is authoritative for Phase 1 storage. `DurableResultStore` creates SQLite tables for cycles, lifecycle events, artifacts, simulated publication intents, and audit records. SQLite uses full synchronous writes. Artifacts are stored under SHA-256-derived paths and referenced by immutable evidence records.

Completion writes the final result, lifecycle transition, and simulated publication intent in one transaction. A result that names unstored evidence is rejected and the transaction rolls back.

At startup, `RecoveryReviewQueue` discovers cycles interrupted in `cycle_accepted`, `pipeline_executing`, `result_publication`, or `plc_acknowledgement`. It creates one durable review per original cycle and moves the in-memory supervisor from `startup_reconciliation` to `latched_fault`.

The review API allows the owner to dismiss or request an offline rerun. Neither decision clears the fault. An authenticated reset is required afterward. A rerun creates a new cycle ID, preserves the part, recipe, and exact package identity, links the new cycle to the original review, and starts only after offline reconciliation. The original cycle is never resumed.

Routes:

- `GET /api/v1/recovery-reviews`
- `POST /api/v1/recovery-reviews/{id}/dismiss`
- `POST /api/v1/recovery-reviews/{id}/rerun`
- `POST /api/v1/reset`

Backup/restore tooling, retention, corruption handling, and broader power-loss testing remain Phase 1 work. The current implementation must not be represented as production-recovery complete.
