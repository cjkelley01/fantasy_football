# CGL navigation and League Overview proposal

Phase 0 design for [WR-027](BACKLOG.md#cgl-record-book-integration). This follows the [product plan](CGL_PRODUCT_PLAN.md#6-target-information-architecture), [feature inventory](CGL_FEATURE_INVENTORY.md) and [data and identity proposal](CGL_DATA_IDENTITY_PROPOSAL.md). Route names are proposed. Existing behavior remains governed by the [interface contract](INTERFACE.md).

On November 22, 2026, the product owner confirmed the six-group static sidebar and the page groupings below. Use these groupings as the Phase 1 design baseline. Route details, public link compatibility and implementation verification remain open.

## Shared shell

Keep War Room's existing visual language and use a static desktop sidebar. Show the six groups in the same order on desktop and in the mobile drawer. Groups stay expanded. Keep navigation reachable when the viewport is short, highlight the current page, and indicate the parent when a detail route is open. The top bar carries league identity, manager perspective, refresh, theme and source context. The manager selector remembers a mapped manager ID locally and leaves analytical snapshot selection unchanged.

| Group | Sidebar destinations | Inline or contextual views |
|---|---|---|
| **Home** | Dashboard | Weekly Briefing starts as a dashboard section. It can become a page when content and evidence warrant it. |
| **My Team** | Team Overview, Matchup Center | Schedule starts as a Team Overview tab. Lineup Center waits for its evidence gates. Career links to the manager's profile. |
| **Players & Moves** | Player Explorer, Waiver Wire, Trade Center | Comparison belongs inside Player Explorer. Streaming belongs inside Waiver Wire. Trade Finder, Builder and Evaluator share Trade Center. Preserve the existing Opportunity Explorer until the replacements cover its workflows. |
| **League** | League Overview, Standings & Power, League Matchups, Playoff Race, Teams & Managers | Current and Career tabs share a manager profile. League Activity starts as a section linked from Overview. Scenario, rooting and probability history views belong inside Playoff Race when supported. |
| **History** | Season Archive, Records & Leaderboards, Head-to-Head, Draft History | Champions, brackets and season awards live inside Season Archive. Historical metrics belong beside their seasons or records. Moves That Mattered and League Honors & Culture start as links from the archive or records when migrated. |
| **Tools** | Simulation Center, Data & Model Center | Keep existing run details inside Simulation Center. Put local display defaults in a settings panel. Scenario Library waits for an approved persistence design. |

Introduce sidebar links as their workflows become usable. Retain the existing analytical views during the transition. Use the same keyboard order and labels on mobile, close the drawer after navigation, and restore focus appropriately after dismissal.

## First League Overview

| Position | Content and action | Authority |
|---|---|---|
| Header | League and season, latest verified snapshot time, link to data context | Canonical War Room snapshot |
| Main column, first | Recent settled results and upcoming matchups where supported. Link to League Matchups. | Current snapshot and verified period evidence |
| Side column, first | Standings leaders with W–L–T and points. Link to Standings & Power. | Canonical team standings |
| Main column, second | Latest completed champion and a bracket link. | Versioned historical archive |
| Side column, second | Dated playoff summary from an applicable retained run and a link to Playoff Race. | War Room run with snapshot identity and coverage |

Keep the dashboard readable without a manager selection, archive or applicable forecast. If the run belongs to an older snapshot, show its date and identity in its own run context. Use an explicit no-applicable-forecast state on the current overview. Home is personalized when a perspective is selected. League Overview remains league-wide.

## Route and context rules

- Proposed stable locations are `/home`, `/my-team/matchup`, `/league/overview`, `/league/matchups`, `/league/playoff-race`, `/history/seasons/:year`, and `/tools/simulation`. Preserve reload and browser back/forward for the approved routes. Add new route handlers only in the implementation package.
- Put shareable page context in the URL, such as archive year, matchup period or a public-facing internal manager key where appropriate. Keep the local perspective default and snapshot selection in their own state. Never put raw ESPN member IDs or private run payloads in a shareable URL.
- Keep public Record Book fragment links operational during migration. Define a translation table before any future redirect or replacement. Keep missing, invalid and unavailable contexts distinguishable.

## Manager walkthroughs

| Goal | Path through the design | Acceptance check |
|---|---|---|
| Check my upcoming matchup | Home → upcoming matchup → My Team → Matchup Center | A mapped perspective carries into the detail. Without a mapped manager, prompt for a selection or link to league-wide matchups. No unsupported lineup or live advice appears. |
| Find the league's playoff odds | League → League Overview → playoff summary → Playoff Race | Show odds from the applicable War Room run with its snapshot and uncertainty. Provide a Simulation Center link when a run is needed. |
| Find the 2025 champion and bracket | History → Season Archive → 2025 → championship bracket | The year and bracket can be reopened directly. Champion, title series and final standings reconcile to the published history. Preserve the current league snapshot when returning to League. |

The design walkthrough above traces the three paths using the proposed destinations. Browser behavior, keyboard flow, mobile layout and source reconciliation remain implementation checks. Phase 1 starts with the shell and source-backed League Overview slice after the navigation and identity decisions are accepted.
