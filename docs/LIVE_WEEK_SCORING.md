# Live-Week Scoring State (LW1)

Status: **IMPLEMENTED — REVIEW PENDING**. Governing requirement: approved LW1 milestone and the live-week addendum in `MASTER_SPEC.md`.

LW1 adds canonical schema v4 scoring-state evidence without changing the V1 season engine. Schema-v2 and schema-v3 snapshots remain readable. New snapshots retain explicit NFL game status, current player actual-score observations, per-week classification, and a provenance-bearing simulation-boundary decision. Existing analytical results remain bound to their original immutable snapshots.

## Boundary contract

The resolver classifies a scoring week only from explicit game-status evidence:

- every game final: completed;
- every game explicitly scheduled/not started: unplayed;
- a mixture of scheduled, in-progress, and completed: partial;
- missing, postponed, canceled, or unsupported status: unresolved.

Kickoff time, wall-clock time, a zero player score, and a non-null score are not completion evidence. The unchanged V1 engine copies ESPN cumulative standings and then simulates directly from `first_simulation_week`; it has no partial-period settlement or standings-reconciliation step. Automatic V1 selection therefore authorizes only the **current** scoring week when every NFL game is explicitly unplayed and that week begins its configured matchup period. `next_fully_unplayed_period` remains independently visible but is never authorization to skip a partial, completed-but-unsettled, or otherwise unresolved current period. Mid-period starts in multiweek matchups also fail closed.

A manual override cannot bypass those invariants: it must select the current, explicitly unplayed, matchup-period-start week. A current period whose NFL games are final remains unresolved until authoritative fantasy-matchup and cumulative-standings settlement evidence is available. Player actual records are retained as observations but neither their presence nor absence proves settlement. The existing engine continues to receive only a safe fully unplayed boundary.

The future LW2 boundary is `earliest_unresolved_period`. When that period is partial, `partial_week_simulation_required` is true. LW1 exposes this fact but does not simulate it.

## ESPN evidence audit

| Evidence | Raw location | Classification | Limitation |
|---|---|---|---|
| Current scoring period | league `scoringPeriodId` | **Verified live** by prior adapter audit | Identifies period, not completion. |
| NFL kickoff | `proTeamSchedules_wl` game `date` | **Verified live** | Never completion proof. |
| NFL game status | `proTeamSchedules_wl` game `status` | **Verified by tests** | Live field/token coverage requires an authorized diagnostic. Missing evidence fails closed. |
| Player actual points | `kona_player_info` `stats[]`, source 0, matching season/period/split, `appliedTotal` | **Verified by tests** | Record presence does not prove participation; absence cannot distinguish inactivity, bye, unrostered status, delayed data, or missing data. Correction timing remains unverified. |
| Fantasy matchup/standings settlement | no accepted current-period settlement field | **Unsupported** | Final NFL games and some/all player actual records do not prove that ESPN cumulative fantasy standings include the period. |
| Future projection | matching stat source 1 | **Verified live** | Not relabeled as remaining in-game projection. |
| Per-player lineup lock | no accepted field | **Unsupported** | Required before LW2 authorizes remaining moves. |
| Stat-correction finalization | no accepted field | **Unsupported** | Final NFL status does not authenticate final corrected fantasy scoring. |

Unknown game tokens fail translation. Missing actuals remain unavailable rather than becoming zero, while confirmed zero remains distinct. No complete-player-pool rule is used: rostered, unrostered, bye, and score-record counts are diagnostic categories, not inferred participation. Player observations preserve source IDs and remain separate from future `PlayerWeek` projections. Schema-v4 snapshots retain detailed NFL game rows for the complete configured scoring schedule needed to substantiate the live boundary; the V1 `PlayerWeek` horizon remains unchanged.

For authorized manual evidence collection, run `python -m fantasy_football.live_scoring_diagnostic` (or add `--json`). It performs bounded ESPN GET requests and reports sanitized aggregate token/count evidence only; it does not print player identities, cookies, or raw payloads. Synthetic tests verify the parser, not live token availability.

## LW2 integration contract

`LiveScoringState` supplies immutable week status and `PlayerScoringObservation` rows. LW2 must treat confirmed actual points as fixed, simulate only independently established remaining contributions, honor verified lineup locks, and combine actual/simulated values exactly once. Every refresh publishes a new snapshot and cannot mutate prior evidence or results. Until lock evidence, in-progress remainder semantics, and stat-finalization evidence are validated, LW2 must fail closed for affected operations.

## Compatibility and limitations

Schema-v4 serialization is deterministic. Schema-v2/v3 decoding supplies unknown game status and no live-scoring companion. Earlier schema-v4 documents lacking the added matchup-period evidence remain readable, but are not trusted as simulation-ready; application summaries suppress their stored boundary and engine consumers reject it. API 1.0 remains additive: `SnapshotSummary.live_scoring` is nullable and refresh `first_simulation_week` is nullable. Automatic refresh fails closed when current-period status or matchup-period alignment is insufficient. The advanced override is not a mechanism for skipping unresolved work.

LW1 does not implement partial-week Monte Carlo, historical standings reconstruction, in-game remaining projections, or Weekly Deep Dive UI.
