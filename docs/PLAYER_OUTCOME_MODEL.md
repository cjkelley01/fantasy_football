# Player outcome generation and numerical identity

This reference owns accepted outcome generation, public model identity and generated-score missingness policy. [DATA](DATA.md#projection-contract) owns raw classification/provenance; [RESEARCH](RESEARCH.md) owns temporal authenticity, calibration and promotion; [Season](SEASON_SIMULATOR.md) and [Transactions](TRANSACTIONS.md) own lineup and transaction permission. Both supported families are **provisional**, not authenticated pregame-calibrated forecasts or a V1 release. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

- [Interfaces and execution boundary](#current-execution-boundary)
- [Missing, zero and present projections](#missing-zero-and-present-projections)
- [Normal distribution and draw bytes](#normal-distribution-and-draw-bytes)
- [Empirical engine model](#empirical-engine-model)
- [Identity and compatibility](#identity-and-compatibility)
- [Evidence and promotion gates](#evidence-and-promotion-gates)

## Current execution boundary

`PlayerOutcomeModel` exposes `model_id`, an ordered `parameters` tuple, and `generate_scenario(snapshot, master_seed=..., path_index=...)` returning an immutable `DeterministicScoreScenario`. The seed is an explicit integer (not Boolean); path index is a nonnegative integer. Each supported generator sorts by (player_id, season, scoring_week), emits exactly one finite `RealizedPlayerScore` for every canonical in-horizon player-week, rejects duplicate/incomplete coverage and leaves source records unchanged. Include unrostered, bench and reserve players so later lawful acquisitions already have path-defined outcomes. No resampling follows lineup selection or ownership changes.

Both Normal and empirical are engine inputs. The public HTTP outcome DTO accepts only `provisional_normal`; empirical selection and partial companions have no public request fields. [PARTIAL_WEEK](PARTIAL_WEEK.md#monte-carlo-subset) owns the separately gated unstarted partial path. Generation does not authorize a lineup, trade, claim or current-week move. Ordinary projected selection uses canonical `effective_projection > 0` before seeing outcomes. A positive draw for an unavailable player cannot make that player eligible.

The protocol requires exact deterministic zero for canonical MISSING. Custom implementations must honor it; there is no generic plugin-output correction wrapper. Caller-supplied deterministic scores and fixed observed actuals are different input contracts, not generated forecasts.

## Missing, zero and present projections

| Raw projection / canonical class | Effective value / source | Ordinary projected-lineup available | Normal generated outcome | Empirical generated outcome |
|---|---|---|---|---|
| Missing / `MISSING` | 0 / missing_projection_policy; raw remains null | No | Exactly `0.0`; skip dispersion and sampling | Exactly `0.0`; no sampling |
| Explicit ESPN zero / `EXPLICIT_ZERO` | 0 / espn | No | Existing zero-mean distribution, sigma = base_stddev | Existing deterministic `0.0` |
| Positive / `PRESENT` | Unchanged / espn | Yes, subject to legality | Normal formula below | Empirical atoms below |
| Finite negative / `PRESENT` | Unchanged signed value / espn | No | Same signed-mean Normal formula | Same finite-present atom policy |

Missingness is determined by classification, never by an effective value of zero. Canonical imputation, lineup availability and generated outcomes are distinct decisions. Explicit zero can have zero Normal dispersion when base_stddev=0; it still follows the nonmissing draw path. No injury-return, positional-average or replacement forecast is authorized. Missing/explicit-zero counts remain separate in `projection_class_counts()` and batch metadata. Completed partial-week starters may contribute fixed actuals despite zero/negative projections under their separate evidence gate.

The conservative policy accepts real missingness risk. The dated 2026 week-6 M0 sample had 395 missing records: one was rostered, 297 lacked an NFL association, and 54 were NFL-assigned, non-bye and marked ACTIVE. Missing did not semantically equal an ESPN zero or prove absence. These are historical observations, not universal rates. Full original methodology/counterexamples remain in the [accepted dated missing-policy report](https://github.com/tdkelley2001/fantasy_football/blob/4eea42d829e066ad9816e77399f09b9e28163de8/docs/MISSING_PROJECTION_POLICY.md); current raw mapping is defined once in DATA.

M0 evidence-role contract 3 checks DATA's [raw projection contract](DATA.md#projection-contract) and [field/identity mapping](DATA.md#identity-rosters-and-player-weeks), [M2 policy evidence](METHODOLOGY.md#projection-classes-and-complete-outcome-universe) and [T2 identity evidence](TECHNICAL_REFERENCE.md#missing-projection-policy-and-identity-boundary). This reference remains the generation/identity authority and its existing anchors remain available. The retired historical file is not a local executable dependency. The canonical ProjectionClass/ProjectionSource/PlayerWeek.effective_projection interfaces and existing evidence limits remain unchanged. A pinned historical citation is not independent byte authentication; no verifier network/Git dependency is introduced.

## Normal distribution and draw bytes

`ProvisionalNormalModel(base_stddev, projection_scale, version=...)` uses, for every **nonmissing** record:

`mu = float(effective_projection)`

`sigma = base_stddev + projection_scale * abs(mu)`

`score = mu + sigma * Z`

Both dispersion parameters are mandatory, finite nonnegative numeric values, excluding Boolean; at least one must be positive. Version is a nonempty string. Nonfinite generated scores fail rather than being clipped. Signed and zero outcomes are retained, with no flooring, rounding, conditional bias correction, position multiplier, independent injury/availability event, shared game shock, player correlation or horizon adjustment. Dispersion is an explicit illustrative assumption, not ESPN-derived calibration.

For the standard-normal draw, encode this exact ordered tuple with `json.dumps(..., separators=(",", ":"), ensure_ascii=True).encode("utf-8")`:

```python
(
    "fantasy-football-player-week-draw-v1",
    master_seed, path_index, player_id, season, scoring_week,
    version,
    (
        ("base_stddev", float(base_stddev)),
        ("projection_scale", float(projection_scale)),
        ("conditional_independence", True),
    ),
)
```

Let this byte string be key. In counter order:

`n0 = int.from_bytes(SHA256(key + b"\x00").digest(), "big")`

`n1 = int.from_bytes(SHA256(key + b"\x01").digest(), "big")`

`u1 = (n0 + 0.5) / float(1 << 256)`

`u2 = (n1 + 0.5) / float(1 << 256)`

`Z = sqrt(-2.0 * log(u1)) * cos(2.0 * pi * u2)`

These are the implemented floating-point expressions, not an alternative RNG or idealized higher-precision conversion. The half-offset intends an open-interval mapping; floating-point rounding can reach the upper endpoint. No new clamp/retry is implied. Preserve the counter bytes, tuple order, float conversion, JSON representation and Box–Muller operation order. No Python hash(), mutable shared RNG, batch position, team, ownership or worker enters the key. Adding an unrelated player cannot perturb an existing draw.

## Empirical engine model

`fit_projection_empirical_residual_model(dataset, report, selection_reference=...)` constructs `ProjectionEmpiricalResidualModel`. It revalidates dataset/report identities and exact linkage, requires exploratory_best_candidate_id=projection_empirical_residual with exactly the empirical/projection-conditioned candidate, and rejects another family rather than substituting one. This current constructor uses that explicit selection field; a research recommendation alone is not an automatic production promotion.

Select only rows strictly before the configured (evaluation_start_season, evaluation_start_week), with raw projection and actual, no policy imputation, and the report's evidence policy. Authenticated-only selects row-bound authenticated evidence; exploratory policy can also include historically returned unverified pairs. Require minimum training rows. Existing historical training remains `unverified_exploratory`. RESEARCH owns authentication and the outstanding scientific validation gates.

The fitted ordered public parameters preserve dataset_sha256, research_report_sha256, evidence_status, evaluation_start_season, evaluation_start_week, projection_bin_edges_json, minimum_group_size, sparse_group_policy=pooled_empirical, training_row_count, training_evidence_sha256, fitted_atoms_sha256, selection_reference, conditional_bias_correction=False, missing_projection_outcome, explicit_zero_projection_outcome and draw_contract. Immutable projection_bin_edges, pooled_residual_atoms and grouped_residual_atoms retain the actual fitted support; version supplies the public model ID. Constructor validation preserves finite sorted unique edges, positive group minimum, nonempty evidence/version/selection, valid hashes, finite residuals/Boolean zero flags, unique sorted group keys and exact partition of pooled atoms by groups.

Bins use lower-inclusive/upper-exclusive edges (value < edge selects the preceding bin). A group meeting minimum_group_size uses its atoms; sparse/absent groups use the recorded pooled empirical atoms. This within-family policy differs from the research framework's separate hybrid partial pooling and is not a missing-projection fallback.

Each training actual zero is flagged as an exact zero atom. Otherwise the target outcome is target projection + (historical actual − historical raw projection). Sort the resulting target outcomes, then select index `min(int(u * atom_count), atom_count - 1)` without interpolation. Signed outcomes survive. Missing and explicit-zero current classes bypass sampling.

The quantile key is the compact ASCII JSON tuple:

```python
(
    "fantasy-football-empirical-quantile-v1",
    master_seed, path_index, player_id, season, scoring_week,
    "outcome_quantile", model_id, parameters,
)
```

Use the big-endian SHA-256 integer and the same half-offset/2^256 floating conversion. Complete empirical identity participates in these draws; unlike Normal's preserved private identity, changing fitted evidence/parameters intentionally changes empirical keys. Collection/transaction/worker order is excluded.

## Identity and compatibility

WR-022 is independently accepted at `28bf605` ([PR #117](https://github.com/tdkelley2001/fantasy_football/pull/117)). It separates identity layers:

| Identity | Exact scope / compatibility |
|---|---|
| Normal historical draw identity | Original version and the three ordered private `_draw_parameters` above; nonmissing key bytes and outcomes unchanged, including custom versions |
| Public Normal policy identity | `model_id = version + ":missing-projection-zero-v1"`; public `parameters` appends `("missing_projection_outcome", "deterministic-zero-v1")` |
| Scenario identifier | `mc:<public model_id>:seed=<seed>:path=<path_index>`; not a complete configuration fingerprint (different parameters can share a model_id/scenario label) |
| Score-content digest | SHA-256 of ordered (player_id, season, scoring_week, points) records; excludes scenario label/public model identity |
| Simulation configuration fingerprint | Snapshot content/identity plus complete public model parameters and policies; [MONTE_CARLO](MONTE_CARLO.md#configuration-run-and-path-identity) owns payload |
| Run identity / path range | Configuration plus seed; range/count are separate exact-coverage inputs |
| Structural action/completed-state identity | Transaction structure/state and scope; not interchangeable with its model-specific numerical evaluation identity |

A historical Normal custom version could equal the corrected model_id. The suffix alone cannot prevent collision. Historical Normal public parameters always had exactly three entries; the explicit fourth policy pair distinguishes the corrected configuration for every supported historical version. Draw hashing must never consume that fourth entry or the suffixed ID.

All pre-correction Normal checkpoints (including earlier PR #117 versions without the discriminator and all-nonmissing inputs) fail current compatibility before new paths/writes. Empirical identities/semantics are unchanged. Missing Normal outcomes change to zero; all-nonmissing scenarios retain their score-content digest, while corrected public configuration/run identities intentionally differ globally. Historical rows, snapshot IDs/bytes/checksums and results are not rewritten. Exact old replay requires prior implementation, not a production legacy-policy switch.

The frozen test-only `LegacyNormalReference` preserves the old algorithm. Two accepted 432a85f goldens remain literal and unchanged: configuration `9d2a4d074a6e3b3562ef30d6136b7d3a8266d6e40f2c4700b1892c2ddd1eb89a` and batch-result `94704af6aa56a9efeab59403d4abaa046878c7e439a14dc02842ebf0d8a9bee1`. Tests compare corrected nonmissing draws against that independent historical oracle rather than replacing goldens. The [accepted compatibility record](https://github.com/tdkelley2001/fantasy_football/blob/06b408f818d3debd52c6a86793f21dfd697f50f8/docs/MISSING_PROJECTION_ZERO_CORRECTION.md) preserves delivery evidence.

## Evidence and promotion gates

Implemented behavior is **Verified by tests**, not new **Verified live** calibration. Normal's common dispersion and empirical's sparse sampled seasons/projection-only bins do not satisfy every approved position/projection/scoring-support/calibration requirement. Authentic pregame/lead-time evidence, signed support and zero/downside calibration, bias/relative-error alternatives, shrinkage, runtime and championship-decision sensitivity remain [RESEARCH](RESEARCH.md#metrics-and-promotion)/[BACKLOG](BACKLOG.md#numerical-and-partial-evidence-obligations) gates.

No family is scientifically preselected by these provisional interfaces. No third-party means, horizon inflation/discount, player-specific volatility, correlation/persistence or separate injury process is approved here. Later bias calibration must preserve raw ESPN decision inputs, apply a validated shrunk correction only once in sampled outcomes, and pass independent review. Common draws across counterfactuals reduce sampling variance; they are not within-path player correlation.

Source: [player_outcomes.py](../src/fantasy_football/player_outcomes.py), [HTTP contracts](../src/fantasy_football/app/contracts.py). Behavioral/numerical evidence: [Normal/universe](../tests/test_player_outcomes.py), [empirical fit](../tests/test_m33_player_outcomes.py), [WR-022](../tests/test_missing_projection_zero.py), [historical oracle](../tests/legacy_normal_reference.py), [partial/goldens](../tests/test_partial_monte_carlo.py). Tests establish code behavior only. No numerical implementation, distribution or identity changes are made by D6.
