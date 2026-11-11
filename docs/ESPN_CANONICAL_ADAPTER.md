# ESPN-to-Canonical Adapter — Canonical Schema v4

## Status

M1.2 is **COMPLETE as of 17 October 2026**. M2.3b advanced adapter output to canonical schema version 3 while retaining the schema-v2 eligibility-slot correction and historical read support. LW1 advances new publications to schema version 4.

LW1 retains immutable NFL game-status and current-player actual-score observations. Its read-only diagnostic (`python -m fantasy_football.live_scoring_diagnostic`) reports sanitized aggregate raw-token and actual-record coverage. The parser is verified by tests; live token coverage, player participation semantics, correction finality, and official fantasy-matchup settlement remain unverified or unsupported and therefore fail closed.

On 17 October 2026, a read-only live run used explicit `first_simulation_week=8` because scoring week 7 was already active. The adapter retrieved the current league, current player authority, future week pages, and NFL calendar sequentially. Translation stopped before producing a snapshot because 97 players exposed raw eligible lineup-slot ID `25`, while `settings.rosterSettings.lineupSlotCounts` defined only IDs `0–24`.

That run correctly stopped under schema v1. Schema v2 now records every raw `eligibleSlots` identifier in immutable `LineupSlotNamespace.observed_slot_ids`. `LineupSlotRule` remains limited to slots ESPN explicitly configures, and roster occupancy still requires a configured positive-capacity slot that is also in the player's eligibility tuple. Slot 25 is not special-cased and does not become playable merely because it was observed.

## Responsibility boundaries

`fantasy_football.espn_adapter` separates:

1. Sequential authenticated retrieval into `CapturedResponse` and `EspnSourceBundle` records.
2. Network-independent translation through `translate_bundle`.
3. Existing canonical validation, deterministic serialization checking, sanitized reporting, and critical-state reconciliation.

`AdapterResult.raw_sources` retains in-memory raw mappings, request context, retrieval time, and SHA-256 digest for future M1.3 persistence. M1.2 does not write those payloads, allocate snapshot IDs, publish snapshots, or implement a database.

## Endpoints and source precedence

| Purpose | Endpoint/view | Authority and transformation |
|---|---|---|
| League settings, teams, rosters, standings, schedule | League endpoint with `mTeam,mRoster,mMatchup,mSettings,mStandings` | Initial current-state authority; parsed from raw JSON rather than library convenience objects |
| Current player ownership/status/identity | League endpoint, current `scoringPeriodId`, paginated `kona_player_info` | Current view is authoritative; reconciled exactly to roster entries |
| Future player-week projections | One paginated `kona_player_info` request series per required future week | Only matching season/week, `statSourceId=1`, `statSplitTypeId=1` records are projections; future wrapper ownership is ignored |
| NFL teams/games/byes | Season endpoint with `proTeamSchedules_wl` | Raw team IDs, `byeWeek`, game IDs, teams, scoring periods, and UTC kickoff times |
| Mutable-state reconciliation | A final league request | Ownership/slots, W/L/T, PF/PA, seeds, and waiver priority must exactly match the initial observation |

Every pool series must begin at offset zero, advance by the declared limit, contain no duplicate/overlapping IDs, and end with a short page. Every future week must return the same player universe as the current authoritative view. ESPN supplies no independent total count, so this is completeness against the verified pagination contract rather than an external universe.

## Projection translation

The adapter implements the approved canonical policy without using `espn-api`'s rounded/defaulted projection attribute:

- no matching weekly record → raw null, `missing`, effective zero, `missing_projection_policy`;
- matching finite zero → `explicit_zero`, effective zero, ESPN source;
- matching finite positive or negative value → `present`, unchanged effective value, ESPN source;
- malformed, duplicate, cross-week, wrong-split, or nonfinite matching records fail.

## Rule translation and manual configuration

Verified raw scoring, roster, acquisition, trade, standings, championship, and consolation fields translate into dynamic canonical rule records. Unknown or unresolved operational semantics remain `unverified`, `unsupported`, or `missing`; no waiver, tie, or consolation default is introduced.

Schema v3 also reads `settings.rosterSettings.isUsingUndroppableList` and raw current-player `player.droppable`. League and player values are nullable and carry exact request source IDs. Current pool, roster copy, and future views must agree on presence and known Boolean value. Raw `droppable=true` is represented as canonical `is_undroppable=false`, and raw `false` as `true`; absence stays null. The adapter retains `undroppable_player_mapping` as **unverified** because no private individual raw record was directly correlated to the ESPN “Can't Drop” UI. Consequently, live v3 ingestion preserves the proposed mapping but deterministic automatic-drop authorization remains blocked pending approval/corroboration.

Optional YAML configuration is restricted to audited gaps and requires:

- exact league ID and season;
- canonical rule ID and audited gap ID;
- configuration key;
- approved structured value;
- exact expected current ESPN raw value;
- approval reference; and
- HTTPS evidence URL.

