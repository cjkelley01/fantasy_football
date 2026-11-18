# Historical data and outcome research

This is the current historical-dataset, temporal-trust and outcome-research reference. [DATA](DATA.md) owns ESPN parsing/source interpretation/canonical storage; [PLAYER_OUTCOME_MODEL](PLAYER_OUTCOME_MODEL.md) owns actual generation policy; [MONTE_CARLO](MONTE_CARLO.md) owns execution/statistical aggregation. Research recommendations are not production promotion or release acceptance. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

- [Evidence and hypotheses](#evidence-and-hypotheses)
- [Dataset and authenticated captures](#dataset-and-authenticated-captures)
- [Coverage and leakage](#coverage-and-leakage)
- [Temporal evaluation and candidates](#temporal-evaluation-and-candidates)
- [Metrics and promotion](#metrics-and-promotion)
- [Reproduction and artifacts](#reproduction-and-artifacts)
- [Sources and limitations](#sources-and-limitations)

## Evidence and hypotheses

Investigate projection error conditional on raw position and projected-point level, including explicit zero mass, signed outcomes, tails, bias, calibration/sharpness, sparse groups and computational cost. Empirical, parametric and hybrid families compete; synthetic success does not identify a scientifically calibrated model. Lead-time, player-specific effects, correlations and independent injury/availability processes require separately approved evidence/model scope, not assumed improvements.

The 16 October 2026 M0.5 historical audit selected 2024/2025 weeks 1,8,14,17 and returned 8,760 player-weeks, 7,489 raw projections, 5,214 actuals and 5,000 structural pairs. No raw creation/update/as-of/revision timestamp or ordered forecast vintages established when the values were available. Stable same-session requests and bounded kona_playercard agreement establish only current-response coherence. Returned team/position/scoring context may also be retrospective. All pairs remain **Unverified** pregame evidence, not authenticated forecast errors.

M0's aggregate-only observation originally had no retained row artifact. The separately authorized 23 October 2026 workflow later materialized private schema-v2 rows: 2,638 training and 2,362 evaluation pairs, **zero authenticated-pregame pairs**. This corrects old delivery-time statements that materialization was future work without upgrading temporal trust. Current historical field availability was **Verified live** in those retrievals; parsing/research safeguards are **Verified by tests**. Vintage/horizon labels remain **Unsupported** by that source.

The projection-band empirical candidate had the lowest exploratory CRPS (2.0294 versus projection-band Normal 2.0544); recommended_candidate_id stayed null. That is a dated exploratory comparison, not calibrated selection. The live table/method remains in [the accepted source record](https://github.com/tdkelley2001/fantasy_football/blob/7cfb09b4935f3e73bbfdb76e25b1cab102b751c1/docs/OUTCOME_DISTRIBUTION_RESEARCH.md). It does not change the explicitly provisional Normal model or promote the separate provisional empirical engine input. Neither family is authenticated pregame calibration; HTTP exposes only its accepted Normal configuration.

## Dataset and authenticated captures

`build_historical_research_dataset(samples, sources, validated_pregame_captures=())` in [historical_dataset.py](../src/fantasy_football/historical_dataset.py) is pure: no network, snapshot-store writes or model fitting. Inputs select every season/week and observation window explicitly, with immutable raw parser records, distinct projection/actual/scoring source roles, UTC retrieval/period boundaries, canonical request-parameter JSON, raw-payload SHA-256 and a single shared scoring signature. Source IDs/roles/target periods are unique/coherent; actual retrieval cannot precede observation-period end. Finite signed values, unique player-weeks and matching context are enforced. No implicit league/season/position/scoring default exists.

EvidenceClassification (verified_live, verified_by_tests, unverified, unsupported) and source kind are descriptive declarations, not proof. Sanitized fixtures cannot claim verified_live. ProjectionValueKind distinguishes espn_value (finite nonzero), espn_explicit_zero, missing and model_policy_imputed_zero (raw remains null). ProjectionTemporalProvenance independently distinguishes authenticated_pregame, historical_projection_pregame_provenance_unverified and not_applicable. Missing/policy-imputed rows are never genuine projection-error pairs; explicit ESPN zeros and signed values are retained.

### External trust and exact row binding

`validate_pregame_capture_manifest()` is the only supported constructor for `ValidatedPregameCapture`. It requires canonical manifest bytes, exact captured payload bytes and **independently_trusted_manifest_sha256** provisioned outside the source record. Validate canonical manifest/schema/field set, lowercase digests, exact payload hash, source endpoint/view/parameters/ID, target season/week, authority/reference and aware capture-before-period start (strictly earlier). The accepted historical ESPN parser reparses those exact bytes; the validated object carries immutable parsed records and a validation token.

The dataset builder binds the validated capture to identical source metadata and sample boundary. Every authenticated row must match captured player ID, season, week, raw projection, default position and NFL team. An altered value, invented player, wrong period/context or source-ID-only match cannot inherit authentication. Later actuals require their separate post-period source; a pregame payload cannot authenticate them. Captured as-of-like fields, caller timestamps/digests, a verification label or fixture do not substitute for this gate.

Computing a hash of an arbitrary manifest and passing it back is structurally valid but **not independent real-world authentication**. Code cannot prove where the trust anchor came from. Operational authenticated calibration additionally needs an independently controlled registry/review record and retained exact bytes. No such live authorization was added by D5; existing historical pairs do not become authenticated. Unauthenticated captures can remain as explicitly unverified evidence rather than disappearing.

### Output and identity

HistoricalResearchDataset schema **2** contains selected sample identities, sorted sources/rows/capture identities, scoring signature, computed coverage, limitations and dataset_sha256 over the evidence-bearing core. `historical_research_dataset_to_json()` validates identity and emits deterministic UTF-8 JSON bytes. Input ordering does not change output. The validated manifest digest and non-sensitive authority/reference are retained, **not raw payload bytes**. Preserve exact private payloads separately if future validation needs them. Dataset schema, canonical schema, SQLite and model/checkpoint identities remain independent.

## Coverage and leakage

Coverage derives from rows, not caller totals: overall, season, season/week, raw default position and descriptive projection magnitude (missing, policy-imputed zero, explicit zero, finite negative, positive <5, 5..<15, >=15). Report rows, raw projections/actuals/pairs, authenticated pairs, unverified exploratory pairs, missing/zero/imputation counts. Imputation is a disclosed subset of raw-missing, not replacement evidence. These audit bands are not approved model strata or universal thresholds.

Raw source 0 actuals and source 1 projections stay separate; period 0 totals/averages cannot masquerade as weekly values. Reject duplicate/cross-sample/wrong season/week/source/split records, nonfinite values, malformed status/provenance and mismatched scoring signatures. A scoring signature proves currently returned configuration agreement, not unchanged game-date settings. Stat-component or as-of-like metadata presence is a diagnostic, never universal participation/pregame proof.

Missingness and explicit zeros dominate some endpoint groups. Actual absence can mean omission/nonparticipation/another unknown; never infer participation or success from zeros/presence. There is no independent denominator for historical universe completeness. Position/team wrappers, scoring revisions and sharp season/week coverage changes can induce selection bias. Report missing-projection rows with actuals separately; do not fill missing forecasts to manufacture calibration pairs. Matching source hashes and exact values prevent specified tampering but do not remove endpoint revision or selection bias.

## Temporal evaluation and candidates

`research_outcome_distributions(dataset, configuration, candidates)` first verifies dataset identity. Explicit `TemporalSplit(evaluation_start_season,evaluation_start_week)` puts earlier periods in training and boundary-or-later periods in evaluation. No random player-week split. The current framework has one chronological training/evaluation boundary, **not a third sealed final-test set or automated repeated-tuning protection**; those remain protocol obligations for promotion.

`EvidencePolicy.AUTHENTICATED_ONLY` selects manifest/payload/row-bound pairs. INCLUDE_UNVERIFIED_EXPLORATORY permits historical pairs but cannot issue a calibrated recommendation. Only raw projection+actual pairs qualify; policy/missing rows are excluded. Explicit zero, negative projections and zero/negative outcomes remain distinct. Projection-band edges, minimum group/training/evaluation counts, shrinkage strength, density-bandwidth floor and Normal quadrature size are explicit ResearchConfiguration, not league settings or hidden production parameters. Insufficient data (including dataset=None) returns an insufficiency report, never invented scores.

The default nine candidates comprise empirical residual and zero-mass-plus-Normal residual families under pooled, position, projection-band and joint position/band conditioning (eight), plus joint empirical hybrid partial pooling (one). Actual zeros contribute zero mass; nonzero residual support is translated to the target projection. Normal candidates use deterministic quantile quadrature. Signed predicted outcomes are neither floored nor rounded.

Empirical/Normal groups below minimum_group_size use established **pooled-only fallback**. Hybrid differs: empty local group uses pooled-only; every nonempty group retains local evidence with configured shrinkage. Nonempty groups below threshold increment sparse_partially_pooled_rows, not fallback_rows; groups at/above threshold increment neither diagnostic. fallback_rows records actual pooled-only execution. Do not erase sparse evidence or confuse research pooling with a missing-projection production fallback. No player-specific/correlation/injury/lead-time mechanism is added.

## Metrics and promotion

Each candidate reports mean CRPS (primary proper-score ordering), kernel negative log score with explicit bandwidth floor, forecast-mean bias/RMSE, relative-error count/mean only for nonzero projections, zero-outcome Brier score, 50/80/90% coverage/width, lower-10/upper-90% exceedance, ten-bin deterministic mid-PIT and evaluation/fallback/sparse counts. Coverage is separate by temporal split, evidence class, season/week, position and configured band. Research report identity includes dataset/configuration/candidate evaluations/coverage/recommendation and limitations; serialization revalidates it.

Order candidates by held-out mean CRPS, then negative log score, then stable candidate ID. Minimum sample requirements plus authenticated-only policy can produce a **research recommendation pending independent review**; exploratory evidence can only produce exploratory_best_candidate_id. Neither path installs a production model. Retain out-of-sample calibration/sharpness, zeros/support/tails/bias, sparse-data sensitivity, baseline/alternative comparisons and full-scale sampling cost in a reproducible decision record. Separate model/data uncertainty and selection sensitivity from Monte Carlo sampling precision.

The governing research program also requires honest temporal training/validation/sealed-test use, prevention of tuning leakage, representative position/projection-level and scoring coverage, calibrated pooling/fallback sensitivity and championship-decision relevance before promotion. Current one-boundary code or sample sufficiency alone does not satisfy every scientific gate. [BACKLOG](BACKLOG.md#data-research-and-m0-migration-obligations) retains the exact outstanding protocol/evidence obligations. Do not preselect a family, promote on synthetic scores, treat a single CRPS winner as universal calibration, add third-party means or invent horizon variance. [PLAYER_OUTCOME_MODEL](PLAYER_OUTCOME_MODEL.md) owns any separately approved production family/identity transition.

### Component-specific promotion

Retain internally selectable, explicitly versioned experimental policies for uncertainty distributions, roster valuation, waiver behavior, candidate pruning and numerical methods where those interfaces are supported. This design obligation does not mean every experimental selector is implemented or exposed publicly. Choose acceptance criteria for the component's actual job: calibration/zero mass/support/tails for outcomes, sampled decision quality for V(R), source-rule fidelity for mechanics, and exact equivalence plus runtime/memory for numerical optimization. Compare against the incumbent approved version using held-out or prospective evidence when feasible, accounting for selection bias and runtime regression. No single universal score/threshold promotes all components.

Promotion requires a reproducible decision record and independent acceptance after the documented criteria pass. The normal application must default to the latest approved official combination without experimental model-selection clutter; current API exposes the provisional Normal configuration, not a newly calibrated official family or every internal model. Scientific requirements and conditional methods remain binding under [WR-019](BACKLOG.md#wr-019-scientific-promotion-and-deferred-research); implementing a report, obtaining the lowest exploratory CRPS, or transferring documentation cannot promote a model. Raw ESPN decision inputs and existing numerical identity remain unchanged unless a separately approved versioned transition says otherwise.

## Reproduction and artifacts

Pure in-memory path after separately authorized data preparation:

```python
from fantasy_football.historical_dataset import build_historical_research_dataset, historical_research_dataset_to_json
from fantasy_football.outcome_distribution_research import research_outcome_distributions, outcome_distribution_report_to_json

dataset = build_historical_research_dataset(samples, sources)
report = research_outcome_distributions(dataset, configuration)
dataset_bytes = historical_research_dataset_to_json(dataset)
report_bytes = outcome_distribution_report_to_json(report)
```

Sources/samples/configuration must be explicitly constructed and validated; this is not a fabricated sample or runnable empty-data promotion recipe. The caller controls private writes.

The read-only **networked** materialization workflow is separately authorized work, not an offline verification command:

```powershell
.venv\Scripts\python.exe -m fantasy_football.historical_research_workflow --plan docs/examples/m32_historical_research_plan.json --output-dir outputs/m32-evidence-local
```

The plan chooses every season/week/window/configuration explicitly. The workflow reuses paginated historical parsing, captures canonical player/scoring responses, filters, UTC retrievals and scoring signatures, builds dataset then evaluates. Output must be a new/empty child under ignored outputs; it writes historical-research-dataset.json, outcome-distribution-report.json, raw-player-pages-<season>-<week>.json, raw-scoring-<season>.json and retrieval-manifest.json with plan/dataset/report digests and request context. It does not create authenticated captures: selected observation windows are not evidence of pregame timing. A coverage-only historical_projection_audit invocation does not materialize these artifacts. Keep credentials, raw responses and private player/league rows out of Git. No retrieval is authorized merely by this guide or D5.

## Sources and limitations

Source: [historical parser](../src/fantasy_football/historical_projection_audit.py), [dataset/trust boundary](../src/fantasy_football/historical_dataset.py), [evaluation](../src/fantasy_football/outcome_distribution_research.py), [materialization](../src/fantasy_football/historical_research_workflow.py) and [example plan](examples/m32_historical_research_plan.json). Credential-free tests: [dataset](../tests/test_historical_dataset.py), [research](../tests/test_outcome_distribution_research.py), [workflow](../tests/test_historical_research_workflow.py), [historical audit](../tests/test_historical_projection_audit.py). They preserve exact-payload row binding, invalid trust assertions, scoring/temporal contradictions, deterministic identity, perfect-forecast CRPS, signed/zero cases and all sparse-threshold distinctions. Fixtures prove code, not live calibration. Current commands/results belong in the PR and [CONTRIBUTING](../CONTRIBUTING.md), not another milestone report.

Historical pregame vintages, original player context/scoring finality, complete population/participation and independent capture registry remain absent/unverified. Prospective collection is optional separately approved work, not a V1 prerequisite; its cadence/horizon/retention must be decided **before** collection. Documentation consolidation changes no distribution, numerical policy, source classification, storage or production model.

The [M0 role contract](DATA.md#m0-consumer-dependency) uses contract 3 to check DATA, METHODOLOGY, VALIDATION and TECHNICAL_REFERENCE. The historical-projections role binds DATA's research-source trust, VALIDATION's empirical-validity sections, TECHNICAL_REFERENCE's research interfaces and the existing parser/trust-boundary definitions; RESEARCH retains substantive research authority. Its locally checked historical citation identity is not an authenticated pregame capture, inspected historical payload or new live verification.

[PLAYER_OUTCOME_MODEL](PLAYER_OUTCOME_MODEL.md) specifies the accepted provisional generators/identities; [MONTE_CARLO](MONTE_CARLO.md) specifies sampling estimators and their limitations; [PARTIAL_WEEK](PARTIAL_WEEK.md) specifies temporal execution permission. Research promotion gates above remain binding; availability of an engine model or deterministic remainder is not evidence of calibration.
