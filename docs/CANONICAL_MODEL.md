# Canonical Snapshot Contract — Schema Version 3

## Scope

M1.1 defines the immutable ESPN-independent contract between ingestion, persistence, and simulation. M1.2 translates live ESPN responses into this contract, and M1.3 assigns publication IDs and persists complete documents. The canonical package itself does not fetch ESPN data, access SQLite, compile simulation arrays, or implement league behavior.

The implementation is in `src/fantasy_football/canonical/` and uses frozen, slotted Python dataclasses and tuples. Records are deliberately flat and joined by stable identifiers. `LeagueSnapshot` is the logical container, not an ESPN response mirror. New snapshots use schema version 3; version-aware reading preserves existing schema-v2 documents.

Schema v2 adds immutable `LineupSlotNamespace.observed_slot_ids`, derived from the exact union of raw player eligibility evidence. This namespace is distinct from `LeagueRules.lineup_slots`, which contains only ESPN-configured capacities. An observed but unconfigured category such as slot 25 remains preserved but is not a legal roster destination. Occupied slots must have positive configured capacity and appear in the player's raw eligibility. Schema-v1 JSON is intentionally incompatible and rejected because it cannot express this distinction.

## Entities and keys

| Entity | Identity / role |
|---|---|
| `LeagueSnapshot` | Schema version, optional externally allocated snapshot ID, league ID/name and season identity, retrieval interval, explicit simulation horizon, rules, records, provenance, gaps, and validation metadata |
| `LeagueRules` | League-agnostic grouping of scoring, roster, acquisition, trade, standings, championship, consolation, and interpreted raw rules |
| `FantasyTeam` | Positive fantasy-team ID plus present cumulative standings, optional seed/rank/waiver/division state |
| `Player` | Signed nonzero player ID, default-position ID, eligible raw lineup-slot IDs, current NFL team/injury/acquisition state |
| `RosterEntry` | The only authoritative ownership relationship: player ID → team ID and occupied lineup-slot ID |
| `PlayerWeek` | Composite key `(player_id, season, scoring_week)` and the raw/effective projection contract |
| `MatchupPeriod` | Positive matchup-period ID mapped to ordered scoring weeks and only a `regular` or `postseason` calendar phase |
| `FantasyMatchup` | Positive matchup ID, period ID, bracket classification, zero-to-two known sides, and explicit scheduled/bye/partial/placeholder state |
| `NFLTeam` | Positive NFL-team ID and optional bye week; player NFL-team ID zero is a separate unassigned sentinel |
| `NFLGame` | Source game ID, scoring week, two NFL-team IDs, and optional UTC kickoff |
| `SourceReference` | Stable request/reference ID, HTTPS endpoint, raw view, parameters, UTC retrieval time, optional SHA-256 payload digest |
| `RuleInterpretation` | Raw field/value, optional normalized meaning, evidence status, source, and optional audited manual configuration provenance; values may be JSON scalars or immutable structured JSON |
| `RuleGap` | Named unresolved gap, reason, affected future capabilities, source, and whether audited manual configuration is permitted |

Schema v3 adds nullable `LeagueRules.undroppable_list_enabled` and its source ID plus nullable `Player.is_undroppable` and its source ID. Known values require valid source references; absence remains null. Raw ESPN `droppable` polarity is inverted only by the adapter (`true` means canonical `is_undroppable=false`). The separate `undroppable_player_mapping` interpretation records whether that semantic mapping is understood. Current live translation leaves it `unverified`, so automatic dropping is capability-blocked even though the raw Boolean is retained.

Default-position IDs and lineup-slot IDs remain separate namespaces even where their integers happen to overlap. Player IDs may be negative; zero is invalid. NFL team ID zero is allowed only on `Player` as the unassigned sentinel and is never interpreted as a bye.

## Ownership and current state

`RosterEntry` is the single authoritative ownership table. `Player` contains current acquisition status but no owner. A player without a roster entry is unrostered, though their acquisition status may still be `WAIVERS`, unavailable, or another raw state rather than immediately free.

Recognized current ESPN states are reconciled with that table: `ONTEAM` requires a roster entry, while `FREEAGENT` and `WAIVERS` require no owner. Unknown acquisition enums are retained without guessed semantics and do not invalidate an otherwise coherent snapshot. A populated raw owner on a roster entry must agree with its authoritative team ID.

