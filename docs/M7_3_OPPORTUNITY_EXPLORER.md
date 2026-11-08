# M7.3 Opportunity Explorer

Status: **IMPLEMENTED — REVIEW PENDING (30 October 2026)**.

Governing contracts: `MASTER_SPEC.md` M7.3, `ROADMAP.md` M7.3, the accepted M5.8 retained-result semantics, the API 1.0 background-run contracts, and `UI_DESIGN_VISION.md`.

## Input and trust contract

The workspace is read-only. It discovers retained, completed `action_search` runs with available results for the active canonical snapshot and selects the newest matching result deterministically. It does not launch, resume, cancel, or modify analysis.

Before display, the client validates API version, run identity and type, run snapshot identity, result provenance snapshot identity, and canonical snapshot SHA-256. Candidate pages, candidate details, and completed-state details must retain the selected run and candidate/state identities. Stale requests are aborted and ignored. Missing, active, failed, expired, mismatched, and incomplete results are explicit states rather than permission to combine evidence.

Candidate retrieval uses the backend's bounded `limit=25` pagination and supported server-side ordering/filter fields. The browser does not download the full result universe, re-rank a partial page as though it were exhaustive, or infer omitted actions. Search integrity reports candidate coverage, state-evaluation coverage, statistical precision, ranking resolution, stop reason, retained paths, and unique states independently.

All discovery state is keyed by canonical snapshot identity and selected run identity. Changing either remounts the analytical workspace and clears filters, sort, page offset, candidate page, selected path, detail, completed-state detail, comparison selections, errors, and loading state. Summary, candidate-page, candidate-detail, and completed-state requests each use cancellation plus latest-request and response-identity checks. Discovery, path-detail, and completed-state failures are independent terminal UI states; a failed request never leaves a perpetual loading indicator or permits a late response to overwrite the active context.

## Visual representation decisions

M7.3 applies the [visual-first information design standard](UI_DESIGN_VISION.md) without turning categorical records into decorative charts:

- **Candidate impact — signed diverging bars.** The metric is explicitly labeled **requester championship-probability change**, already supplied in probability points. A common zero baseline exposes positive, negative, and exact zero direction without recomputing analytics. Exact values remain adjacent; null is **Unavailable** with no mark. It is not described as a league-wide objective.
- **Action path — ordered transaction flow.** Trades, chains, acquisitions, outgoing/incoming players, drops, counterparties, and conditional steps are chronological relationships, so a numbered flow is clearer than a flat record table.
- **League impact — paired signed-interval plot.** Every team shows baseline and scenario probability text plus the engine-supplied paired difference and interval on a common zero-centered probability-point axis. This does not mix probability units with difference units or imply independent intervals.
- **Equivalent paths — shared-state identity annotation.** Candidate paths that reach one canonical completed state explicitly share one evaluation. The UI never treats duplicate paths as additional statistical evidence.
- **Local comparison — compact comparison tray.** Up to four retained paths can be visually compared without changing server-side search coverage or claiming a global optimum.
- **Coverage and authorization — categorical integrity strip and badges.** Candidate/evaluation completeness, precision, ranking resolution, transaction authorization, structural-hypothetical status, and conditional waiver status are exact categorical facts; aligned text is more honest than an arbitrary chart.

## Semantics and safeguards

`transaction_authorized` and `structural_hypothetical` remain distinct. The latter is not presented as ESPN-executable merely because its final roster is structurally legal. `conditional_waiver` remains contingent; the application invents neither claim-success probabilities nor counterparty acceptance. Candidate caveats and accepted path assumptions are shown separately. Evaluated/unevaluated, statistically competitive/not competitive, individual precision, and authoritative run-level ranking resolution remain separate concepts.

An evaluated candidate may still lack individual precision. A statistically competitive candidate is not necessarily the confirmed best action. A completed run may still have incomplete candidate coverage, incomplete numerical evaluation, incomplete precision, or unresolved ranking. All four are visible independently. Probability-point values are displayed as supplied and are never multiplied by 100 again.

The explorer deliberately remains useful when ranking is unresolved. Every retained candidate path remains pageable and inspectable, evaluated paths expose their available requester and all-team evidence, and unevaluated paths remain visibly unavailable rather than disappearing. “Ranking unresolved” qualifies comparisons; it does not suppress valid partial evidence or promote a one-state evaluation to a global optimum.

## Search construction handoff

Simulation Center constructs the Opportunity Explorer's candidate universe from the active canonical snapshot. Player selection is identity-first: display name, position, NFL-team identifier, and fantasy ownership are visible, while raw IDs remain only in the advanced compatibility surface and submitted API payload. Requesting-team outgoing players, selected-counterparty incoming players, and unowned acquisition players are separate pools. Counterparties and positions support all, one, or several selections.

An empty player selection has an explicit meaning: include every eligible player in that resolved pool. Immediately before submission the frontend expands that choice into sorted explicit player IDs in the unchanged API 1.0 `candidate_search` contract. The preview reports resolved outgoing, incoming, acquisition, counterparty, and unique-trade-player counts; resource limits remain separately visible because the preview is a declared universe, not a promise of exhaustive completion.

## Accessibility, responsive behavior, and scale

Every graphical value has exact text or an accessible label. Native controls expose filtering, sorting, selection, pagination, and comparison; keyboard focus remains visible. The two-column browser/detail layout becomes a single flow on narrow screens, with an explicit **View selected path** link between discovery and detail. Filter controls remain horizontally navigable, status labels and transaction steps retain legible type, and every league-impact interval includes explicit `CI … pp` text.

The sanitized fixture exercises positive, negative, and unavailable impacts; a multi-action structural/conditional path; two distinct paths sharing a state; all-team effects; incomplete coverage; and an eight-team configured league. Component tests also exercise a fourteen-team view, 350 retained paths, two completed searches, snapshot changes with overlapping candidate identifiers, out-of-order responses, page changes during detail retrieval, and independent request failures without introducing a league-size rule. Actual implementation captures live under `docs/images/m7-3/` for desktop dark/light, laptop, mobile, selected detail, league impact, comparison, and empty states.

## Limitations

M7.3 explores already-retained results only. It does not create searches, submit ESPN transactions, model trade acceptance, manufacture waiver success, or provide M7.4 manual scenario composition. Comparison is deliberately local and bounded; server-side pagination remains authoritative for the complete retained universe. M5 statistical and model limitations remain in force.
