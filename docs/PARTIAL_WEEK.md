# Partial-week evidence and execution

This reference owns temporal evidence and partial-week execution permission. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

## Current capability map

All partial execution below is **engine-only, Verified by tests** with synthetic evidence. It is not automatic live ESPN translation, public HTTP support, browser execution or new **Verified live** calibration. [DATA](DATA.md#live-and-temporal-evidence) owns actual field observations; this reference owns evidence permission and current-week composition.

| Layer | Implemented support | Fail-closed boundary |
|---|---|---|
| Ordinary whole-week V1 | Existing optimizer/weekly transactions from a verified unplayed boundary | The current week must begin its independently supported full matchup period; cannot skip earlier contributions or unresolved settlement. v2/v3 read compatibility supplies no new live authority. |
| Companion and deterministic composer | `PartialWeekEvidence` artifact 1; fixed completed actuals, explicit unstarted realizations or explicitly approved deterministic remaining values | Source/snapshot/timing, exact observed slots/capacity, locks, observed-score reconciliation and cutoff/carry must validate. No conditional distribution is inferred. |
| Deterministic partial-season execution | `DeterministicPartialWeekInput` to the existing coordinator, first week once then ordinary future weeks | No first-week transactions/retroactive initial ownership or ledger changes. Full period membership, cutoff and applicable prior postseason history required. |
| Narrow partial Monte Carlo | `MonteCarloPartialWeekInput`; every rostered current player must be unstarted with approved matching nonmissing canonical projection | Completed, in-progress, missing, nonparticipating or unresolved relevant current-player evidence fails. Supplied legal lineup is preserved. |
| Application/browser | Ordinary V1 and factual nullable live-state summaries | No public partial request fields, automatic companion creation, current-week action/lineup recommendations or conditional remainder sampler. |

[Season](SEASON_SIMULATOR.md) owns deterministic weekly/bracket resolution, [Outcome Model](PLAYER_OUTCOME_MODEL.md) owns draws, [Monte Carlo](MONTE_CARLO.md) owns numerical identity/aggregation, and [Transactions](TRANSACTIONS.md) owns hypothetical ownership/legality. This is one coordinator with an opt-in input, not a second simulator.

- [Companion identity and preservation](#companion-identity-and-preservation)
- [Observed assignments and locks](#observed-assignments-and-locks)
- [Player states and deterministic composition](#player-states-and-deterministic-composition)
- [Standings and period authority](#standings-and-period-authority)
- [Deterministic season integration](#deterministic-season-integration)
- [Monte Carlo subset](#monte-carlo-subset)
- [Deferred evidence and capabilities](#deferred-evidence-and-capabilities)

## Companion identity and preservation

`PartialWeekEvidence` is an immutable frozen/slotted companion independent of canonical schema versions 2/3/4. `PARTIAL_WEEK_ARTIFACT_VERSION = 1` does not revise published snapshots. It carries snapshot_id/league/season/current scoring period and matchup period/full scoring weeks, UTC retrieval interval, sorted unique source_ids, evidence_digest_sha256, authority, unresolved_capabilities, players, observed lineups, matchups and standings_cutoff.

`evidence_basis_digest()` hashes lw2.2a-evidence-basis-v1 with snapshot UUID/league/season and the selected source IDs/exact payload digests. Every source must exist and have a digest. This basis hash is **not a hash of all companion content**. `partial_week_to_json()` separately emits deterministic compact ASCII JSON with artifact_type=partial_week_evidence, artifact_version=1, artifact_sha256 and complete evidence. SHA-256 covers canonical evidence bytes. `partial_week_from_json()` checks the envelope/checksum and reconstructs unresolved records; consumers must still validate them against the referenced snapshot. Replacing content plus its checksum is not independent authentication.

Preservation validation requires the published snapshot identity, aware UTC interval within the immutable snapshot retrieval interval, canonical player/team/slot relationships, exact source basis, finite signed values and coherent unique coverage. EvidenceAuthority distinguishes verified_live, verified_by_tests, unverified and unsupported. These are supplied provenance assertions, not an independent ESPN-byte authenticity mechanism. Synthetic authority never becomes live evidence merely because execution accepts a fixture.

`validate_partial_week_evidence()` preserves coherent unresolved records. `require_executable_partial_week_evidence()` adds executable cutoff/lock/score/remainder checks; the season coordinator additionally requires verified companion-level authority. Serialization alone does not authorize execution. Checksum-valid malformed companion deserialization can still raise raw KeyError/enum ValueError; it remains non-executable, with error-taxonomy normalization tracked as WR-006.

## Observed assignments and locks

`ObservedLineupAssignment` records player/team/slot, derived starter/bench/reserve role, exact canonical eligibility, lock state/authority, source and observed_at_utc. Observed assignments must exactly match canonical `RosterEntry` player/team/slot records, not merely form an eligible replacement lineup. Require complete roster coverage, one player once, configured slot capacities and coherent source/times. A forged eligible starter/bench/flex exchange is not observed truth.

Canonical rosters are as-of retrieval, not historical weekly lineups. Execution requires each assignment's source view to include mRoster and either an explicit scoringPeriodId matching the companion or an unparameterized current roster request whose snapshot live current period matches. The simulation horizon cannot establish timing. Missing/mismatched timing is preservable but fails execution.

LockState is verified_locked, verified_changeable or unresolved. Locked/changeable determinations require supplied verified authority; a started player cannot be declared changeable. Kickoff and NFL game state cannot replace verified player-specific lock permission. Slot kinds/capacities and flex eligibility come from league configuration, not a universal roster template.

`ProposedLineupAssignment` is separate from immutable observed evidence. `validate_proposed_lineup()` requires exact observed-player coverage, unchanged ownership, eligibility/capacity and every locked/unresolved assignment unchanged. Only verified-changeable players may move among legal slots. Executable composition rejects unresolved locks even if the proposed slot is unchanged. No current-week optimizer or recommendation service is implied; future weeks do not inherit these current-week locks.

## Player states and deterministic composition

`PartialPlayerState` separates nfl_game_status, score_state, fixed_actual_points, full_game_projection, remaining_status and source_id. RemainingContributionStatus is not_applicable, approved or unresolved.

| PlayerScoreState | Required evidence / contribution | Deterministic execution | Narrow Monte Carlo |
|---|---|---|---|
| `completed_fixed` | Completed NFL game, confirmed finite actual (including zero/negative), no remainder | Fixed actual exactly once; any supplied resimulation value rejects | Rejected for rostered current players |
| `unstarted` | Scheduled game, no actual, approved finite full-game projection | Exactly one `unstarted_realized_points` value for a non-bye starter | Accepted only with matching nonmissing canonical projection |
| `in_progress` | In-progress game, confirmed actual-so-far, remainder semantics distinct | Starter requires APPROVED semantics and exactly one `approved_remaining_points`; no unstarted value | Rejected even with an approved deterministic remainder |
| `missing` | No invented actual/remainder | Preservable; executable evidence rejects missing states | Rejected |
| `nonparticipating` | No invented actual/remainder | Cannot be a contributing starter; no unused realization may be supplied | Rejected for rostered current players |
| Unresolved evidence | A capability, game/lock/remaining/authority condition, **not a sixth PlayerScoreState enum** | Preserve coherent evidence; block dependent execution | Rejected |

`compose_partial_week_scores()` first validates evidence/lineup and supplied finite values. Bench/reserve and explicit fantasy-team byes contribute no starter points. Completed starters receive no draw/value; unstarted starters get a whole-game realization; in-progress starters get fixed-so-far plus the supplied approved deterministic remainder. Duplicate/unused inputs, bench/bye values and inconsistent categories fail. Missing never becomes actual zero.

For each team, fixed_points and remaining_points are math.fsum of the respective starter components. Then:

`final_matchup_points = carry_in_points + fixed_points + remaining_points`

Expose each component and starter breakdown separately. Observed **current-week** team points must equal the sum of fixed starter actuals exactly; carry and simulated remainder cannot conceal disagreement. Preserve finite signed scores, without flooring/rounding/tolerance substitution. This reference composition is deterministic arithmetic over explicit values, not a probability distribution or permission to estimate an intragame remainder by projection − actual.

A verified completed zero is fixed zero; an in-progress observed zero remains a real actual-so-far value; missing is unavailable. A zero/negative current projection does not erase completed fixed points. Injury during a game does not independently authorize a zero remainder. Kicker/D/ST conditional methods need the same evidence and appropriate future calibration.

## Standings and period authority

`StandingsCutoffEvidence` names settled period IDs, through_scoring_week, current_matchup_period_included, authority and source. Executable evidence requires a verified cutoff explicitly excluding the current period. Deterministic season preflight additionally requires the cutoff to end **immediately before the first week of the actual matchup period**, include known preceding canonical periods and never settle a known current/future period. It does not independently authenticate supplied historical ESPN standings totals.

Companion full membership must agree with canonical schedule authority. With current live metadata, use independently supported full membership (including earlier weeks outside the simulation horizon), not a shortened stored list. Without live metadata the accepted canonical period/request-timing contract still applies. A different live current period cannot be omitted. [DATA](DATA.md#live-and-temporal-evidence) owns authoritative source interpretation and older-v4 fail-closed handling.

Mid-period matchups require verified finite prior carry for each team; first-period-week evidence cannot invent carry. For postseason starts, provide every preceding current-period `WeeklyTeamScore` with exact team/week coverage, no duplicates and math.fsum equal to each verified aggregate carry. Explicit canonical byes are separate from scheduled matchup sides. Seeded participants must match observed current matchups and downstream bracket obligations.

NFL completion/statsOfficial does not prove fantasy settlement, standings incorporation or stat-correction finality. Completed historical contributions/cutoff cannot be guessed from scores or wall clock. Corrections require a new immutable snapshot/evidence publication; old snapshots/results remain unchanged. Forward simulation resolves modeled remaining results; it does not claim that the observed real matchup is already settled.

## Deterministic season integration

`simulate_deterministic_season(..., partial_week=DeterministicPartialWeekInput(evidence, lineup, values, prior_postseason_scores=()))` is opt-in; default None preserves V1. The ordinary `DeterministicScoreScenario` contains **subsequent weeks only**. First-week scenario scores plus partial values would double-count and are rejected.

The existing coordinator composes the first week once, freezes the supplied legal starters, skips first-week essential transactions and rejects hypothetical initial ownership/slot-state changes or an existing transaction ledger. A separately validated legal lineup proposal can differ only under the lock rules above. There is no current-week trade/claim permission implied by a legal slot assignment.

Regular resolution reuses the existing weekly resolver, disabling only its ordinary positive-projection selection requirement for these already-validated starters. Ownership/eligibility/capacity and canonical values remain checked. Observed unfilled slots use observed_unfilled_slot where appropriate. Subsequent weeks restore the existing essential-transaction, optimization, scoring and postseason policies.

For postseason, replay prior team/week contributions through `apply_postseason_team_scores()`. Add only the current fixed+remaining week, **not aggregate carry again**, then accumulate future weeks through the same resolver. Restore authoritative full scoring-week membership and advance only after the entire period is complete, using configured original-seed/tie rules. Explicit bye teams have no score inputs.

Supported starts are single-week regular periods and reconstructible first-round postseason matchups/byes, including multiweek first-round carry with complete preceding history. Multiweek regular periods and later-round starts missing earlier bracket history fail closed; no winners, standings corrections or carry breakdown are inferred.

Deterministic result shape stays the same. Configuration provenance adds `partial_week:v1:<sha256>` over the complete serialized companion and sorted assignments, realizations and prior scores. The smaller source-basis digest alone is insufficient. Absent partial input adds no entry and changes no historical whole-week identity.

## Monte Carlo subset

`MonteCarloConfig.partial_week` accepts `MonteCarloPartialWeekInput(evidence, lineup, prior_postseason_scores=())`. Every rostered current player, **including bench/reserve**, must be UNSTARTED with APPROVED full-game semantics equal to a nonmissing canonical effective projection. Nonparticipating/bye classification is not an exception to that player gate; an explicit fantasy-team bracket bye separately causes its starter draws to be unused.

Validate before asking the outcome model for draws. Generate the unchanged **full canonical player-week universe once per path**. Map only legal current non-bye starters' draws into `DeterministicPlayerValue(unstarted_realized_points=...)`; remove all current-week scores from the ordinary scenario and invoke the existing deterministic coordinator with both inputs. No second draw set, completed-score resampling, unconditional in-game redraw or conditional distribution is added. Future weeks retain ordinary outcome/transaction policy.

The path summary retains the original complete scenario ID/score-content digest for matched-draw auditing. [MONTE_CARLO](MONTE_CARLO.md#configuration-run-and-path-identity) owns the partial-only fingerprint entry: full companion, sorted lineup/history and lw2.3a-unstarted-partial-week-v1. Evidence/assignment changes invalidate incompatible checkpoints; absent input preserves the whole-week payload. Sequential/batched/parallel/resumed results remain equal at identical supported paths and execution-compatible recovery policies.

Pregame equivalence requires identical snapshot/model/seed/path ranges/policies, supplied current starters matching V1's optimized lineup and no first-week acquisitions changing ownership. Team scores, matchup results, standings, postseason/path outcomes and aggregate probabilities then agree. Partial provenance and first-week transaction audit remain distinct. Other legal observed lineups may intentionally change outcomes with identical draws. Adaptive schedules can choose different path counts; compare equal prescribed ranges rather than seed alone.

## Deferred evidence and capabilities

No automatic ESPN-to-companion translator, public HTTP partial fields, browser partial execution, current-week action/lineup recommendations, unverified lock permissions or unreconciled cutoff/carry is supported. In-progress conditional distributions and wider Monte Carlo states require separate approval. A valid deterministic composition does not establish that a sampler for its remaining values exists.

The 11 November 2026 unplayed observation verified field presence, not active-game lock/remainder/settlement meaning; DATA preserves its source context. [WR-009/010/011/012/013/014/015](BACKLOG.md#numerical-and-partial-evidence-obligations) retain active-game source/actual/lock/settlement, narrower milestone approval, translation/API, expanded states, historical bracket support and conditional-method gates. No LW2.3B work is authorized by accepted pregame tests or its accepted performance checkpoint.

Approved future conditional research requires immutable intragame player/game/clock/actual/live-projection/final-score/position/pregame observations; retrospective final rows cannot reconstruct vintages. Evaluate zero/downside and signed support by position, group/clock/context calibration, selection/latency/corrections, injury and game-script dependence, held-out proper scores and decision sensitivity. Do not infer remaining points by subtraction, clipping or an unconditional redraw; no synthetic/live-calibration promotion or new independence assumption for in-game remainders.

Source: [foundation](../src/fantasy_football/partial_week.py), [serialization](../src/fantasy_football/partial_week_serialization.py), [season](../src/fantasy_football/season_simulator.py), [Monte Carlo](../src/fantasy_football/monte_carlo.py). Evidence: [composition/observed-lineup adversaries](../tests/test_partial_week.py), [cutoff/carry integration](../tests/test_partial_season.py), [draw-once/worker/checkpoint/golden parity](../tests/test_partial_monte_carlo.py). These establish **Verified by tests**, not new ESPN live validation. [MC performance evidence](MONTE_CARLO.md#performance-evidence) owns the accepted historical benchmark context; D6 makes no code, mathematical or performance change.
