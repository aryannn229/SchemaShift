# Status

| Item | State |
|---|---|
| 0 current-state note | done |
| F frontend redesign | done (tag phase-F-done) |
| 14 fuzz + differential/metamorphic | done (tag phase-14-done) |
| 15 independent corpus + honest metrics | done (tag phase-15-done); independent labels need a second person |
| 16 evidence levels + counterexamples | done (tag phase-16-done) |
| 17 concurrency analyzer | done (tag phase-17-done) |
| 18 workload-aware optimizer | done (tag phase-18-done) |
| 19 pipeline optimizer | done (tag phase-19-done) |
| 20 benchmark lab | done (tag phase-20-done); 1k and 100k runs recorded in docs/bench |
| 21 prod infra | code, configs, smoke test and a local prod-like stack **done and verified**; **deployment to Vercel / Render / Neon / Atlas: ready for owner to run** (docs/DEPLOY.md) (tag phase-21-done) |
| 22 incremental schema migration compiler | done (tag phase-22-done) |
| 23 safe migration mode | done (tag phase-23-done) |
| 24 risk score + simulator | done (tag phase-24-done) |
| 25 CI/CD tooling | done (tag phase-25-done); the GitHub Action has not been run on GitHub |
| 26 Z3 reasoning | done (tag phase-26-done) |
| 27 evaluation + write-up | done (tag phase-27-done) |
| Presentation | `docs/SchemaShift_Presentation.pptx` (22 slides, screenshots in `docs/screenshots/`, rebuilt by `scripts/build_presentation.py`) |

## BLOCKED / not done
- Live deployment (Vercel, Render, Neon, Atlas) and the smoke test against the live URL: not run from this repository by design.
- Independent (blind, second labeler) corpus labels: needs another person (`schemashift corpus label-template`).
- Mutation score refresh: `scripts/mutation.sh` needs Docker and a long run; the recorded 89.1% predates Phase 18.
- The GitHub Action and the VS Code client were not exercised on GitHub / in VS Code.

## `[AUTONOMOUS — REVIEW]` decisions to look at
See `docs/DECISIONS.md` for the full text. In order: Phase 18 additive workload terms and the banking host change; Phase 19 rewriting stage lists instead of a separate pipeline IR;
Phase 21 arq as the queue; Phase 22 automatic restructuring limited to simple layouts; Phase 23 MANUAL steps never executed; Phase 24 risk-score reference points; Phase 25 a removed UNIQUE
reported as "removed" (not SAFE to BROKEN); Phase 26 the meaning of PROVEN.
