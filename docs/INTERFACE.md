# Interface and user workflows

This is the current user-workflow and frontend reference. [README](../README.md) owns product capabilities/release status; [OPERATIONS](OPERATIONS.md) owns setup, configuration, private data and shutdown. Presentation does not own estimators, legality, API definitions or process recovery. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

For operating steps use [USER_GUIDE](USER_GUIDE.md); [T5](TECHNICAL_REFERENCE.md#http-api-dtos-and-evolution) and [T9](TECHNICAL_REFERENCE.md#application-and-frontend-securityintegration-contracts) describe executable transport and integration. This focused product reference retains visual hierarchy, responsive/interaction standards and accessibility requirements. Its implemented workspace descriptions are distinct from desired design treatments and outstanding WR-017 acceptance; component tests do not establish actual-browser accessibility. Current authority precedence is unchanged.

- [Visual standards](#visual-first-design-standard), [journey](#user-journey-and-navigation)
- [Identity and requests](#identity-and-request-isolation), [interpretation and units](#analytical-presentation-and-units)
- [League Overview](#league-overview), [Opportunity Explorer](#opportunity-explorer)
- [Trade Lab](#trade-lab), [Simulation Center](#simulation-center)
- [Accessibility](#accessibility-and-responsive-behavior), [limitations](#limitations-and-future-work)
- [Source/tests and visual evidence](#source-tests-and-visual-evidence)

## Visual-first design standard

The approved direction combines professional quantitative research, sports editorial hierarchy and interactive decision support in one analytical workspace. Meaningful visualizations take priority over dense tabulation; exact detail is progressively disclosed. This is a **design standard**, not a claim every proposed treatment exists.

For each major component:

1. Determine whether a meaningful visual improves understanding, comparison, exploration or decisions.
2. Consider charts, plots, heatmaps, distributions, brackets, diagrams, timelines, imagery and interactive graphical layouts.
3. Choose the encoding matching the actual relationship, preserving numerical accuracy, statistical uncertainty, accessibility, provenance and interpretation.
4. Use it as primary presentation when effective; retain exact values, accessible alternatives and supporting tables for inspection.
5. Use a table as the primary form only when graphics would be less clear, less precise, misleading or unnecessarily complex. Structured API records do not mandate cards/grids/tables.

Document the primary representation and rationale for major analytical components. Do not add decorative charts, arbitrary encodings or football imagery without explanatory value. Aligned numerals, concise labels, restrained team/matchup identity, modern sans-serif type, selective color, subtle separation and purposeful whitespace support analysis. Dark/light contrast, keyboard, screen-reader, reduced-motion and mobile/direct-control use remain first-class requirements. Future imagery needs licensed, provenance-documented sources; current monograms are not NFL logos or player portraits.

Mandatory tactical fields, constellations, spatial maps and the immersive room metaphor are superseded. They are not active routes or future commitments. Football/bracket motifs are appropriate when they explain actual structure. The visual language is reusable, not a frozen layout for every feature.

## User journey and navigation

Follow [OPERATIONS first use](OPERATIONS.md#launch-and-first-use), then deliberately select the intended canonical snapshot. The shell unifies snapshot selection, connection, observed active-run count, refresh, theme and source/build context. Desktop uses horizontal navigation; narrow screens use a menu. League Insights is visibly deferred.

| Task | Workspace |
|---|---|
| Understand league outlook | Submit a baseline in Simulation Center; inspect its retained result in League Overview. Without a usable result, observed standings remain explicitly factual. |
| Explore hypothetical actions | Configure a finite universe in Simulation Center; inspect retained paths/effects in Opportunity Explorer. |
| Test a specific exchange | Commit a Trade Lab draft, review pins/settings and intentionally evaluate aggregate final ownership. |
| Follow or reopen work | Monitor/cancel/select retained runs in Simulation Center and navigate to their matching result workspace. |

Opening a viewer or selecting a player does not silently launch analysis. Refresh is separately intentional: the panel exposes sanitized configured league/access mode, automatic safe-week detection and an advanced explicit override. Successful publication appends/selects an immutable snapshot. Closing stops observation, not work; reopening can rediscover the process-local refresh. Credentials stay server-side. [APPLICATION_SERVICE](APPLICATION_SERVICE.md#snapshot-selection-and-refresh) owns commit/error semantics; after a late failure, publication metadata can still identify a committed snapshot.

## Identity and request isolation

Stable canonical IDs and snapshot joins determine team/player/ownership/eligibility/slot/schedule/postseason context. Names are presentation, not identity. No universal league size, slot map, IR/flex rule or bracket is hardcoded into the interaction contract. A valid snapshot may lack a consumer capability.

The browser reads relative API 1.0 URLs through [lib/api.ts](../frontend/src/lib/api.ts); [types/api.ts](../frontend/src/types/api.ts) mirrors consumed DTOs. TypeScript assertions are not independent runtime authentication. Viewers explicitly validate schema, run/type, snapshot and canonical checksum before retained analytics; path/state/scenario identities are checked where applicable. Expired, deleted, unavailable and mismatched evidence cannot authorize mixing runs or reconstructing omitted results. [APPLICATION_SERVICE](APPLICATION_SERVICE.md#provenance-and-identity) owns public identity and evolution.

Effect-owned AbortControllers plus latest-request/version and response-identity checks protect results, errors, status and loading, even if transport resolves after abort. Retry replaces the relevant local request. Detail/page/state failures settle independently instead of stranding loading. Rapid shell selection remains available while an earlier detail request is pending; late data cannot overwrite the active snapshot or stored selection. Team focus cannot bridge leagues just because numeric IDs coincide.

Trade Lab is keyed by snapshot UUID/checksum; Explorer detail state by snapshot/run. Snapshot/run changes reset incompatible workspace selections. Simulation Center resets snapshot-specific selectors and observation. Local drafts/comparisons/filters are not a durable scenario library; navigating away can discard them. Context change or aborted fetch **does not cancel an accepted backend job**. Explicit run cancellation is separate; ambiguous submission failures are not automatically retried.

Only `war-room-theme` and `war-room-snapshot` preference identifiers persist in localStorage. Private rosters, drafts and results do not. The source/build drawer displays snapshot/schema/time/horizon and package/revision metadata; unavailable revision stays unavailable. An explicit revision string is not authenticated source bytes. [OPERATIONS](OPERATIONS.md#local-data-and-privacy) owns privacy safeguards.

## Analytical presentation and units

The frontend formats supplied results; it does not recompute probabilities, intervals, rankings, distributions, legality or completion. Preserve model, snapshot/run, configuration, seed/path and evidence context. **Verified live**, **Verified by tests**, **Unverified** and **Unsupported** remain distinct; fixtures/captures cannot establish ESPN availability.

| Value/status | Presentation |
|---|---|
| Absolute probability and interval | API decimals in [0,1] become displayed percentages; use supplied interval endpoints. |
| Paired `*_probability_points` fields | Display signed supplied values/paired intervals as `pp`, with no extra multiplication by 100. Preserve the existing public contract; [APPLICATION_SERVICE](APPLICATION_SERVICE.md#public-dtos-units-and-validation) documents its engine/service units. Do not subtract standalone interval endpoints to construct paired bounds. |
| Precision controls | Baseline submits `target_half_width_probability` as probability, then converts for probability-point display. Action search submits/displays `target_paired_half_width_probability_points` directly. |
| Null estimate/interval | **Unavailable** / **Interval unavailable**, never zero. An estimate can remain without a whisker; no invented zero-width marker. Explicit zero is a real value. |
| Completion/confidence | Execution, completed paths/capabilities, candidate generation, state evaluation, individual/league precision and ranking resolution are independent facts. |

Confidence intervals measure Monte Carlo sampling uncertainty, not model error. Incomplete paths/capabilities need prominent qualification: estimates conditional on completed valid paths are not unconditional league probabilities. Never silently renormalize missing work or call capped output exhaustive. A visible positive difference is not automatically significant; competitive does not mean confirmed best. Numerical attractiveness does not prove authorization or waiver success. Observed standings/points/seeds remain snapshot facts, not forecasts.

[Monte Carlo](MONTE_CARLO.md), [manual scenarios](MANUAL_SCENARIOS.md) and [search](UNIFIED_ACTION_SEARCH.md) own formulas, paired sampling, multiplicity and stopping. The interface does not redefine them.

## League Overview

Select a retained completed baseline with available result for the active snapshot. The newest usable matching run is the deterministic default (finish/creation time, then run ID); explicit selection or a preferred run from Simulation Center changes the complete evidence context. The viewer never submits work.

| Implemented form | Meaning |
|---|---|
| Ranked championship dot-and-whisker plot | Common percentage scale, exact text, supplied simultaneous intervals and keyboard/button team selection. |
| Ranked playoff probability bands | Distinct qualification grammar, exact values and bounded supplied interval markers. |
| Team-by-placement heatmap | Distribution shape and exact cell percentages, linked selection and semantic table alternative; no inferred expected-rank metric. |
| Focused team panel | Exact forecast metrics/intervals separated from observed record, standing and seed. |
| Progressive technical disclosure | Paths, stop reason, precision, provenance and limitations are exact facts without decorative charts. |

League-wide and metric precision warnings remain prominent after completion. Nulls follow shared rules. With no usable baseline, explain active/unsuccessful/no-result states and retain factual standings without invented analytics. Validation rejects unknown teams and mismatched provenance. Rapid run/snapshot changes cannot blend retained numbers.

## Opportunity Explorer

Read-only discovery defaults to the newest usable completed action-search result for the snapshot, with explicit/preferred-run selection. Create/monitor searches in Simulation Center; Explorer does not launch, resume, cancel or alter them.

Discovery uses **25-row server pages**, with global backend filtering/sorting before pagination. UI filters expose ordering, transaction authorization, acquisition condition and evaluated status. Sorts expose requester impact high/low, most paths and candidate ID. Backend allowlists own supported query semantics; the browser does not download/re-rank a partial universe as exhaustive. Summary exposes coverage/evaluation/precision/ranking, stop reason, retained paths and unique completed states independently.

Signed diverging bars show **requester championship-probability change**, not a league-wide objective. Select a path for ordered trade/chain/acquisition flow, counterparties, outgoing/incoming players, drops, caveats and assumptions. All-team paired plots show supplied baseline/scenario probabilities, signed changes and intervals; exact text accompanies marks, unevaluated impacts stay unavailable. Badges distinguish transaction authorization, structural hypotheses and conditional waivers without inventing success probabilities.

Candidate identity differs from `completed_state_sha256`: distinct ordered paths can share one canonical final state/evaluation. A local tray compares up to **four** paths and labels shared evidence; duplicates do not increase confidence. Changing snapshot/run clears filter/sort/page/selection/detail/state/comparison/loading/error state. Summary/page/detail/state requests have separate cancellation/latest-response guards; stale responses must not restore prior selection.

Keep available evaluated evidence inspectable when ranking is unresolved, including one evaluated state. Competitive/imprecise/unevaluated remain separate states. A top displayed path or local comparison is not global optimality or ESPN executability.

## Trade Lab

Choose canonical teams/players, search/filter using canonical slot labels and commit exchanges as chronological steps. Multiple teams can participate across uneven/multi-player steps. Protected pins prevent automatic capacity drops by completion; they are not ESPN locks or execution permissions.

### Draft and edit contract

`replayDraft()` is the single frontend bookkeeping authority for displayed ownership and original-to-final changes. It rejects unknown teams/players, duplicate step IDs, empty steps, duplicate same-step players, same-team transfers and chronological ownership discontinuities; emits at most one change per changed player. This is not a TypeScript legality engine.

Edits retain stable ID/index and replay only the prefix before that step for player selection. Save validates the complete proposed sequence including later dependencies before replacement. Invalid edits/removals/reorders leave committed steps untouched and show the error. Cancel discards only temporary choices. Independent steps reorder when full replay remains valid. Pins synchronize to final hypothetical ownership.

Temporary team/player/filter/editor choices do not change committed revision or invalidate completed output; the UI identifies their absence from that result. Committed step/pin/evaluation-setting changes invalidate prior output and abort local observation, without cancelling accepted backend work. Private draft payloads are not persisted in browser storage.

### Evaluate and inspect

Intentional Evaluate final state reviews seed, matched paths, confidence, precision, IR/waiver policies and provisional Normal parameters. Preflight validates finite/integral/range relationships, enums, nonnegative/nonzero dispersion and known rostered pins; backend remains authoritative. Player selection alone submits nothing.

The timeline is an **intermediate structural hypothesis**. API 1.0 accepts aggregate final ownership, not an executable ordered chain. The engine performs deterministic completion and paired evaluation. Forced drops/automatic additions remain separate from explicit actions. Weekly before/after starter/bench/reserve/vacancy explanations show projected-point consequences, not new source player projections.

Each submission has a unique `trade-lab-<snapshot>-<uuid>` scenario ID, distinct from run ID and engine checksum. Polling/results validate active revision, run/type/scenario, snapshot/checksum, schema, terminal completion and availability. Retained-result requests also use a monotonic token; abort alone cannot prevent a late ignored response overwriting another run. Stale, cancelled, interrupted, failed, rejected, unavailable and mismatched outputs are not successful evidence.

Simulation Center can reopen retained manual results, **without reconstructing their historical draft**: no request/draft endpoint exists. Scenario-library comparison, ordered-chain legality, acceptance and ESPN submission remain unsupported. Implemented forms are paired roster surfaces, numbered flow, signed paired intervals and explicit completion/lineup consequences; no drag action is required.

## Simulation Center

Configure → Monitor → History submits explicit baseline/action-search work and delegates result rendering. Trade Lab owns manual construction. Deliberate submission blocks duplicate in-flight requests and does not retry ambiguous transport failures.

### Workload and universe controls

Quick/Balanced/Detailed baseline and maximum-per-state path ceilings are **5 / 25 / 100**. These are editable workloads, not runtime/precision guarantees or league/engine limits. Custom settings and effective-request review distinguish preset/user values, submitted units, derived worker/path budgets and backend admission.

Primary action controls expose team, families, counterparties, roster-aware pools and declared-universe preview. Advanced settings expose statistics/sampling, orderings, trade/chain/action bounds, fixed/progressive evaluation, workers, candidate/state/path/time and response limits. Response rows do not bound computation or prove coverage.

Player choice displays name, position, NFL-team ID and fantasy ownership; raw IDs remain in advanced compatibility/submitted payloads. Outgoing requester, incoming counterparty and unowned acquisition pools are distinct. Roster assignments establish ownership; canonical FREEAGENT/WAIVERS then qualify unowned acquisition records, while ONTEAM cannot replace roster authority. Counterparty/position scopes allow all/one/several.

Each directional scope distinguishes All eligible, Include only selected and All except selected. Empty include-only is an invalid empty universe, never implicitly all. Team/counterparty/position/snapshot changes clear incompatible choices; family changes remove incompatible orderings. Native scopes expand to sorted explicit IDs before unchanged API submission. Populated legacy explicit-ID fields retain their compatibility precedence. Preview counts describe declared pools, not guaranteed exhaustive completion. Backend validates analytical rules/admission.

### Monitor, cancel and reopen

Polling continues while any discovered snapshot run is nonterminal, even when the selected run is terminal. Selection clears prior progress; version/identity guards isolate late progress and pending submissions. Transient errors back off/retry without claiming work stopped. Snapshot changes clear snapshot-specific state/observation and warn accepted work may continue.

The timeline uses backend status and `started_at_utc`. Queued/Running are active, `cancel_requested` is pending intent; Completed alone is successful terminal execution. Cancelled/Failed/Interrupted remain distinct unsuccessful outcomes. Text/icons/badges supplement color. Progress is actual stages/counters, elapsed time, coverage/precision/ranking and stop reason; baseline/manual updates are coarse. No inferred percentage or ETA.

Cancellation requires backend acknowledgement and is cooperative; committed completion can win. Terminal deletion is separately confirmed by identity. History searches/filters snapshot/type/status and shows expiry. The **browser requests the newest 50 records**, then filters locally, not complete lifetime history or a server snapshot query. API list capacity can be larger; 50 is the current browser choice.

Completed runs navigate by explicit preferred run ID and matching snapshot: baseline → Overview, search → Explorer, manual → Trade Lab. Viewers independently validate provenance. Expired/deleted results stay unavailable. Export is **retrieved run metadata JSON only**, labeled scope/timestamp units/snapshot/checksum/run, not full candidate/state universes or saved request configuration. Numerical checkpoints are separate from interrupted application jobs and unsupported HTTP resume.

## Accessibility and responsive behavior

Exact text/accessibility labels accompany graphics; status cannot rely on color. Use native keyboard controls, visible focus, skip navigation, semantic headings and explicit loading/error/empty states. The placement matrix has a semantic table and narrow-screen local scroll rather than page overflow. Charts/detail collapse, Explorer links View selected path, paired rosters stack without dragging. Dark/light tokens and reduced-motion CSS support accessible contrast and motion.

Shell navigation/context drawers implement modal focus entry/wrapping, inert analytical background, Escape closure and trigger restoration. Do not generalize that tested guarantee to all panels: refresh declares dialog semantics but lacks equivalent tested focus containment. That accessibility review remains under [WR-017](BACKLOG.md#interface-follow-up-disposition), not a claim of full compliance.

Axe/component tests supplement manual keyboard, screen-reader, contrast, responsive and usability review. Synthetic league sizes demonstrate configurable rendering, not universal live compatibility. No new browser/accessibility certification is implied here.

## Limitations and future work

See [README](../README.md#current-capabilities) for availability and [BACKLOG dispositions](BACKLOG.md#interface-follow-up-disposition) for follow-up. Contextual-help glossary, denominator-backed progress bars and runtime estimation remain unimplemented; estimates would need approved current-hardware evidence and uncertainty, not old timings or stage-name inference. League Insights, Weekly Deep Dive and scenario comparison are deferred.

Visual/trade/waiver improvements remain exploratory under WR-017. Superseded spatial concepts are not commitments. Helper duplication, old prototype selectors and possible virtualization require scoped evidence/approval under WR-005/007; no cleanup/redesign is authorized here. Approved undelivered query/benchmark/analytical obligations remain in their owning domains and BACKLOG.

## Source, tests and visual evidence

| Source / existing tests | Contract |
|---|---|
| [App](../frontend/src/App.tsx) / [tests](../frontend/src/App.test.tsx), [refresh](../frontend/src/LeagueRefresh.tsx) | Shell/snapshot races, preferences, drawer focus, refresh identity/publication/observation. |
| [Overview](../frontend/src/LeagueOverview.tsx) / [tests](../frontend/src/LeagueOverview.test.tsx) | Retained baseline selection, percentages/intervals/placements, null/incomplete/precision states and late responses. |
| [Explorer](../frontend/src/OpportunityExplorer.tsx) / [tests](../frontend/src/OpportunityExplorer.test.tsx) | Server paging/filtering, unresolved ranking, shared path/state evidence, comparison and isolation. |
| [Trade Lab](../frontend/src/TradeLab.tsx) / [tests](../frontend/src/TradeLab.test.tsx) | Replay/edit invariants, revisions, pins/settings, intentional evaluation, retained identity and completion/lineups. |
| [Simulation Center](../frontend/src/SimulationCenter.tsx) / [tests](../frontend/src/SimulationCenter.test.tsx) | Preset units, native/legacy scopes, preflight, lifecycle races, active runs, history/navigation. |
| [Styles](../frontend/src/styles.css), [client](../frontend/src/lib/api.ts), [DTOs](../frontend/src/types/api.ts) | Themes/responsive/motion and consumed public shape. Backend tests remain the numerical/API oracle. |

[CONTRIBUTING](../CONTRIBUTING.md#tier-1-fast-local-checks) owns current verification commands and side effects. Synthetic fixtures establish **Verified by tests**, not live ESPN or actual-browser evidence.

Selected visual references: [precision-qualified Overview](images/m7-2/league-analytics-precision-limited-dark-1440x1100.png), [shared-path comparison](images/m7-3/opportunity-comparison-dark-1440x1100.png), [edit-in-place](images/m7-4/trade-lab-edit-in-place-dark-1440x1200.png), [cancellation pending](images/m7-5/simulation-center-cancellation-requested-dark-1440x1200.png). These are **historical sanitized implementation captures**, not current live output or new screenshots. Source/tests determine current behavior. Old shell captures and spatial prototypes are historical/superseded, not active layouts. D3 retains all assets and capture/prototype tools unchanged; retention does not prove current accuracy.
