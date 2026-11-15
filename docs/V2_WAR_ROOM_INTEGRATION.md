# V2 experimental War Room integration

Status: **backend preview wired on feature branch; local verification pending**.

The existing V1 baseline, scenario, search, background-run and refresh routes are
unchanged. V2 is accessible only by explicit opt-in at:

`POST /api/v1/experimental/v2/preview`

Example JSON body:

```json
{
  "snapshot_id": "3aba763f-0945-4e79-b4e8-1fcc596a531f",
  "paths": 100,
  "seed": 42,
  "coefficient_of_variation": 0.35,
  "manage_rosters": true,
  "vacancy_bonus": 8.0
}
```

The endpoint loads the selected stored snapshot, builds its canonical regular-season
schedule and matchup periods, then runs V2 projected seasons. Response reports
experimental championship/playoff probabilities and runtime. It is synchronous,
ephemeral, limited to 1–1000 paths, and does **not** create a V1 background run
or write a result. No frontend control has been added yet.

**Not equivalent to V1.** The V2 projected scoring CV is an uncalibrated assumption.
It has distinct management semantics and is not a drop-in replacement for the
accepted V1 analytical contracts. The isolated-week transaction-state matrix
passed for weeks 10–18 (39 IR activations, 15 acquisitions), but this is not
full-season or transaction-ledger parity.

## Local smoke test

Start the local server after pulling this branch. In a separate PowerShell
window:

```powershell
$body = @{
  snapshot_id = "3aba763f-0945-4e79-b4e8-1fcc596a531f"
  paths = 100
  seed = 42
  coefficient_of_variation = 0.35
  manage_rosters = $true
  vacancy_bonus = 8.0
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/experimental/v2/preview" -Method Post -ContentType "application/json" -Body $body
```

Verify `experimental=true`, `calibrated=false`, `persisted=false`, and
that the existing V1 routes still work. Run the full pytest suite before
considering frontend integration.

## Next

1. Run full tests and smoke-test this endpoint on the user's local snapshot.
2. Add a clearly labeled experimental panel in the React Simulation Center,
   without altering the V1 selection or results.
3. Evaluate durable run support, progress, cancellation, and calibration
   separately; do not imply they are already implemented.
