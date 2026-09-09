# OpenWiki repository brief

Document this repository for engineers and coding agents maintaining the inspection-platform replacement.

Priorities:

1. Clearly separate verified production facts, approved design decisions, implemented behavior, tested behavior, and unresolved commissioning work.
2. Treat `src/inspection_platform/` and `tests/phase1/` as the active Phase 1 platform kernel.
3. Treat the root demo, capture, parity, and `experiments/` trees as useful earlier work unless a current task explicitly reactivates them.
4. Explain safety and compatibility boundaries before implementation details.
5. Keep commands Windows-first for the Admin PC and identify commands intended for the Ubuntu SiMa board.
6. Never publish secrets or infer unresolved PLC/Modbus meanings.
7. Maintain concise linked pages rather than one oversized document.
8. Maintain `CHANGELOG.md` as the permanent append-only record required by `AGENTS.md`.

The architecture source of truth is `../04-replacement-architecture.md`. The wiki summarizes it but must not silently change it.
