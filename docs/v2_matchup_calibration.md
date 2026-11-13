# V2 matchup calibration: historical replay contract

## Status
Experimental replay scaffold only. No production integration, no calibrated
matchup claims, and no completed historical matchup report yet.

## Required source
The historical backfill has player-week projections and actual scores but does
**not** establish which players were in each fantasy team's starting lineup
before kickoff. Do not reconstruct starters by selecting highest actual scorers,
or by treating final rosters as pregame lineups. That leaks future information.

Supply an independently established pregame starter manifest:
```json
{
  "starter_provenance": "pregame",
  "last_completed_2026_week": 9,
  "matchups": [
    {
      "season": 2026,
      "week": 9,
      "matchup_id": "2026-09-1-2",
      "teams": [
        {"team_id": 1, "starter_player_ids": [101, 102]},
        {"team_id": 2, "starter_player_ids": [201, 202]}
      ]
    }
  ]
}
```
IDs are illustrative placeholders, not genuine historical starters.

Run:
```powershell
.\.venv\Scripts\python.exe -m fantasy_football.v2.matchup_replay --manifest path\to\pregame_starters.json
```

The manifest's `pregame` declaration is a **claim by the data provider**, not
independent verification. Before accepting reports, audit the lineup source,
capture times, scoring slots, bye weeks, and historical scoring settings.

## Evaluation policy
- Historical ESPN projections are **assumed** equal to kickoff projections.
  This is unverified and may introduce look-ahead bias.
- Train on prior seasons only: 2025 uses 2024; 2026 uses 2024-2025.
- 2026 evaluation stops at the explicitly supplied last completed week.
- Experimental sampler: uniform adaptive neighborhood, exact zeros preserved.
- Missing paired starter scores fail closed; no silent substitution.
- Simulation assumes independent player scores. Team correlation is not modeled.
- Matchup Brier score is a starting metric, not sufficient calibration evidence.

## Remaining work before production
1. Extract and audit ESPN historical lineup starters with reliable timing and
   position-slot eligibility; generate real matchup manifests.
2. Validate actual team totals against ESPN matchup results and scoring rules.
3. Benchmark the same historical matchups against a documented baseline.
4. Report probability reliability bins, Brier/log loss, uncertainty, and sample
   sizes; check positional and team-total calibration.
5. Validate playoff advancement and championship probabilities with historical
   brackets, including Weeks 17-18 two-week finals.
6. Integrate behind a reversible feature flag, run regression and performance
   tests, then write final model and operations reports.

No historical matchup data has been replayed by this commit.
