# V2 vs V1 — behavioral comparison and Timmy approval checklist

**Prepared:** 2026-11-16 | **Status:** Open for independent review; no behavioral change approved by this document.  
**Review target:** Draft PR #1, `feature/v2-compact-simulator` → `main`.  
**Companion evidence:** [Detailed model-change audit](V2_MODEL_CHANGE_REVIEW_FOR_TIMMY.md), [Merge handoff](V2_TIMMY_MERGE_HANDOFF.md), [Master specification](MASTER_SPEC.md), [Roadmap](ROADMAP.md), [Standing instructions](../AGENTS.md).

## How to use this checklist

**Classification** (proposed, **not Timmy's decision**):
- **Must match V1:** Required by the approved spec, unless a spec change is proposed and approved.
- **Proposed experimental difference:** Known deviation that may be acceptable only if clearly scoped, labeled, approved and tested; never silently promote to production.
- **Needs investigation:** Insufficient evidence, unresolved league rule, or unclear impact.

**Status** is separate from classification: `Source-confirmed`, `Unverified`, `Needs correction`, `Approved`, `Rejected`, `Tested`. No items below are marked approved or tested by Timmy. For each row, Timmy should record **decision (accept as experimental / require V1 parity / reject / defer), rationale, test evidence, and reviewer/date**. An approved experimental deviation does not itself authorize promotion as V1-equivalent.

## A. Model assumptions and player decisions

| ID | Behavior in ordinary terms | Classification | Current evidence/status | Required review or test |
|---|---|---|---|---|
| M01 | Use actual ESPN weekly projections for projected-optimal legal starters | Must match V1 | Source-confirmed reuse of lineup optimizer; end-to-end unverified | Golden lineup fixtures, flex/bye/IR |
| M02 | Only pick up players when an actual mandatory starting slot is unfillable | Must match V1 | Broadly aligned; exact positive-improvement check unverified | Vacancy/no-vacancy and borderline improvement cases |
| M03 | Choose the best pickup for **remaining-season roster value**, not merely this week's projection | Proposed experimental difference | **Source-confirmed deviation:** V2 ranks additions by current-week projection | Same-need two-player counterexample; V1 V(R) comparison |
| M04 | Choose drops using replacement-adjusted remaining-season value and position scarcity | Proposed experimental difference | **Source-confirmed deviation:** V2 weighted current/future-average sacrifice | Multi-position bench, scarcity and bye fixtures |
| M05 | Consider add/drop combinations together rather than independently | Proposed experimental difference | **Source-confirmed deviation:** V2 chooses cheapest drop then highest-current add | Counterexample where alternative pair wins |
| M06 | Use fixed weights: future factor 0.65, +12 temporary-absence drop protection, +8 vacancy threshold allowance | Proposed experimental difference | Source-confirmed defaults; no documented calibration/approval | Approve/reject each constant; threshold-negative-gain test |
| M07 | Respect league bench slots and legal destination choices | Must match V1 | Fast first-destination heuristic; full legality parity unverified | Multi-slot/bench-full/position-limit cases |
| M08 | Preserve repeat **essential** claims when multiple real lineup holes remain | Must match V1 | V2 repeat claims consistent with master spec; simpler reference window defaults to one | Multiple needs, contested claims, no speculative upgrades |
| M09 | Automatically correct recovered IR occupants without inventing injury evidence | Needs investigation | V2 projection-based correction and heuristic drop, strict mode distinct | Strict evidence, no room, protected drop, return from IR |
| M10 | Generate plausible uncertain player outcomes | Proposed experimental difference | V2 35% normal variation floored at zero vs calibrated V1 distribution target | Zero outcomes, tails, projection bias, calibration |
| M11 | Keep seeded player-week randomness reproducible | Must match V1 for reproducibility; draw identity may differ experimentally | V2 hash-keyed draws; **not** V1's exact draw contract | Repeat runs and same-engine scenario pairing |
| M12 | Do not independently invent surprise injuries in baseline | Must match V1 | Both models' baseline excludes surprise injuries | Confirm baseline scope; no defect inferred |

## B. Transactions and league rules

| ID | Behavior in ordinary terms | Classification | Current evidence/status | Required review or test |
|---|---|---|---|---|
| T01 | Reject any pickup or drop that violates the authorized league rules | Must match V1 | Compact executor bypasses full canonical executor; parity **unverified** | Differential legal/illegal transaction fixtures |
| T02 | Let dropped players become available **only after authorized waiting period** | Must match V1 | **Needs correction/integration:** no authorized release transition in inspected V2 season loop | Drop in one week, release later with verified evidence |
| T03 | Reset waiver priority only if the league setting authorizes it | Must match V1 | **Needs correction:** V2 recalculates weekly without V1 configuration guard | Reset-enabled/disabled/unknown fixtures |
| T04 | Move successful waiver claimants to end and leave failed claims unchanged | Must match V1 | Source indicates dynamic ordering; not end-to-end proven | Multiple claimants and failed claims |
| T05 | Process immediate free agents in reverse **frozen opening priority** | Must match V1 | V2 compact window matches master spec; simpler reference differs | Competing free agents after waiver priority changes |
| T06 | Preserve no duplicate ownership, legal roster size, no same-cycle reentry, undroppable protection | Must match V1 | Compact checks present; complete parity unverified | Adversarial constraints and repeated-window tests |
| T07 | Record canonical provenance/ledger for accepted transactions | Proposed experimental difference | **Source-confirmed deviation:** compact changes ownership without canonical ledger | Decide whether path-only V2 can omit audit ledger |
| T08 | Keep season/matchup acquisition limits and IR restrictions enforceable | Must match V1 | Compact implementation distinct; edge parity unverified | Limits, unknown evidence, multiple periods |

## C. Season, playoffs and output

| ID | Behavior in ordinary terms | Classification | Current evidence/status | Required review or test |
|---|---|---|---|---|
| S01 | Simulate actual schedule and carry real settled standings forward | Must match V1 | Snapshot schedule and historical standings used; live unverified | Real snapshot fixtures and incomplete schedule rejection |
| S02 | Rank teams by configured standings tiebreakers | Must match V1 | V2 uses record, PF, then team ID; league-specific verification required | Exact tie scenarios; ESPN config |
| S03 | Use verified playoff size, byes, matchups and scoring weeks | Must match V1 | Six-team 2026 bracket implemented; default/hardcoding and old eight-team docs | Six/eight-team fixtures and configuration gating |
| S04 | Resolve exact playoff matchup ties using verified rule | Needs investigation | V2 chooses higher seed; evidence for league rule not established | Source-backed ESPN rule or explicit unsupported state |
| S05 | Sum six playoff qualifiers and one champion per 14-team path | Must match V1 for this league | Source-level invariants; targeted tests previously passed | Fresh real-league run and retained-result integrity |
| S06 | Preserve original V1 features and distinguish experimental V2 results | Must match V1 (nonregression) | Separate V2 API; full integration unverified | V1 smoke/regression and UI/API tests |
| S07 | Persist completed forecasts and recover from cancellation/restart | Must match documented app contract | Source architecture present; real service test pending | End-to-end background run, cancel, restart, reload |
| S08 | Preserve speed improvements without changing forecast semantics unintentionally | Needs investigation | Caches and compact state; no full differential benchmark | Same-model reproducibility, profiling, parity |
| S09 | Use current six-team benchmark, not historical eight-team baseline | Must match V1 acceptance discipline | **Needs correction:** pinned reference totals 8,000 appearances for 1,000 paths | Regenerate measured six-team seed-42 reference |
| S10 | Define supported simulation size consistently | Needs investigation | Internal 20k default, background API 10k cap | Agree benchmark-only versus user-facing target |

## Timmy's decision record

| ID(s) | Decision (accept experimental / require parity / reject / defer) | Reason and governing spec amendment if needed | Test or evidence link | Reviewer/date |
|---|---|---|---|---|
| M03–M06 | **Pending** | | | |
| M09–M11 | **Pending** | | | |
| T01–T03 | **Pending** | | | |
| T07 | **Pending** | | | |
| S03–S04 | **Pending** | | | |
| S08–S10 | **Pending** | | | |

## Review sequence and acceptance gates

1. **Agree on the product boundary:** Is V2 a separately labeled, opt-in *experimental alternative*, or is parity with V1 required before integration? Record the decision; do not silently change the approved master spec.
2. **Resolve rule correctness first:** T01, T02, T03, T06, T08, S02–S04. A fast simulator must not knowingly violate the configured league.
3. **Explicitly approve or reject model substitutions:** M03–M06, M09–M11, T07. Document the impact on interpretation of championship probabilities.
4. **Run differential fixtures:** Shared inputs through canonical and compact transaction paths, including all negative cases, roster changes and playoff ties. For differing scoring models, compare distributions and decisions rather than expecting identical random outcomes.
5. **Run integration evidence:** Full backend suite; frontend build/tests; pinned real-league seeded forecast; probability mass; persistence, cancellation and restart; V1 nonregression; benchmark refresh.
6. **Independent Timmy sign-off:** Record decisions, evidence, and remaining explicitly accepted limitations before marking PR #1 ready. **No merge approval is implied by this checklist.**

## Short nontechnical summary

**Timmy's original approach:** Follow the verified league rules, fill genuine lineup holes, choose roster moves based on their value across the rest of the season, use an evidence-based scoring model, and retain a detailed record of authorized changes.

**Our V2 approach:** Use the same league data and lineup optimizer, but simulate more efficiently. For pickups, favor the strongest player **this week**; for drops, use a simpler weighted projection formula. Generate scores from a simpler variation rule and process roster changes with faster checks. Some release, waiver-configuration and provenance behavior remains unresolved.

**Decision:** Whether those shortcuts are acceptable in an explicitly experimental simulator, and which must be brought into compliance before anyone relies on its probabilities.
