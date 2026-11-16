# V2 War Room integration — Timmy handoff and merge gates

Status: **handoff prepared; NOT YET MERGE-ACCEPTED** (2026-11-16).
Branch: `feature/v2-compact-simulator` → proposed `main`.
Review the existing [integration notes](V2_WAR_ROOM_INTEGRATION.md) and [acceptance audit](V2_IMPLEMENTATION_ACCEPTANCE_AUDIT.md) as historical snapshots; several status statements there predate the current implementation and must not be treated as current release evidence.

## Scope and architecture

- V1 baseline, Opportunity Explorer, Trade Lab, and other legacy analytical routes remain available. The V2 compact forecast is a separate **experimental** simulation path, not certified V1-equivalent.
- V2 path: `app/v2_experimental.py` loads a canonical snapshot and schedule; `v2/run_projected.py` prepares projected scoring and automatic roster management; `v2/simulation_batch.py` runs independent season paths; `v2/season_results.py` ranks teams by record and points for; `v2/playoff_bracket.py` resolves the six-team bracket.
- The frontend `SimulationCenter.tsx` offers V2/V1 selection; `LeagueOverview.tsx` loads retained V2 results and displays championship, playoff, seed, finalist, and regular-season finish estimates. Background-run handling is in `app/background.py`, `app/service.py`, and `app/http.py`. Confirm persistence and restart behavior against a real local snapshot.
- The preview endpoint is `POST /api/v1/experimental/v2/preview`; background submission is `POST /api/v1/experimental/v2/runs`, with retained result at `GET /api/v1/experimental/v2/runs/{run_id}/result`. The preview is synchronous and ephemeral; the background flow is distinct.

## League rules and probability integrity

For the user's 14-team 2026 league, **six** teams qualify, with seeds 1 and 2 receiving Week 15 byes; Week 16 is semifinals; Weeks 17–18 form the two-week championship. Record and points-for determine seed ordering.

Each completed path must have six distinct playoff qualifiers and one champion. Accordingly, across all 14 teams:
- Sum of playoff qualification probabilities = **6.0** (600%).
- Sum of championship probabilities = **1.0** (100%).
- The first and second seeds appear in the Week 16 semifinal field.

The batch engine now enforces six berths per path and aggregate counts; League Overview rejects invalid retained V2 totals instead of displaying them. Older retained forecasts may reflect the prior eight-team implementation; **do not silently normalize or reuse them**. Generate a fresh run after pulling the latest branch.

The fixed 1,000-path reference in `v2/benchmark.py` still contains eight-team-era playoff appearance counts. It **must be regenerated from an actual six-team run**; do not fabricate replacement expected values.

## Verification already observed (user Windows machine)

On 2026-11-16, after pulling commit `8587adf`:
- `python -m pytest tests/test_v2_season_results.py tests/test_v2_simulation_batch.py -v`: **9 passed**.
- `frontend/npm run build`: **passed**.
- `frontend/npm test -- --run`: **98 passed, 5 files**.
- jsdom canvas `getContext` warnings occurred but did not fail tests.

These are **targeted** checks. They are not evidence of a full backend suite, a new real-snapshot run, post-restart persistence, or V1/V2 semantic parity.

## Merge-blocking verification checklist

- [ ] Restart the local service after updating the branch; confirm an older invalid V2 retained run is rejected with a clear error.
- [ ] Run a fresh **100-path, seed-42** V2 background forecast for the actual 2026 snapshot. Verify playoff probability sum 600%, championship sum 100%, seeds/byes and two-week final. Verify the result survives a server restart and snapshot selection remains correct.
- [ ] Run the full backend suite: `.\.venv\Scripts\python.exe -m pytest tests -q` (and the repo release verification script as appropriate). Investigate failures rather than bypassing tests.
- [ ] Re-run `cd frontend; npm.cmd run build; npm.cmd test -- --run`.
- [ ] Regenerate and validate the benchmark's six-team reference against the pinned real snapshot. Verify reproducibility with seed 42; compare performance without promising the unproven 20,000-path/five-minute target.
- [ ] Verify V1 baseline, scenario/Trade Lab, and refresh routes still work; check V2 cancellation/recovery and retained-run behavior.
- [ ] Review branch diff, CI results, conflicts, and security/privacy of generated artifacts. No secrets, private ESPN credentials, or local SQLite files should be committed.
- [ ] Timmy reviews this handoff, validates the remaining gates, and **performs the merge**.

## Local operator commands

From `C:\Users\cjkel\Documents\fantasy_football`:

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pytest tests -q
cd frontend
npm.cmd run build
npm.cmd test -- --run
cd ..
powershell.exe -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

War Room: `http://127.0.0.1:8000/`. Use the existing `scripts/stop-local.ps1` for ordinary shutdown.

## Known limitations and deferrals

V2 projected-score variance (CV default 0.35) is an assumption, not demonstrated calibration. Full V1 transaction-ledger parity, historical matchup calibration, 20,000-path performance acceptance, and manager realism remain separate research/acceptance questions. Existing docs may contain obsolete **eight-team** references and need reconciliation before treating them as authoritative.

**Out of scope for this merge:** a decomposition of championship odds by bye probability, seed, and conditional round-win probabilities. Discuss targeted enhancements with Timmy *after* integration is stable.

## Handoff decision

This branch is ready for **review and remaining acceptance work**, not yet a verified merge. Do not mark it merge-ready until the unchecked items above have actual evidence.

## Collaboration and change-control requirements (reviewed 2026-11-16)

Follow [AGENTS.md](../AGENTS.md), [MASTER_SPEC.md](MASTER_SPEC.md), and [ROADMAP.md](ROADMAP.md). The specification is authoritative; the roadmap governs sequencing and acceptance. Every future implementation PR should identify the governing spec section, approved scope, input/output and invalid-input contracts, focused plus adversarial tests, and evidence. Do not silently revise modeling assumptions, infer league settings, or claim V1 parity without proof. Changes to approved behavior require a proposed specification revision and approval first. Keep independent increments small and reviewable; Timmy's independent review/merge remains the final gate.

**Review discrepancy:** [V2_IMPLEMENTATION_ACCEPTANCE_AUDIT.md](V2_IMPLEMENTATION_ACCEPTANCE_AUDIT.md) contains historical eight-team bracket descriptions. The current six-team implementation and 2026 league requirement must be reconciled in authoritative documents before merge. In particular, AGENTS.md requires deriving league mechanics from retrieved configuration rather than universal hardcoding; confirm the current six-team guard applies only to the intended league/configuration and does not inadvertently reject other supported leagues.

## Additional transaction and waiver review gate

Read [V2 model-change and specification consistency review](V2_MODEL_CHANGE_REVIEW_FOR_TIMMY.md), including the **2026-11-16 follow-up audit**. Priority items: compact preflight is not canonical authorization; the simulated weekly cycle does not itself release HOLD/WAIVERS players; unlimited repeat claims differ from the reference window's default cap; waiver/free-agent ordering, IR recovery, and missing canonical ledger updates require explicit acceptance or corrective tests. Do not treat this as a performance-only refactor. Verify the intended supported semantics and document Timmy's approval before merge.
