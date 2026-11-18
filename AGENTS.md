# Standing Development Instructions

[Manual scenarios](docs/MANUAL_SCENARIOS.md) owns explicit hypothetical completion; [Unified action search](docs/UNIFIED_ACTION_SEARCH.md) owns candidate grammar, state grouping and search orchestration. Shared legality/V(R) remains in TRANSACTIONS and numerical inference in MONTE_CARLO.

[DATA](docs/DATA.md) owns retrieval, canonical evidence/validation and immutable storage; [RESEARCH](docs/RESEARCH.md) owns temporal trust, research evaluation and promotion evidence. The M0 verifier checks seven evidence roles against current sections/interfaces and recorded historical identities; it does not authenticate historical bytes offline. Contract 3 checks DATA, METHODOLOGY, VALIDATION and TECHNICAL_REFERENCE; no retired historical Markdown file is an executable dependency.

[Outcome models](docs/PLAYER_OUTCOME_MODEL.md) own generated-score policy and numerical model identity; [Monte Carlo](docs/MONTE_CARLO.md) owns estimators, paired inference and numerical recovery; [Partial Week](docs/PARTIAL_WEEK.md) owns current-week evidence/permission and composition.

[Season mechanics](docs/SEASON_SIMULATOR.md) owns lineup/scoring/standings/postseason; [TRANSACTIONS](docs/TRANSACTIONS.md) owns hypothetical roster evolution, legality and V(R). Canonical records remain source truth.

