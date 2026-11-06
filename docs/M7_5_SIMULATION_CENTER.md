# M7.5 Simulation Center

Status: **COMPLETE AND ACCEPTED** (31 October 2026). Governing contracts: `MASTER_SPEC.md` M7.5, API schema 1.0, `RUN_MANAGEMENT.md`, and the visual-first standard in `UI_DESIGN_VISION.md`.

## Contract and output

The Simulation Center is an integrated Configure → Monitor → History surface. It submits accepted API 1.0 baseline and action-search requests, monitors retained run and progress records, and delegates completed-result rendering to League Overview, Opportunity Explorer, or Trade Lab. It does not implement analytical estimators, legality, roster completion, or candidate generation in TypeScript.

Quick, Balanced, and High Precision are transparent runtime presets. M8.3 measured the exact live path cost and reduced their path ceilings to 5, 25, and 100 respectively; these are application workload policies, not runtime guarantees or engine limits. Baseline and action-search precision are deliberately separate: baseline `target_half_width_probability` is submitted as a probability and displayed after conversion to probability points, while action-search `target_paired_half_width_probability_points` is submitted and displayed directly in probability points. The effective-request review identifies preset-provided or user-customized values, explicit submitted values and units, derived worker/path budgets, and the separate backend admission boundary.

Action-search configuration exposes requesting team, compatible families and orderings, explicit counterparties and player universes, trade shape, chain/action bounds, fixed/progressive evaluation, sampling relationships, candidate/evaluation limits, wall-clock budget, workers, and response rows. Frontend preflight mirrors the API and engine's structural request invariants; backend validation and admission policy remain authoritative.

## Lifecycle semantics

- Submission requires deliberate action and is guarded against duplicate in-flight submission. Ambiguous transport failures are not retried.
- Active polling aborts on unmount, remains scoped to explicit run/snapshot identity, and continues discovery while any run for the active snapshot remains nonterminal. A selected terminal run cannot hide another active run. Run selection clears prior progress, late progress is identity-checked, and transient failures back off and recover without claiming that backend work stopped.
- A snapshot switch clears snapshot-specific teams, counterparties, player restrictions, selected progress, local notices, and submission state. It aborts local observation only; an already accepted backend job may continue and is never described as cancelled.
- The UI shows actual stages, counters, coverage, precision, ranking resolution, elapsed time, and stop reason. It never derives an overall percentage or ETA. Baseline/manual work is explicitly described as coarse progress.
- The lifecycle timeline is mapped explicitly from the accepted backend status. `started_at_utc` establishes whether Running was reached; it never implies completion. Queued and Running identify their active step, `cancel_requested` has a distinct pending outcome, `completed` is the only successful terminal outcome, and Cancelled, Failed, or Interrupted are labeled unsuccessful terminal outcomes. Text, icons, status badges, and accessible labels carry the distinction without relying on color.
- Cancellation is cooperative. `cancel_requested` is distinct from `cancelled`, and completion can win the race before cancellation takes effect.
- Terminal deletion is explicit, confirms the run identity, and is visually separate from cancellation.

## History, navigation, identity, and export

History is snapshot-scoped and searchable/filterable, and displays the retained record's expiration time. API 1.0 currently exposes a newest-first bounded list of at most 50 records and no request-body retrieval; the UI does not claim complete lifetime history or recreate historical configuration. Expired or deleted results remain unavailable rather than being reconstructed.

Completed baseline and action-search runs reopen in their established workspaces with an explicit preferred run ID. Snapshot mismatch is rejected by navigation and independently validated by each result viewer. Manual results reopen Trade Lab, but their historical draft is not reconstructed because the retained contract does not contain it.

Trade Lab submissions now receive unique user-facing scenario IDs (`trade-lab-<snapshot>-<uuid>`), distinct from run ID and engine scenario SHA-256. The current export is intentionally limited to retrieved run metadata JSON and labels its scope, timestamp units, snapshot, canonical identity, and run ID.

## Visual and accessibility decisions

- A lifecycle timeline communicates categorical state without inventing a gauge.
- Source-backed counters are primary; the coverage/precision matrix keeps independent completeness dimensions distinct.
- Exact retained records use a compact list because timestamps and identities require precise inspection.
- Status is never encoded by color alone. Keyboard controls, semantic labels, mobile reflow, and reduced-motion behavior use the shared shell.

## Verification and limitations

Focused tests cover precision-unit serialization and display, DTO construction, family/ordering and resource validation, deliberate submission, duplicate protection, multiple active runs, terminal reconciliation, rapid selection with late progress, transient lifecycle identity, snapshot changes during submission, cancellation acknowledgement, filtering/navigation, bounded-history disclosure, and axe accessibility. Explicit lifecycle cases cover Queued, Running, Cancellation Requested, Completed, Cancelled, Failed, Interrupted, and completion winning a cancellation race, including status badges and actions. Retained-viewer suites validate baseline, action-search, and manual-result loading; App integration preserves preferred-run and snapshot isolation. Actual captures—including running, completed, cancellation-pending, failed, mobile, and retained-manual states—live under `docs/images/m7-5/`.

Known limitations: run history is bounded to 50 records; baseline/manual progress is coarse; expiration has no separate status enum; export covers metadata rather than full candidate/state universes; retained manual results cannot reconstruct historical transaction drafts.

M7.5 passed independent review. M8 has not started.