The snapshot begins from cumulative current standings and current rosters. Schema v4 retains nullable `LiveScoringState` evidence and derives a safe `first_simulation_week` only from explicit game status, never the wall clock; an advanced manual override is labeled separately. Every included player has exactly one future `PlayerWeek` for every required week in the inclusive V1 range. Schema-v2/v3 snapshots remain readable without live-scoring metadata.

## Projection contract

| Raw record | Classification | Effective value | Effective source | Projected-lineup available |
|---|---|---:|---|---|
| Missing | `missing` | `0` | `missing_projection_policy` | No |
| Present and exactly zero | `explicit_zero` | `0` | `espn` | No |
| Present and positive | `present` | exact raw value | `espn` | Yes |
| Present and negative | `present` | exact raw value | `espn` | No |

Availability is derived as `effective_projection > 0`; no redundant boolean is stored. Raw and effective values must be finite. Values are neither rounded nor clamped. This availability rule does not constrain future realized fantasy scores, which may be negative when the later outcome model permits them.

## Rule interpretation

Allowed evidence states are `understood`, `unverified`, `unsupported`, and `missing`. An understood record requires a normalized interpretation. A missing record cannot contain a raw or normalized value. Unverified and unsupported records preserve valid raw source data without inventing an operational meaning.

Manual configuration provenance is all-or-nothing: configuration key, approval reference, and audited gap ID must be present together, the gap must exist, and it must permit manual configuration. The canonical snapshot may contain unresolved rules. `require_understood_rules(snapshot, rule_ids)` is the deliberately small boundary a future simulator capability can invoke; it is not a capability-permission framework.

Rule values use ordinary JSON scalars when sufficient. Structured evidence uses frozen `JsonArray` and `JsonObject` records; arrays retain order (for example, ordered tiebreaks), while object keys use canonical increasing order. Nested strings, numbers, Booleans, nulls, arrays, and objects round-trip deterministically. Mutable containers, duplicate or unordered object keys, unsupported values, and nonfinite nested numbers fail validation. Illustrative fixture structures are test evidence only, not verified current-league rules.

## Matchup calendar and bracket entries

`MatchupPeriod` is a calendar relationship, not a bracket classification. It maps scoring weeks to either the regular season or postseason. Championship, consolation, placement, and unresolved bracket entries may coexist in one postseason period through each `FantasyMatchup.bracket_type`; round numbers remain on the corresponding championship or consolation rule records.

`FantasyMatchup.entry_status` distinguishes a scheduled two-team matchup, a one-team bye, a one-known-team partial future pairing, and a zero-known-team placeholder. Regular-season operational periods require complete scheduled pairings for every team. Postseason periods permit explicit partial and placeholder entries so the snapshot never fabricates future participants. Known teams remain unique within a period, and unknown IDs, self-pairings, mismatched status/participant counts, or bracketless postseason entries are contradictions.

## Validation boundary

`validate_snapshot` is pure, deterministic, and non-mutating. Snapshot-acceptance invariants include:

- supported schema version and coherent league/season/horizon identity;
- timezone-aware UTC retrieval ordering and source timestamps inside the retrieval interval;
- unique team, signed player, source, matchup, period, NFL-team, NFL-game, scoring-rule, slot, interpretation, and gap IDs;
- one authoritative owner per player and valid team/player/slot references;
- occupied slot and position counts within dynamically supplied capacities/limits;
- eligible-slot references in the lineup-slot namespace;
- complete player-week horizon coverage and exact raw/class/effective/source relationships;
- finite scoring, standings, projection, and override numerics;
- matchup periods that cover every required scoring week exactly once;
- complete ordinary regular-season pairing coverage, while allowing explicitly classified postseason byes, partial entries, and zero-participant placeholders;
- concurrent championship, consolation, and placement entries in the same postseason period without conflating the calendar with a bracket;
- dynamically configured postseason round references without assuming a league shape;
- valid NFL references, at most one game per team/week, and exactly one game-or-bye state per required week;
- source references for projection and rule evidence; and
- structurally coherent interpretation and manual-configuration records.

Failures identify the entity, identifier, field, and invariant. Validation never fills a value, changes an enum, assigns an owner, creates a matchup, or supplies a rule default.

