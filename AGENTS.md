# Repository operating instructions

These instructions apply to the entire repository.

## Required context before changing anything

1. Read `openwiki/quickstart.md`.
2. Read the OpenWiki pages relevant to the files being changed.
3. Read the latest entries in `openwiki/CHANGELOG.md`.
4. Treat `04-replacement-architecture.md` as the approved replacement architecture and phase boundary.

<!-- OPENWIKI:START -->
## Repository wiki

The maintained repository wiki begins at `openwiki/index.md`. The fastest orientation page is `openwiki/quickstart.md`. Repository-specific wiki instructions are in `openwiki/INSTRUCTIONS.md`.
<!-- OPENWIKI:END -->

## Strict documentation maintenance rule

Every repository change must update the `openwiki/` directory in the same change.

- Update every wiki page whose statements, paths, interfaces, limits, commands, tests, or status became inaccurate.
- Add a new page when a durable subsystem or operating procedure is introduced and no current page owns it.
- Keep `openwiki/index.md` and `openwiki/quickstart.md` links accurate.
- Do not describe planned behavior as implemented behavior. Label decisions, implemented facts, tests, and unresolved work distinctly.
- Ground technical claims in current source, tests, or an explicitly named approved design document.
- Preserve `openwiki/INSTRUCTIONS.md`; it is the human-authored documentation brief.
- A code change is incomplete if the wiki is stale, even when tests pass.

## Append-only change and decision record

`openwiki/CHANGELOG.md` is append-only and mandatory.

- Append an entry for every user-approved decision and every material code, configuration, dependency, documentation, deployment, or operational change.
- Each entry must state: date/time, category, decision or change, reasoning, affected files or components, verification, and backward-compatibility effect.
- Only append at end-of-file. Never delete, reorder, rewrite, squash, or silently correct an existing entry.
- Correct an error by appending a new correction entry that references the incorrect entry.
- Record failed or reverted approaches when they explain the resulting design.
- Never place credentials, password values, private keys, session tokens, or sensitive production data in the changelog.

## Decision authority

The repository owner is the architecture, package, and signing approver. Do not make a new product, safety, security, deployment, compatibility, retention, or operating-policy decision on the owner's behalf. Explain the choice in plain language, recommend an option, and ask for approval before implementing it.

Implementation details that are already fixed by an approved decision may be completed without another question, but must still be documented.

## Safety boundary

- Phase 1 is offline and simulated. Do not write to PLC registers, claim PLC ownership, alter ladder logic, or enable production control.
- PLC/Modbus meanings remain unresolved until ladder logic, LabVIEW behavior, named signals, safe defaults, timing, and controlled commissioning evidence are approved.
- Do not deploy to, stop, overwrite, or reconfigure the SiMa board or production services without an explicit request naming that action and target.
- Never convert `NO_RESULT`, `FAULT`, or `ABORTED` into `PASS` or `FAIL`.
- Never resume an interrupted cycle. Recovery reruns create a new cycle ID and retain a link to the original cycle.

## Implementation invariants

- Preserve the canonical lifecycle and result enums in `src/inspection_platform/contracts.py`.
- Keep platform API compatibility under `/api/v1`; incompatible behavior requires a new version or a compatibility path.
- The server, not the browser or pipeline package, owns lifecycle transitions, cycle identity, authentication, package trust, and hardware capability access.
- Only packages and recipes signed by the configured owner key may be registered or activated.
- Package and recipe activation is idle-only, transactional, audited, immutable, and rollback-capable.
- Pipeline code must use the capability broker and must not gain direct hardware, filesystem, database, SSH, or arbitrary network access.
- Keep all live PLC operations disabled through Phase 1-4.

## Verification

Use the Phase 1 environment for platform tests:

```powershell
.\.venv-phase1\Scripts\python.exe -m pytest -q
```

Run focused tests while developing, then the complete Phase 1 suite before handoff. Report warnings separately from failures. Do not claim a test was run if it was not.

Preserve user-owned and unrelated working-tree changes. Do not clean, reset, or rewrite them.
