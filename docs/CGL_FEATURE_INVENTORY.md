# CGL feature and migration inventory

Phase 0 compares the Record Book at `e5a63e7` with War Room at `0f27de6`. The proposed destinations follow the [six-group product plan](CGL_PRODUCT_PLAN.md#6-target-information-architecture). The checks below are migration acceptance criteria. Source inspection does not establish live ESPN behavior or historical parity.

## Record Book

| Current view | Proposed destination | Migration check |
|---|---|---|
| Overview | Home summaries and League Overview | Link current summaries to canonical facts and historical highlights to the archive. Display the applicable as-of dates. |
| 2026 Live | League Matchups and League Overview | Compare a settled week to published results. Distinguish upcoming, unverified and final matchups. |
| Standings | League → Standings & Power; completed standings in History | Reconcile W/L/T, points, seed and final rank independently. Use War Room's snapshot for current facts and the archive for settled seasons. |
| Playoff Odds | League → Playoff Race, using War Room | Check that the local app uses retained War Room runs. Keep the standalone public forecast and existing links operational during migration. |
| Champions | History → Season Archive, with a League Overview link | Reconcile award, final rank and championship result. Preserve runner-up and title series evidence. |
| Seasons | History → Season Archive | Compare settled matchups, scores, ties, byes and multiweek series using season-specific rules. |
| Records | History → Records & Leaderboards | Verify examples of weekly, season and career records, source links, filters and shared records. |
| Managers | League → Teams & Managers, Career context | Verify team-season and manager identity through one rename and one shared team. Preserve season-specific display names. |
| Head-to-Head | History → Head-to-Head & Rivalries | Recompute selected W/L/T, streak and playoff examples from settled matchups and manager identities. |
| Drafts | History → Draft History, linked from Season Archive | Reconcile one complete draft board, pick order and season/team/manager joins. Surface missing associations. |
| League Analytics | League → Standings & Power and historical comparisons | Verify all-play, expected wins, schedule luck, schedule swap and power rankings against hand-checkable fixtures and published outputs. Use comparable time windows. |
| Moves That Mattered | History, with retrospective links from League Activity | Preserve editorial copy, supporting transaction IDs, ownership stints and starter contributions. Verify multi-season filtering and source coverage. |

The Record Book consumes two generated build-time datasets. `core_history.json` supplies published history, metrics and stories. `playoff_forecast.json` supplies the public forecast. Its ESPN retrieval, history build and static deployment remain independently recoverable during migration. The history dataset marks 2024 and 2025 complete and 2026 partial. Official CGL statistics begin in 2024. League Legend remains editorial. Preserve public fragment links until the compatibility plan is accepted. [Record Book repository](https://github.com/cjkelley01/CGL-Record-Book/tree/e5a63e7) · [Pipeline structure](https://github.com/cjkelley01/CGL-Record-Book/blob/e5a63e7/docs/repository-structure.md) · [Moves methodology](https://github.com/cjkelley01/CGL-Record-Book/blob/e5a63e7/docs/moves-that-mattered.md)

## War Room

| Current capability | Proposed destination | Migration check |
|---|---|---|
| Manual refresh, authorized opt-in startup refresh, latest configured-league snapshot and saved snapshot selection | Shared shell and Tools → Data & Model Center | Verify publication and saved-snapshot access. Changing manager perspective or archive year must preserve deliberate analytical snapshot selection. |
| League Overview and retained baseline results | League Overview for observed facts; League → Playoff Race for forecast detail | Reopen the same run and verify snapshot, displayed probabilities, coverage and uncertainty. Show an unavailable state when there is no applicable run. |
| Opportunity Explorer | Players & Moves → Waiver Wire and Trade Center → Trade Finder | Preserve candidate/state links, all-team impact, coverage and unresolved rankings. Keep execution feasibility distinct from modeled value. |
| Trade Lab, including supported effective-dated scenarios | Players & Moves → Trade Center → Trade Builder and Evaluator | Exercise structural and timed plans. Preserve eligibility, snapshot/run identity and retained results. Acceptance and waiver-claim probabilities remain outside supported outputs. |
| Simulation Center and retained runs | Tools → Simulation Center | Verify submission, progress, cancellation and reopen. Preserve admission, storage and retention boundaries. |
| Source-bound current-week Stage A and engine lineup or transaction interfaces | Existing analysis; future My Team or Players & Moves views where approved | Preserve completed/unstarted evidence checks. Active games and ambiguous permissions fail closed. Current-week advice has separate gates. |
| League Insights placeholder | Reconsider in navigation design | Give replacement views a defined purpose and source. Preserve existing supported routes. |
| Source, model, coverage and settings context | Tools → Data & Model Center and contextual evidence | Verify run identity, displayed units, nulls and evidence labels. Keep private inputs and raw source payloads outside the browser. |

Canonical snapshots own current league facts. Retained War Room simulations own local forward-looking outputs. Stage A capability does not establish broad live-game or current-week advice support. [Current capabilities](../README.md#current-capabilities) · [Interface](INTERFACE.md) · [Current-week input](TECHNICAL_REFERENCE.md#source-bound-current-week-application-input) · [Timed Trade Lab](TECHNICAL_REFERENCE.md#effective-dated-trade-lab-execution)

## Phase 0 handoff

- **Data and identity:** Specify source precedence, stable IDs, public/private classification, and rejection of missing or ambiguous joins. Reconcile 2025 first, use 2024 for year switching and use partial 2026 as a negative test for archive finality.
- **Navigation and League Overview:** Use the six product groups, static desktop sidebar and mobile menu. Walk through current results, the applicable forecast, and a past championship. Resolve page ownership and public link compatibility before committing to routes.
- **Forecasts:** War Room supplies all forecasts for the local app. A comparison for future public replacement is a separately scoped decision. The existing public Record Book stays operational in the meantime.
- **Recurring history:** Use a versioned local archive for discovery. Month-to-month updates, rollover and corrections remain a [separate follow-up](BACKLOG.md#cgl-integration-follow-up).

This inventory records source inspection. Historical row reconciliation, identity validation and the combined interface still need acceptance on their owning work items.
