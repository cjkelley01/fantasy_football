# League Configuration Audit

This standing audit reviews production paths that can affect a simulated
league and was refreshed during the pre-M3 consolidation. The standing rule is that a league-specific mechanic must be API-derived
from the immutable canonical snapshot, supplied through validated configuration,
or explicitly identified as an approved modeled policy. Current-league numbers
may appear in sanitized fixtures and historical evidence, but not as production
simulation defaults.

## Configuration inventory

| Area | Classification | Source and implemented boundary |
|---|---|---|
| League/team identities and count | API-derived | `LeagueSnapshot.teams` and `LeagueRules.team_count`; canonical validation requires exact reconciliation. |
| Roster slots, capacities, eligibility, IR, and position limits | API-derived | Canonical lineup-slot and position-limit records. M2.1, evolving state, and V(R) iterate these records without named-position or league-size constants. |
| Projection availability | Approved modeled policy | The documented missing-projection policy derives effective zero while retaining raw missing evidence. M2.1 starts only positive effective projections. |
| Regular-season schedule and standings | API-derived | Canonical matchup periods/matchups and standings rules. M2.2 supports `allow_ties` and `TOTAL_POINTS_SCORED` only when their applicable evidence is understood; deeper ties remain unresolved. |
| Championship structure | API-derived with capability checks | Qualifier count, reseeding flag, round-period mapping, and tie-rule reference come from canonical rules. M2.4 supports fixed no-reseed graphs and explicitly reports unsupported placement cohorts. |
| Consolation enablement | API-derived tri-state | `True`, `False`, and unresolved `None` are distinct. Unknown enablement creates no bracket. |
| Consolation movement | Verified ESPN rule | Adjacent first-round seeds and bounded winner-up/loser-down movement generate even fields. Odd-field bye reentry remains unresolved. |
| Explicit consolation mapping | Configurable | Immutable `ConsolationLadderConfiguration` receives full graph/rank preflight. Canonical source ID or approval provenance is retained; odd continuation requires one. |
| Postseason scoring periods | API-derived | Championship and consolation round lists independently reference canonical periods and their scoring weeks. |
| Postseason tie result | API-derived and verified | M2.4 requires an understood `higher_seed` interpretation; another or unresolved rule blocks the capability. |
| Acquisition type and limits | API-derived | Canonical acquisition type, budget flag, season/matchup limits, availability, holds, and waiver priority govern M2.3 state. |
| Traditional claim-to-last behavior | API-derived supported mode | Operational transaction execution is now gated to non-budget `WAIVERS_TRADITIONAL`; FAAB/alternative modes fail before processing. |
| Weekly modeled waiver boundary | Approved modeled policy | `MODELED_WEEKLY_V1` is typed and explicitly reported as an assumption, not ESPN timing evidence. |
| Reverse frozen-priority immediate free agents | Approved modeled policy | Explicit V1 manager-behavior assumption, used only inside the transaction capability now gated to its supported traditional-waiver league type. |
| IR recovery | Approved modeled policy or validated evidence | Projection-based recovery is labeled as an assumption; strict mode consumes player/week-specific validated evidence and preserves unknowns. |
| Undroppable protection | API-derived with interpretation safety gate | Snapshot-frozen player evidence and league enablement are preserved. Automatic dropping remains unavailable when polarity interpretation/evidence is insufficient. |

## Focused source findings

- `lineup_optimizer.py`, `weekly_engine.py`, `roster_value.py`, and the evolving
  roster validators derive teams, positions, slots, capacities, weeks, and
  player eligibility from canonical data. No material current-league-size or
  named-position template was found.
- `postseason.py` derives qualifier and consolation populations, rounds,
  periods, byes, and seeds from canonical settings. The 10/12/14/16-team and
  4/6/8-participant values are tests only.
- `espn_adapter.py` recognizes ESPN lineup slot IDs 20 (bench) and 21 (IR) as
  verified ESPN identifier semantics, not configured capacities. Slot 25 and
  other observed-only IDs do not become playable. The 32-team check is the
  supported NFL universe invariant, not a fantasy-league team-count template.
- `future_data_audit.py` now derives its verified schedule-coverage description
  from the command's explicit expected team count. Historical documents retain
  the observed 14-team result as evidence, but neither diagnostics nor simulation
  promote it to a universal invariant.
- `weekly_transactions.py` and `transaction_cycle.py` previously allowed the
  approved traditional/reverse-priority V1 policies without independently
  checking the canonical acquisition type. This was **problematic hardcoding**:
  an FAAB league could have received traditional priority mechanics. Both
  operational and reference entry points now fail unless configuration is
  verified non-budget `WAIVERS_TRADITIONAL`. This defect would have blocked
  M2.5 integration and is corrected with adversarial tests.

No other material production assumption tied to the current fantasy league's
10/12/14/16 team counts, six qualifiers, eight consolation participants, or
current slot mix was found in the reviewed M1/M2 modules.

