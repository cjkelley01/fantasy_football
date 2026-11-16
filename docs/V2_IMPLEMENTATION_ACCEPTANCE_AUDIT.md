# V2 implementation acceptance audit — 2026-11-13

Branch: `feature/v2-compact-simulator`. This is a source inspection, not a new runtime or parity certification.

## Original acceptance contract
20,000 complete season-to-championship paths under five minutes; native compact legal transactions; conservative, roster-aware managers; path-dependent waiver claims; accurate standings and league playoff rules; V1 parity; selectable application backend; trustworthy calibrated probabilities.

## Implemented and directly evidenced
- Indexed compact transaction state: `v2/transaction_state.py`, `v2/compact_state.py`.
- Native compact preflight and acquisition execution: `v2/compact_preflight.py`, `v2/compact_executor.py`.
- Manager decisions, waiver coordination and IR correction: `v2/automatic_manager.py`, `v2/compact_window.py`, `v2/ir_correction.py`.
- Path-local weekly state and simulated scoring: `v2/simulation_batch.py`, `v2/run_projected.py`, `v2/projected_scoring.py`.
- Standings and 8-team bracket: `v2/season_results.py` sorts by record then points-for; `v2/playoff_bracket.py` uses 1v8/4v5/2v7/3v6 in Week 15, semifinals Week 16, combined Weeks 17–18 final.
- Snapshot-backed CLI and profiling: `v2/benchmark.py`.
- Differential tests: `tests/test_v2_canonical_differential.py` compares observable transaction fields on selected scenarios, deliberately excludes V1 ledger parity.
- Real-snapshot replay diagnostic: `v2/parity_replay.py`, which explicitly excludes standings-driven priority resets.
- Historical replay and calibration research utilities exist, but `docs/v2_matchup_calibration.md` explicitly labels historical calibration incomplete.

## Not yet accepted
1. **End-to-end V1 parity:** Differential tests cover selected transactions; full-season scoring, waiver resets, IR cases, ledger and outcomes have not been certified equivalent. V2 compact executor explicitly does not synthesize canonical ledger records.
2. **Production backend selection:** `app/service.py` uses the existing V1 Monte Carlo pipeline; no verified user-selectable V2 backend or reversible application feature flag.
3. **Calibrated probabilities:** `run_projected.py` documents its coefficient of variation as an assumption rather than a validated NFL model. `docs/v2_matchup_calibration.md` requires historical starter provenance, ESPN score reconciliation, baselines, reliability bins, confidence intervals, and postseason backtesting.
4. **Manager realism:** Inspect the very repetitive acquisition/IR decisions across simulation paths; test responsive decisions under changed rosters, waiver ownership, injuries, projections, and manager policy. Do not inject arbitrary randomness to manufacture diversity.
5. **Output contract:** Batch result currently exposes champions and playoff appearances; it retains individual `SeasonResult` only when requested. Application-level seed, bye, bracket and placement probability output is not verified for V2.
6. **Performance:** Latest 1,000-path runs 18.290, 18.142, 18.479 seconds; median 18.290 seconds, ~365.8 seconds extrapolated for 20,000. Five-minute ceiling not yet achieved or measured on an actual 20,000-path run.
7. **Acceptance suite:** 151 targeted V2 tests passed on user's Windows environment. Full repository suite and live snapshot replay still require explicit acceptance runs after final changes.

## Recommended implementation batches
**A — correctness and parity (first):** Run `python -m fantasy_football.v2.parity_replay --db outputs/fantasy_football.sqlite3` against the pinned snapshot; extend it to use path-matched simulated scores and standings-based waiver priority resets. Add full-season V1/V2 differential scenarios, IR/waiver and score/bracket edge cases, and explicit handling of ledger differences.

**B — manager and scoring calibration:** Audit accepted claims by week/team/player and path, distinguish deterministic expected choices from missing state sensitivity, reconcile historical box scores with ESPN, validate score dispersion and matchup reliability without future information leakage.

**C — backend and reporting:** Add an explicit opt-in V2 backend selector behind a reversible flag, preserve V1 default, expose seed/bye/advancement/placement probabilities and provenance, validate application contracts.

**D — performance and release gate:** Reprofile calibrated V2, optimize measured hotspots, compare repeated 1k/10k runs and actual 20k runtime, enforce under-five-minute target and no material parity regressions.

## Guardrails
Do not describe V2 as production-calibrated or V1-equivalent before the acceptance tests pass. Do not replace canonical authorization with compact heuristics without differential evidence. Preserve the user's 14-team league and eight-team playoff format.