An unresolved consolation ladder or tie rule is valid canonical metadata. It blocks only future behavior that explicitly requires the missing meaning. Rule-gap records must name a nonempty unique gap ID and at least one nonempty, nonduplicated affected capability. Invalid references, duplicate ownership, malformed projection provenance, contradictory bye/game records, and malformed rule-gap metadata invalidate the snapshot itself.

## Deterministic quality reporting

`generate_quality_report(snapshot)` first invokes the authoritative canonical validator, then returns a frozen report separate from the canonical document. It reports identity and horizon counts, rostered/unrostered counts, required and observed player-week coverage, schedule/source counts, and projection categories. Percentages use player-week records as their denominator. Projection breakdowns are calculated independently by scoring week, raw default-position ID, and current ownership state.

Missing raw projections, explicit ESPN zeros, finite negatives, positive effective projections, unrostered players, and observed-but-unconfigured eligibility categories are data characteristics rather than structural failures. They appear as aggregate metrics or information diagnostics, not one warning per record. Rule gaps produce one deterministic warning with their affected capabilities, source, and manual-configuration eligibility; non-understood rule interpretations remain visible as limitations. Passing canonical validation does not assert that those capabilities are ready—callers still use `require_understood_rules()` for the specific rule IDs they require.

For the sanitized fixture, the overall projection summary contains 15 records: 12 present raw values, 3 explicit zeros, 3 missing raw values, 3 finite negatives, and 6 positive effective projections. The counts are illustrative test evidence, not live-league findings. Identical snapshots yield equal report objects and canonical JSON report output.

## Serialization

`snapshot_to_json` and `snapshot_from_json` implement schema versions `2` and `3`:

- JSON is indented, human-readable, and ends with a newline;
- object keys and all identity-bearing record collections have deterministic ordering;
- timestamps serialize as ISO-8601 UTC strings ending in `Z`;
- null, explicit numeric zero, negative IDs, negative projections, raw enums, gaps, and evidence states round-trip unchanged;
- JSON nonfinite numbers are prohibited;
- deserialization reconstructs typed immutable records and performs full validation; and
- v3 requires the league/player protection keys even when their values are null;
- v2 rejects v3 protection keys and reserializes without adding them; and
- any schema version other than `2` or `3` fails explicitly.

This remains an interchange contract: `snapshot_store.py` owns SQLite, compressed evidence, publication UUIDs, and whole-document checksums without adding simulation-path state to canonical entities. New publication requires v3. Existing v2 bytes, IDs, and checksums remain immutable and readable for their previously supported capabilities, but they cannot authorize automatic drops.

## Evolving transaction state

`fantasy_football.evolving_state` is a separate immutable simulation-path contract, not part of canonical JSON. `EvolvingLeagueState` records its source snapshot ID/version, scoring week, acquisition cycle and matchup-period identity, authoritative modeled `RosterEntry` tuple, per-player acquisition availability, current and frozen-initial waiver queues, independent season and matchup-period acquisition counts, player/week strict-IR evidence, explicit IR policy, transaction ledger, and named V1 assumptions. Every operation returns a new validated value; ownership is never duplicated into an independently mutable owner map.

Availability distinguishes rostered, immediate free agent, waivers, holding restriction, and unresolved. A dropped player enters `hold` and is prohibited from same-cycle reentry. Cycle advancement never silently releases that player. `release_held_player` requires a validated `ReleaseAuthorization` bound to the player, earliest eligible cycle, semantic holding/clearance condition, resulting acquisition state, evidence digest, and source; known clearance timestamps additionally require a decision-boundary timestamp at or after clearance. Direct construction creates only an untrusted assertion and is rejected. Thus an unrelated source ID is never release authority, unresolved timing remains held, and a validated release may correctly produce either `free_agent` or `waivers`. Successful traditional claims may explicitly move a team to the bottom while the window's initial queue remains frozen. Free-agent additions do not themselves alter either queue.

Pure primitives cover add, drop, compound replace, supported IR placement, explicit activation, priority movement/reset, evidence-backed hold release, and cycle advancement. They validate dynamic slot and position capacities, eligibility, ownership, acquisition limits, availability, undroppable evidence, and same-cycle restrictions. They do not select desirable transactions or orchestrate a weekly claim cycle. Compound calls build a candidate value and expose nothing if a later step fails.

