---
type: concept
title: Security and Isolation
description: Authentication, signing, API, IPC, and worker trust boundaries.
status: active
tags:
  - security
  - signing
  - isolation
---

# Security and isolation

The owner is currently the single preconfigured local account and holds all defined roles. Passwords are hashed with Argon2id. Sessions are stored server-side as token hashes, expire after 15 minutes of inactivity or 8 hours absolute time, and can be revoked.

Browser mutations require both the exact origin `https://DEMETER` and a matching request token. Session cookies are secure, HTTP-only, strict same-site cookies. Built-in API documentation, ReDoc, and raw OpenAPI schema routes are disabled.

Pipeline and recipe approvals use Ed25519. The configured private key is protected using Windows data protection; only the public key is used to pin trusted releases during registration and execution. Valid self-signed releases from another key are rejected.

Local process communication uses authenticated Windows named pipes with bounded JSON envelopes. IPC messages are limited to 1 MB and carry contract version, request ID, cycle ID, and request sequence.

The pipeline worker is assigned a Windows Job Object limited to one process, 2 GB memory, and 25 percent of total CPU. The cycle deadline is 120 seconds and step deadline is 60 seconds. Event, diagnostic, and worker-output budgets are separately bounded.

Windows AppContainer enforcement and a complete dependency allowlist remain incomplete. The current job limit prevents child processes but does not by itself prove denial of every filesystem, registry, or network access. Do not describe the sandbox as complete until those tests exist.

The runner drains worker standard output and error concurrently while the process executes. Both streams share the approved 1 MB budget. Bytes beyond that combined limit are discarded to keep Windows pipe buffers moving, and the cycle is rejected with a worker fault after the process closes. This prevents a noisy approved package from stalling the supervisor before the limit can be checked.
