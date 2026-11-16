# V2 replacement plan for War Room

Status: migration in progress. V1 remains available and authoritative until explicit acceptance.

## Existing application contracts to preserve

- League Overview: retained baseline selection, championship and playoff probability
  displays, per-team detail, final-placement matrix, run diagnostics and precision.
- Simulation Center: baseline and action-search submissions, background progress,
  cancellation, history, persisted results and result selection.
- Trade Lab: manual scenarios and paired before/after analysis.
- Opportunity Explorer: action search, candidate ranking, candidate detail and
  state-level analytics.
- Snapshot refresh, identity, rules, provenance and local service security.

## Current V2 capabilities

- Deterministic compact multi-path execution with projected-score model.
- Automatic roster management, IR correction, waiver and free-agent windows.
- Championship and playoff appearance counts.
- Experimental, synchronous, nonpersistent HTTP preview.
- Isolated-week transaction parity checks and successful local smoke test.

## Gaps before replacing V1

1. **Full league output**: include all teams even when a count is zero (done).
2. **Outcome detail**: regular-season finish and seed distributions plus finals appearances added to experimental preview; still need complete final-placement counts, byes,
   byes, weekly standings, bracket results, and optional per-path evidence.
   Never fabricate unsupported metrics.
3. **Uncertainty**: report sampling intervals and clearly distinguish them
   from model uncertainty. Calibrate the scoring distribution with historical
   evidence before presenting forecasts as authoritative.
4. **Run lifecycle**: integrate V2 into durable background runs with bounded
   execution, progress, cancellation, restart handling, and result persistence.
   Do not run 20,000 paths synchronously through the preview route.
5. **Scenario evaluation**: make the compact engine accept explicit starting
   roster changes and support paired common-random-number comparisons.
6. **Action search**: connect candidate generation/legality and V2 evaluation,
   retaining candidate coverage and precision disclosures.
7. **UI migration**: use a shared engine discriminator and compatible result
   DTOs; retain the existing report layouts and clearly identify V2 provenance.
8. **Acceptance**: regression suite, production frontend build, local ESPN
   snapshot smoke test, determinism, full-season transition checks, and
   independent comparison of V1/V2 supported outputs.

## Incremental progress (15 November 2026)

- Preview now returns every team, regular-season rank distributions, playoff seed distributions, and finals appearances using retained path results. Retention is in-memory during the request, **not** durable run storage.
- Frontend displays championship, finals, playoff qualification, No. 1 seed, and top-two seed odds for all teams.
- Existing V1 retained runs and action search are unchanged. Full V2 background lifecycle and League Overview compatibility remain pending.

## Migration order

- Phase A: complete V2 baseline report DTOs, all-team and placement outputs.
- Phase B: durable V2 background baseline and League Overview integration.
- Phase C: paired manual scenario evaluation and Trade Lab integration.
- Phase D: V2 action-search evaluation and Opportunity Explorer integration.
- Phase E: calibrate, compare and explicitly select V2 as default. Keep V1
  fallback until all acceptance gates pass.

The V2 scoring model (CV 0.35) is provisional. No equivalence claim should be
made solely from reproducible seeds or passing isolated transaction parity.