Season-wide and matchup-period acquisition counters are distinct and enforce their corresponding dynamically configured limits. A finite limit with unavailable initial usage remains `unknown` and blocks only affected acquisition authorization. Matchup counts reset to zero only when `advance_cycle` crosses the canonical matchup-period boundary; they persist across multiple scoring weeks in one period, while season counts never reset. Failed operations increment neither counter. Individual game-lock evaluation, exact waiver clearance clock/timezone, multiple-claim ordering, and dropped-player reentry timing without semantic authorization remain unresolved and block only operations that require those facts. M2.3b deliberately provides no fallback.

IR policy is explicit. `projection_based` reports a positive-projection reserve occupant as an acquisition-blocking correction until explicit activation and records `approved_assumption` in the ledger rather than source evidence. `strict` consumes validated immutable `IREligibilityEvidence` bound to a player, decision week, semantic condition, determination (`eligible`, `ineligible`, or `unknown`), evidence digest, and source. A directly constructed record is not authoritative. A confirmed eligible occupant does not block acquisition; a confirmed ineligible occupant does; absent or unknown evidence produces a capability-specific unresolved failure. Strict activation requires an ineligible determination for that same player/week—an unrelated source ID and a positive projection are insufficient. Placement still requires supported injury evidence (`OUT` or `IR` in the currently verified snapshot-time contract), and neither policy starts or activates an IR occupant implicitly. Scenario state serializes deterministically through `state_to_json()` for audit/testing but is not an authoritative stored snapshot format.

### Eligibility evidence trust boundary

`validate_eligibility_evidence_artifact()` is the single implemented minting boundary for release and strict-IR determinations. It verifies the exact artifact bytes against the canonical `SourceReference.payload_sha256`, requires the dedicated `transaction_eligibility_evidence_v1` view and contract, parses player/boundary/condition/status claims, rejects duplicates or malformed relationships, and returns registered immutable determinations. Transaction execution performs no network access or raw-payload parsing; it accepts only objects minted by that validation step and retains source ID, evidence basis, and assumptions in state or ledger records.

The only currently implemented artifact classification is `verified_by_tests`, producing the explicit `validated_fixture` basis for sanitized controlled fixtures. It proves the internal trust boundary, not live ESPN semantics. `verified_source` is reserved for a future source-specific parser and cannot currently be minted by relabeling an arbitrary response. `approved_assumption` is used only for the already approved projection-based IR policy. No approved assumption currently releases ESPN waiver holds or supplies strict future IR status, so those operations remain unresolved without future verified evidence.

### Replaceable V1 assumptions

- Undroppable designations are frozen from the source snapshot for the entire modeled path; ESPN may change its list later.
- `projection_based` IR recovery is a model policy, not an ESPN injury rule. `strict` is available when projection evidence must not be used.
- Transactions occur in modeled weekly pregame windows rather than at individual real-world manager timestamps.
- Exact future clearance timing is not simplified: it remains unresolved and requires explicit evidence before a hold is released.
- Reverse-initial-waiver-priority immediate free-agent processing is approved for future M2.3d but is not implemented here and is not an ESPN rule.

## Representative fixture and tests

`tests/canonical_fixtures.py` defines a sanitized four-team snapshot with:

- owned and unrostered players;
- a signed negative D/ST ID;
- positive, explicit-zero, missing, and finite-negative projections over three future weeks;
- a regular matchup and a two-week championship period;
- two NFL byes and complete relevant NFL game coverage;
- concurrent championship/consolation entries, an unresolved placement placeholder, unsupported structured consolation evidence, ordered structured tiebreak evidence, and an unverified regular matchup tie rule; and
- request provenance and a raw payload digest placeholder.

An alternate fixture test removes IR and adds multiple flex slots, demonstrating that the validator does not impose the current league's roster template.

## Implemented and deferred boundaries

M1.2 implements ESPN translation, M1.3 implements immutable document persistence, and M1.4/M1.5 implement quality reporting and inspection. M2.3b implements schema v3 and the evolving-state legality primitives above. V(R), transaction selection, complete weekly waiver/free-agent execution, and lookahead remain M2.3c–e.

The M1.2 slot-25 finding is resolved in schema v2 through the observed eligibility namespace described above. No raw eligibility evidence is dropped and no absent capacity is fabricated.
