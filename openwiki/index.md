---
okf_version: "0.2"
type: index
title: Inspection Platform Repository Wiki
description: Maintained entry point for the replacement platform, historical SiMa tooling, safety boundaries, and operating knowledge.
status: active
tags:
  - inspection-platform
  - phase-1
  - repository
---

# Inspection Platform Repository Wiki

This wiki is the maintained engineering map for the repository. Start with the [quickstart](quickstart.md), then follow the subsystem pages relevant to the work.

## Current platform

- [Architecture overview](architecture/overview.md)
- [Contracts and lifecycle](architecture/contracts-and-lifecycle.md)
- [Security and isolation](architecture/security-and-isolation.md)
- [Persistence and restart recovery](architecture/persistence-and-recovery.md)
- [Pipeline packages and signed recipes](guides/packages-and-recipes.md)
- [Development and verification](guides/development.md)
- [Windows Admin PC operations](operations/windows-admin.md)
- [Delivery phases and compatibility](roadmap/phases.md)

## Governance

- [Repository documentation brief](INSTRUCTIONS.md)
- [Append-only decisions and changes](CHANGELOG.md)

## Historical and supporting work

The root `README.md`, `DIY_PIPELINE.md`, capture utilities, parity utilities, and `experiments/yolo26_modalix/` document earlier SiMa inference and data-collection work. They are not the Phase 1 replacement kernel and must not be treated as permission for production deployment or PLC control.
