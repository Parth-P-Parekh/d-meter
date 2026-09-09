---
type: guide
title: Windows Admin PC Operations
description: Current host assumptions, storage locations, bootstrap boundary, and operational cautions.
status: active
tags:
  - windows
  - operations
  - admin-pc
---

# Windows Admin PC operations

The Admin PC host name is `DEMETER`. The intended browser origin and certificate name are `https://DEMETER`. Platform data is stored under `C:\ProgramData\InspectionPlatform`:

- `inspection.sqlite3` — authoritative SQLite database;
- `artifacts\` — content-addressed evidence;
- `packages\` — immutable approved pipeline archives;
- `recipes\` — immutable approved recipe releases;
- signing-key files protected by Windows data protection.

The bootstrap command in `src/inspection_platform/cli.py` creates the database structures, prompts twice for the initial password without echoing it, creates the one local owner account, and creates the signing key. Do not place the password in a command line, source file, configuration, log, or documentation.

The HTTPS certificate has not yet been generated or installed, and no production Windows service has been installed. Those are future explicit host changes and require owner approval at execution time.

Disk policy:

- warning at 20 GB free;
- stop accepting new cycles at 10 GB free;
- maximum artifacts per cycle: 250 MB.

Backup/restore and retention procedures are not yet complete. Do not rely on file copying a live database as an approved backup procedure.
