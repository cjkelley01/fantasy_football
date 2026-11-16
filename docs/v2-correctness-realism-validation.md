# V2 correctness and realism validation

Status: **in progress; experimental**. Performance target is met, but speed is not evidence of forecast calibration.

## Verified evidence (2026-11-15)

- Local V2 test run: 214 passed before the new differential tests in this phase.
- Fixed 2026 snapshot `3aba763f-0945-4e79-b4e8-1fcc596a531f`, 1,000 paths, seed 42: championship counts, playoff appearances, and accepted waiver/free-agent totals matched the stored reference after optimization.
- Same snapshot, 20,000 paths, seed 42: 201.575 seconds (99.219 paths/sec), meeting the under-five-minute goal.
- New acquisition-planner differential tests compare indexed-roster optimization to the unoptimized public planner for multiple capacities, needs, acquisition phases, and vacancy premiums. These tests must be run locally before marking them passed.

## Behavior currently implemented

- Candidate acquisition selection is driven by **current scoring-week projection** for a player eligible for a vacant lineup slot. The drop sacrifice also incorporates future projections and temporary-absence protection; the policy is **not** purely current-week.
- Waiver order is updated from simulated standings after regular-season weeks. The free-agent order within an acquisition window is frozen from its opening waiver queue.
- The lineup solver determines needs. The compact executor validates transactions. IR activation uses modeled recovery based on projection evidence in the current benchmark, **not actual injury eligibility**.
- The Gaussian scoring uncertainty uses coefficient of variation 0.35 by default. That value is a modeling assumption, not a calibrated finding.

## Acceptance gates before calling V2 correct

1. **Unit and invariants:** run all V2 tests and the complete repository suite. Validate ownership uniqueness, roster slots, acquisition caps, priority changes, and no mutation of input states after accepted/rejected transactions.
2. **Seeded regression:** run the exact fixed-snapshot 1,000-path reference. Any difference must be classified as intentional policy change or bug.
3. **V1 comparison:** compare canonical V1 and compact V2 on the *same initial snapshot, weekly schedule, seed, scoring assumptions, and manager policy* wherever behavior is intended to match. Compare transaction legality and state transitions **per week**, not just championship totals. Record known unsupported V1 features rather than silently treating V2 as equivalent.
4. **Realism audit:** inspect representative team/week decisions: starter vacancies, candidate projections, protected starters, drop sacrifice, IR decisions, waiver-order changes, and why no acquisition was proposed. Verify that acquisitions make football sense rather than merely pass executor rules.
5. **Sensitivity:** repeat a sufficiently large sample with management off and with vacancy bonuses 0, 4, 8. Quantify changes in championship/playoff odds and transaction counts. A policy setting that drastically changes odds without plausible transactions requires review.
6. **Calibration:** evaluate projection errors and scoring distribution against historical observations. Do not describe title probabilities as calibrated until this is complete.

## Reproducible commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -k "test_v2_"
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m fantasy_football.v2.benchmark --db outputs/fantasy_football.sqlite3 --snapshot-id 3aba763f-0945-4e79-b4e8-1fcc596a531f --paths 1000 --seed 42 --verify-reference --acquisition-stats --detail-diagnostics
```

## Limitations / non-goals

- Matching a seeded regression does **not** establish V1 parity, transaction-ledger parity, or real-world forecasting skill.
- Repeated identical acquisitions across paths can be expected when current-week projections and available players are unchanged. Standings-driven waiver order can still change who acquires a contested player.
- Do not alter the projection model as part of parity testing; isolate manager-policy effects first.
- Do not merge into the War Room default simulation backend until the acceptance gates and remaining differences are documented.


## Policy sensitivity results (2026-11-15)

Local run: `scripts/v2_realism_audit.py`, 1,000 paths per scenario, seed 42, fixed snapshot `3aba763f-0945-4e79-b4e8-1fcc596a531f`.

| Policy | Accepted free agents | Accepted waivers | Team 4 championships | Team 10 championships | Team 13 championships |
|---|---:|---:|---:|---:|---:|
| Management off | 0 | 0 | 81 | 69 | 240 |
| Vacancy bonus 0 | 8,000 | 3,000 | 89 | 115 | 212 |
| Vacancy bonus 4 | 12,000 | 3,000 | 109 | 113 | 207 |
| Vacancy bonus 8 | 12,000 | 3,000 | 109 | 113 | 207 |

At bonuses 4 and 8, **all championship counts, playoff appearances, acquisition counts and manager diagnostics match** in this test. This does not prove those policies are equivalent on other snapshots. Retain bonus 8 as the existing regression baseline; bonus 4 is an equivalent candidate on this dataset, not an independently calibrated optimum.

With bonus 8, the 17,000 no-proposal decisions with needs comprise 15,000 `no_eligible_player_in_available_pool` and 2,000 `no_positive_current_projection`. At bonus 0, another 9,000 decisions report `candidate_gain_below_threshold`. These are **decision events, not unique team-week vacancies**: the manager may be called multiple times across phases or repeated acquisition rounds. Do not claim that 17,000 distinct lineups were unfillable.

Across 1,000 paths, 15,000 accepted acquisitions and 5,000 modeled IR activations are recorded. Most acquisition choices are identical across paths because initial rosters, projections and available-player pools are shared; week-13 contested free agents show path-dependent variation (team 9 versus team 17). The run reports no executor rejections, but this alone does not establish canonical transaction parity.

**Decision:** acquisition-policy sensitivity and diagnostic classification are documented. **Outstanding before full correctness/realism signoff:** canonical V1/V2 weekly state-transition comparison; explicit verification of legal alternatives for the no-eligible and no-positive-projection cases; historical calibration of projected scoring and manager choices. Keep V2 labeled experimental until those gates are completed.