## Unsupported configurations

- Championship reseeding and complete placement sub-brackets for elimination
  cohorts larger than two.
- Odd-participant consolation continuation without approved bye-reentry
  evidence/configuration.
- Unknown consolation enablement.
- Postseason tie rules other than the understood higher-seed behavior.
- FAAB and alternative waiver execution policies.
- Unverified real-world waiver processing timestamps and unresolved future
  strict-IR evidence.

These limitations are capability-specific. Structurally valid snapshots remain
inspectable and reproducible; only the affected simulator operation fails.

## M2.5 integration

The deterministic season coordinator iterates every team in the configured
canonical league and carries configuration/evidence classifications into path
diagnostics. The current 14-team league is exercised as a representative
acceptance fixture, not the production universe; a separate four-team complete
path uses the same API without source changes.

Weekly priority handling is configuration-driven. `waiver_order_resets=True`
uses the verified inverse-modeled-standings reset, false preserves the dynamic
queue, and null fails preflight. Each advanced cycle freezes its actual opening
queue for the separately approved reverse-priority free-agent policy. No hold
is cleared by cycle advancement. The existing acquisition gate continues to
reject FAAB and alternative modes. No new material league-size or current-slot
hardcoding was found during M2.5 integration.

Postseason acquisition participation is not exposed by a currently verified
canonical ESPN field. M2.5 therefore makes it explicit and provenance-bearing:
the default all-team mode is the approved `all_teams_continue_essential_acquisitions_v1`
model assumption; an active championship/bye/consolation-only mode requires a
canonical understood manual-rule interpretation with a validated configuration
key, approval, audited gap and source; and unresolved mode stops before a
material acquisition. An arbitrary label or source ID cannot establish this
authority, and no verified ESPN evidence provider is currently implemented.
Active participation is derived from supported remaining bracket obligations,
not every historical non-complete record: current byes and graph-proven future
games qualify, while stale byes and fully completed placement obligations do
not. Scored-lineup participation remains distinct. This policy is league-size
independent and filters actors without deleting inactive teams from shared
ownership or valuation state.
## M2.6 probabilistic-layer configuration

The repeated-season runner derives league size, horizon, player-week universe,
lineup rules, acquisition behavior, qualifier count, and placement range from the
canonical snapshot and existing validated policies. Its new configuration is
limited to model identity/dispersion, master seed, exact path range, existing IR
and transaction policies, optional validated consolation mapping, and whether
complete league-wide output is mandatory. These are approved explicit simulator
inputs, not ESPN fields. No new league-size, position, playoff, or current-season
hardcoding was found. Unsupported deterministic capabilities remain unsupported
inside each path rather than receiving probabilistic fallbacks.

The final M2.6 fingerprint serializes every currently supported simulator policy
and explicit consolation provenance rather than trusting labels. It canonicalizes
only known unordered canonical record collections; nested sequences and model
parameters retain their order, so semantically ordered changes alter identity.
Named semantics versions are also hashed. Acquisition-count and eligibility-evidence overrides are not exposed
by the M2.6 runner; adding either later requires including its complete validated
content in the fingerprint before batch combination is permitted.

## Pre-M4 refresh

The M3 production review found no new league-size, roster-shape, scoring-format,
or postseason-format constant in the outcome, precision, parallel, or recovery
layers. Those layers consume the configured canonical team universe and existing
validated simulation policies. Quick/detailed path budgets and worker counts are
runtime profiles, not league rules. A corrected reusable-worker check now binds
each process pool to the seed-bearing Monte Carlo run identity as well as the
seed-independent, partition-compatible simulation fingerprint; this changes no
league mechanic. Remaining supported and unsupported modes above are unchanged.

## M8.4 release re-audit

The final release audit repeated the production-source search across canonical
translation, validation, season/postseason execution, transactions, V(R), Monte
Carlo, trade/action search, application services, HTTP contracts, background
execution, and frontend request construction.

- Team count, roster slots, scoring, qualifiers, periods, and supported rules
  remain **API-derived** canonical inputs.
- Provenance-bearing consolation periods and other evidence gaps remain
  **explicit validated configuration** and fail closed when absent or
  contradictory.
- Waiver processing, postseason participation, essential-only acquisitions,
  and deterministic completion remain named **approved modeled policies**, not
  universal ESPN claims.
- HTTP deadlines, path ceilings, worker counts, page sizes, retained-result
  limits, and Quick/Balanced/Detailed presets are **application resource
  defaults**, not league mechanics.
- Fourteen-team, six/eight-participant, seed, player-ID, and particular-week
  values found in tests, benchmarks, evidence narratives, and examples are
  **fixture/test/evidence constants**.

No **unjustified production assumption** was found. The text in
`league_rules_audit.py` describing investigated historical six-team and current
eight-team consolation evidence is diagnostic provenance, not a production
postseason selector. The manual consolation mapping and provenance contract are
unchanged.