These rules apply to all future work in this repository. The owning [domain references](README.md#understand-the-system) define approved analytical requirements and modeling contracts; [BACKLOG](docs/BACKLOG.md) owns outstanding work. Follow [CONTRIBUTING](CONTRIBUTING.md) for feature-branch/fork synchronization and reviewed pull requests into the original repository's `main`. Do not push changes directly to authoritative main. No GitHub Issues/Projects/labels/templates or repository-setting changes are needed for the repository-native work list.

## Authority and precedence

1. Independently accepted decisions and requirements remain binding, including applicable dependencies and acceptance gates. Only an explicitly approved successor may supersede them; a code discrepancy does not waive a requirement.
2. Each owning domain reference defines its current technical contract, approved constraints and limitations. Link related owners rather than duplicating or overriding another domain's rules.
3. BACKLOG preserves approved unimplemented requirements, unresolved defects, proposals and their evidence/approval gates. A proposal is not implementation authorization.
4. AGENTS and CONTRIBUTING define engineering, verification and review discipline. A passing test or documentation edit does not approve a model, relax a technical constraint or release V1.
5. README owns product scope/navigation and the architecture overview owns system boundaries. Neither silently authorizes new functionality or overrides a technical owner.
6. Ordinary implementation history and former governing/audit sources live in immutable Git revisions and reviewed PRs. Retiring a report does not discard its transferred requirements, source evidence, independent oracles or release/scientific obligations.

## Scope and sequencing

Every implemented component's owning reference must state its purpose, actual algorithm/formulas or pseudocode, input/output and invalid-input contracts, parameters, source provenance, assumptions, edge cases, limitations, regressions, empirical evidence and last validated implementation/model version. Preserve proposed replacements and approval/evidence gates while research is open. Run a minimal end-to-end smoke path after each relevant change; a runnable vertical slice, reference-equivalence check and truthful evidence precede completion. Ordinary dated test totals and rollout history remain in Git/PRs. Keep related domain contracts consistent without creating a replacement master specification or silently changing an analytical requirement.

User/operator procedures are in [OPERATIONS](docs/OPERATIONS.md); developer setup, verification tiers and documentation maintenance are in [CONTRIBUTING](CONTRIBUTING.md). Enhancements must update the owning domain reference and affected operating procedures; future obligations belong in BACKLOG and ordinary history in Git/PRs.

Product navigation is in [README](README.md); workspace/presentation contracts are in [INTERFACE](docs/INTERFACE.md); this does not replace the authorities above or approve future work.

1. Implement from the specification, not from memory, prior code, or convenience. Cite the owning domain section and applicable BACKLOG gate in each task or pull request.
2. Preserve a clean-sheet architecture. Legacy code is historical reference only and must not shape interfaces or behavior merely because it exists.
3. Work in small, independently testable tasks. Do not combine work packages unless their interfaces and acceptance gates have already been verified; a backlog proposal is not implementation approval.
4. Respect applicable milestone dependencies, approved scope and acceptance gates. Never mark a backlog item or milestone complete until its documented acceptance gate passes with inspectable evidence and review.
5. Do not begin a milestone or work package without the required review or approval.
6. Inspect existing modules before adding new ones. Reuse established configuration, authentication, logging, error handling, and validation; extract shared infrastructure only after multiple tasks demonstrate a real need.
7. Avoid premature frameworks, unnecessary abstractions, and architectural changes unrelated to the assigned work package.

## Correctness rules

1. Fail fast on invalid, missing, ambiguous, or structurally inconsistent inputs. Errors must identify the violated invariant and relevant snapshot, transaction, team, player, configuration, or work step.
2. Do not introduce fallback behavior, guessed rules, substitute data, catch-and-continue paths, implicit corrections, or default league mechanics unless the specification explicitly approves and documents them.
3. Exhaustive search means complete enumeration of the explicitly selected finite universe, constrained only by real legality, structural feasibility, and approved model constraints. Optional pruning must be independently disableable and disclose lost coverage.
4. Partial or resource-capped work must report exact coverage and incomplete convergence. Never label a partial search exhaustive.
5. Monte Carlo results must be reproducible from recorded snapshots, configuration, code version, seed, and path definition. Results must remain equivalent across supported worker counts, batching, checkpoint/resume, and generation order.
6. Maintain a simple reference implementation as the correctness oracle. Optimized implementations must prove equivalence for feasible sequences, final states, and path-indexed outputs before promotion.

## Testing and evidence

Assert or check observed invariants, including an expectation that only one participant needs a capacity drop in a two-party trade; do not hardcode the search so an unexpected second drop or simultaneous vacancy can never be observed. Adversarial tests must be able to expose the counterexample. Explicitly designed manual gap configuration, calibrated pooling and operational recovery are separate documented algorithms, not permission for silent error-masking fallbacks. Honest resource-capped Monte Carlo output is normal only with exact partial coverage and unmet-target disclosure; structural/data violations still fail with precise identifiers and context.

1. Define each task's input contract, output artifact, invalid-input behavior, and acceptance test before implementation.
2. Add focused unit tests plus negative and adversarial tests that attempt to break invariants. Do not make unexpected cases unreachable merely to satisfy a test.
3. Use hand-checkable fixtures for exhaustive enumeration and deterministic season behavior. Use seeded fixtures and cross-worker comparisons for probabilistic behavior.
4. Benchmark before adding complex optimizations. Record correctness equivalence, runtime, memory, environment, and limitations.
5. Keep provisional methods visibly provisional. Do not describe an algorithm, data source, ESPN field, or milestone as verified without source evidence and passing tests.
6. Prefer representative sanitized fixtures. Assert precise failure behavior and messages, not merely that some exception occurred.
7. Mock and synthetic tests establish code behavior only; they never prove a live ESPN field or API behavior exists.

## ESPN evidence and provenance

1. For every investigated ESPN field, record the raw endpoint and field, any `espn-api` transformation, any repository transformation, the verification method, and known limitations.
2. Classify findings exactly as **Verified live**, **Verified by tests**, **Unverified**, or **Unsupported**.
3. Keep missing values distinct from explicit zeros. Detect truncated pagination and incomplete multi-request retrieval; never substitute a default for absent or inconsistent data.
4. Validate identifiers and relationships across responses. Record retrieval context sufficient to identify observations that may have changed between requests.
5. Record library defects and unsupported assumptions explicitly. Do not quietly compensate with new modeling rules.
6. Each ESPN discovery task must report implementation, live evidence, test-only evidence, library/API limitations, uncertainties, tests and results, and any recommended architecture change.
7. Do not introduce or implement a new modeling assumption without explicit approval.
8. Discover league settings dynamically and derive supported behavior from the retrieved configuration. Never use this league's team count, roster slots, playoff field, byes, round lengths, divisions, IR/flex presence, or consolation format as a universal validation template. Separate structurally invalid data from valid-but-unsupported semantics.

## Change discipline

1. Keep the engine independent of presentation and network retrieval. Preserve immutable snapshot and provenance boundaries defined by the specification.
2. Never commit credentials, cookies, tokens, private league data, or sensitive values. Use ignored local configuration and sanitized fixtures.
3. Do not silently change approved modeling requirements. Propose specification changes explicitly, document the rationale, and obtain approval before implementation.
4. Update the authoritative documentation when approved behavior changes. A code change that contradicts the specification is incomplete.
5. Leave the repository runnable and reviewable after each increment; include the smallest relevant verification evidence in the commit or pull request.
