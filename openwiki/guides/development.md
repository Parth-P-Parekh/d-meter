---
type: guide
title: Development and Verification
description: Local environment, dependency, test, and documentation workflow for Phase 1.
status: active
tags:
  - development
  - testing
  - windows
---

# Development and verification

Phase 1 targets Python 3.11 on Windows. Exact installed dependencies are recorded in `requirements-phase1.lock`; `jsonschema 4.26.0` is the approved validator for signed recipe configuration.

Run the focused platform suite:

```powershell
.\.venv-phase1\Scripts\python.exe -m pytest -q
```

At the time this wiki was initialized, 54 Phase 1 tests passed. Treat that number as historical evidence, not a permanent assertion; update this page and append the new result to the changelog when the suite changes.

Development workflow:

1. Read the relevant architecture and wiki pages.
2. Confirm whether a new behavior requires owner approval.
3. Add or update focused tests with the implementation.
4. Run focused tests, then the entire Phase 1 suite.
5. Update affected OpenWiki pages.
6. Append a complete `openwiki/CHANGELOG.md` entry.

Known test warnings at initialization:

- Starlette's test client references an AnyIO alias that is deprecated upstream.
- One tamper test deliberately writes a duplicate ZIP member and Python's ZIP library warns about the duplicate.

Neither warning was a test failure, but new warnings must be investigated rather than normalized automatically.
## OpenWiki Codex integration

The official OpenWiki `0.5.0` Codex integration is installed at user level. It adds the `openwiki` skill under `C:\Users\Admin\.agents\skills\openwiki` and registers the OpenWiki MCP server in `C:\Users\Admin\.codex\config.toml`. Restart Codex before using it. Host-driven runs use the Codex session and repository tools; they do not require a separate model-provider key.

## Latest verification

After live bounded worker-output draining was added, the complete Phase 1 suite reported 55 passing tests and the same two known warnings.