The file SHA-256 and evidence URL become source provenance. A manual interpretation is stored separately from the original ESPN interpretation, so raw ESPN evidence retains its own source reference. Identity mismatch, unaudited gaps, duplicate rules, malformed/nonfinite values, or conflict with ESPN evidence fails. Store private league configuration under a name such as `config/league_rules.local.yaml`; `config/*.local.yaml` is ignored by Git.

Example structure (illustrative only, not an approved league rule):

```yaml
league_id: 123
season: 2026
rules:
  - rule_id: consolation_structure
    gap_id: consolation_ladder
    config_key: postseason.consolation_ladder
    expected_raw_value: false
    approved_value:
      rounds: []
    approval: review-reference
    evidence_url: https://example.invalid/league-rule-evidence
```

## Retrieval reliability

The adapter permits at most two retries after the initial request. It retries connection timeouts, connection failures, HTTP 5xx responses represented by the ESPN client, and request exceptions carrying 5xx responses. Authentication failures, invalid leagues, malformed payloads, identity conflicts, incomplete pagination, and canonical contradictions are not retried. Errors retain request role, scoring week, offset, and limit where applicable. Cookies are never placed in source metadata, logs, reports, or exceptions.

## Credential-free verification

Sanitized fixtures cover current ownership, three-page pagination, two future weeks, positive/zero/missing/negative projections, a negative D/ST ID, 32 NFL teams, games/byes, standings, waiver priority, postseason rule metadata, unresolved rules, approved YAML configuration, deterministic translation, and sanitized CLI output.

Adversarial tests cover incomplete and overlapping pages, inconsistent universes, ownership conflicts, invalid IDs, nonfinite/malformed projections, missing required settings, incomplete regular schedule, mutable-state changes, manual-config conflicts, malformed slot namespaces, and attempted occupancy of unconfigured eligibility categories. Updated test counts and live results appear in the acceptance section below.

## M1.1 schema amendment

The approved correction distinguishes the **observed raw lineup-slot identifier namespace** from **configured lineup-slot capacities**. `LeagueSnapshot.lineup_slot_namespace` is derived exactly from the union of player eligibility evidence. `LineupSlotRule` continues to mean an explicitly present ESPN capacity. Player eligibility validates against the observed namespace; roster occupancy continues to require an explicitly configured positive-capacity slot.

Schema v2 introduced this slot namespace. Schema v3 retains it unchanged and adds undroppable evidence. Schema v1 remains rejected. No universal ESPN slot mapping is introduced; meanings remain unknown unless separately verified.

## Live acceptance result

The repeated read-only run used explicit `first_simulation_week=8` through final scoring week 18 and passed canonical validation, critical-state reconciliation, and deterministic serialization. Sanitized counts were:

- 14 fantasy teams;
- 1,052 players: 234 rostered and 818 unrostered;
- 11,572 player-weeks, exactly 1,052 players × 11 required weeks;
- 7,227 present raw projections, including 2,814 explicit zeros; 4,345 missing raw projections; no finite negative projections in this observation;
- 49 future fantasy matchup records;
- 32 NFL teams, 165 future games, and bye metadata retained for the NFL universe;
- 63 captured read-only source requests;
- observed eligibility slot IDs `[0, 2, 3, 4, 5, 6, 7, 12, 14, 15, 16, 17, 20, 21, 23, 25]`; and
- exact reconciliation of roster ownership, standings/points, and waiver priority.

The identical in-memory snapshot serialized twice to the same canonical SHA-256 (`9d259f72d9f76eb8515b0aecbf25b3c0e148f2b427392cf56b012296fe0d4418`). This digest identifies that transient live candidate only and is not an authoritative persisted snapshot ID.

The live run retains unresolved rule gaps for the consolation ladder, dropped-player reentry, multiple-waiver-claim behavior, playoff ties, regular matchup ties, trade review workflow, and waiver-process timezone. These are explicit capability limitations, not canonical ingestion failures.

Follow-up, 18 October 2026: direct owner inspection of the ESPN settings UI established that the observed regular `matchupTieRule=NONE` allows ties and that tied playoff matchups advance the higher seed. New translations therefore retain raw `NONE` while normalizing these meanings to `allow_ties` and `higher_seed`; the corresponding gaps are no longer emitted for that enum. This does not rewrite the original live-run snapshot or its then-current seven-gap report. Unfamiliar tie enums remain unverified.

The targeted canonical/adapter/CLI suite passed 81 tests. The complete credential-free repository suite passed 185 tests. No credentials, raw private responses, or player/manager details were written to the repository.

## Live workflow

```powershell
python -m fantasy_football.espn_adapter `
  --first-simulation-week 8 `
  --expected-team-count 14
```

The first week is an explicit operational decision and must be the first fully unplayed week. The command emits only sanitized aggregate output. `--manual-config` accepts an optional local YAML file; neither that file nor private raw responses should be committed.
