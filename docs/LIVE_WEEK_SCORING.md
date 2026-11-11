# Live-Week Scoring State (LW1)

Status: **IMPLEMENTED — REVIEW PENDING**. Governing requirement: approved LW1 milestone and the live-week addendum in `MASTER_SPEC.md`.

LW1 adds canonical schema v4 scoring-state evidence without changing the V1 season engine. Schema-v2 and schema-v3 snapshots remain readable. New snapshots retain explicit NFL game status, current player actual-score observations, per-week classification, and a provenance-bearing simulation-boundary decision. Existing analytical results remain bound to their original immutable snapshots.

## Boundary contract

The resolver classifies a scoring week only from explicit game-status evidence:

- every game final: completed;
- every game explicitly scheduled/not started: unplayed;
- a mixture of scheduled, in-progress, and completed: partial;
- missing, postponed, canceled, or unsupported status: unresolved.

Kickoff time, wall-clock time, a zero player score, and a non-null score are not completion evidence. Automatic V1 selection chooses the first fully unplayed week only when no unresolved evidence gap precedes it. A current period whose games are final but whose player actual-score evidence remains incomplete is unresolved, rather than prematurely finalized. An advanced manual override remains available and is labeled `manual_override`; it is never represented as detected ESPN evidence. An override that contradicts explicit completed or partial status is rejected. The existing engine continues to start at `LeagueSnapshot.first_simulation_week` and never receives a partial week.

The future LW2 boundary is `earliest_unresolved_period`. When that period is partial, `partial_week_simulation_required` is true. LW1 exposes this fact but does not simulate it.

## ESPN evidence audit

| Evidence | Raw location | Classification | Limitation |
|---|---|---|---|
| Current scoring period | league `scoringPeriodId` | **Verified live** by prior adapter audit | Identifies period, not completion. |
| NFL kickoff | `proTeamSchedules_wl` game `date` | **Verified live** | Never completion proof. |
| NFL game status | `proTeamSchedules_wl` game `status` | **Verified by tests** | Live field/token coverage requires an authorized diagnostic. Missing evidence fails closed. |
| Player actual points | `kona_player_info` `stats[]`, source 0, matching season/period/split, `appliedTotal` | **Verified by tests** | Live completeness and correction timing remain unverified. |
| Future projection | matching stat source 1 | **Verified live** | Not relabeled as remaining in-game projection. |
| Per-player lineup lock | no accepted field | **Unsupported** | Required before LW2 authorizes remaining moves. |
| Stat-correction finalization | no accepted field | **Unsupported** | Final NFL status does not authenticate final corrected fantasy scoring. |

Unknown game tokens fail translation. Missing actuals remain unavailable rather than becoming zero. Player observations preserve source IDs and remain separate from future `PlayerWeek` projections. Schema-v4 snapshots retain detailed NFL game rows for the complete configured scoring schedule needed to substantiate the live boundary; the V1 `PlayerWeek` horizon remains unchanged.

## LW2 integration contract

`LiveScoringState` supplies immutable week status and `PlayerScoringObservation` rows. LW2 must treat confirmed actual points as fixed, simulate only independently established remaining contributions, honor verified lineup locks, and combine actual/simulated values exactly once. Every refresh publishes a new snapshot and cannot mutate prior evidence or results. Until lock evidence, in-progress remainder semantics, and stat-finalization evidence are validated, LW2 must fail closed for affected operations.

## Compatibility and limitations

Schema-v4 serialization is deterministic. Schema-v2/v3 decoding supplies unknown game status and no live-scoring companion. The application labels those snapshots as predating LW1 and preserves their explicit boundary. API 1.0 is additive: `SnapshotSummary.live_scoring` is nullable and refresh `first_simulation_week` is nullable. Automatic refresh can fail when ESPN omits status; users may use the explicit advanced override after independently verifying the boundary.

LW1 does not implement partial-week Monte Carlo, historical standings reconstruction, in-game remaining projections, or Weekly Deep Dive UI.
