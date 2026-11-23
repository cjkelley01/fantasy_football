# CGL War Room: Master Product Plan

> Repository transcription of the November 20, 2026 product outline. The [backlog](BACKLOG.md#cgl-record-book-integration) owns work status and use of this outline. The owning domain references retain technical authority.

> **Forecast decision to reconcile.** The earlier WR-027 decision uses War Room as the only forecast engine for the combined local app. This outline proposes comparison with the public Record Book forecast before consolidation or public replacement (sections 6.4, 11.3 and 18). Phase 0 records that decision explicitly. The initial local foundation assumes War Room forecast ownership and preserves the existing public Record Book.

> **Navigation baseline.** Use the six groups in section 6 for Phase 0 design. The earlier sketch establishes the preferred static desktop sidebar and mobile menu. Preserve War Room's visual style and review which destinations to combine before fixing routes.

Status: Product direction and phased planning baseline

Date: November 20, 2026

Base repository: tdkelley2001/fantasy_football (War Room)

Source project to integrate: cjkelley01/CGL-Record-Book

Initial operating model: Local, trusted CGL application with no user accounts

## 1. Purpose of this document

This document defines the long-range product direction for bringing the CGL Record Book and Fantasy Football War Room together into one coherent fantasy-football manager platform. It is the planning authority for the target experience, migration approach, capability boundaries, phasing, and major decisions that must guide later backlog work.

This is not yet an implementation backlog. It deliberately separates:

- the target product vision;
- features that already exist;
- features that can be assembled from existing data and models;
- features that require new research, data, modeling, infrastructure, or validation;
- approved architectural direction;
- unresolved decisions that must not be guessed during implementation.

After the plan is reviewed and refined, approved phases should be translated into bounded backlog items in the War Room repository. Backlog conversion must preserve dependencies, acceptance gates, source-of-truth boundaries, and explicit non-goals from this document.

## 2. Product vision

Build one CGL-centered application that combines:

- League truth and memory — official results, standings, seasons, drafts, records, managers, rivalries, and league culture.
- Current competitive context — rosters, schedules, matchups, team strength, playoff position, and current league activity.
- Forward-looking decision support — playoff simulation, roster evaluation, acquisitions, trades, scenarios, and eventually lineup and weekly planning tools.
- Transparent analytical evidence — snapshot identity, model version, assumptions, data freshness, uncertainty, simulation coverage, and limitations.
- CGL personality — Game of the Week, commissioner reports, awards, storylines, rivalries, memorable moments, and league-specific editorial content.

The product should feel like a complete fantasy-football headquarters rather than two applications placed beside one another. A manager should be able to answer four questions:

- What matters to me right now?
- What should I consider doing next?
- Where does the league stand?
- How did we get here?

The interface should provide abundant depth without becoming repetitive. Every metric, model, and historical calculation must have one authoritative home. Summary surfaces may link to that home, but they must not create independent, conflicting implementations.

## 3. Product strategy

### 3.1 CGL-first, generic-ready

The initial product is for CGL. It should use the league's established identity, history, rules, managers, rivalries, reporting style, and editorial traditions.

The system should nevertheless avoid needless CGL hardcoding inside reusable analytical components. League size, roster slots, scoring, postseason structure, identities, schedule, and access assumptions should remain configuration or canonical data wherever the War Room already treats them dynamically.

This does not authorize building a multi-league hosted service now. It means the CGL implementation should not carelessly block a future generic product.

### 3.2 Local and trusted first

The initial combined application will run locally in a trusted environment.

- No user accounts.
- No login system.
- No role-based manager permissions.
- No pretense that selecting a manager authenticates that identity.
- Any manager perspective may be selected.
- The selected manager may be remembered locally.
- ESPN credentials remain backend-only and outside version control.
- Administrative and analytical controls may rely on the trusted local environment.

Authentication should be reconsidered only if the application is hosted more broadly, stores genuinely private user-owned work, or requires permissions that cannot be provided safely through a trusted local deployment.

### 3.3 Preserve the public Record Book during migration

The existing CGL Record Book should remain operational and publicly shareable while the combined local application is built. Existing links should not be broken casually.

The migration should prove feature parity and data reconciliation before any decision to retire, redirect, or replace the public Record Book experience.

### 3.4 Evidence before claims

The application must distinguish:

- observed results from forecasts;
- historical facts from derived analytics;
- model estimates from manager behavior;
- supported capabilities from proposed capabilities;
- missing values from explicit zeroes;
- completed exhaustive work from partial or resource-capped work;
- Monte Carlo sampling uncertainty from model error.

No new feature should be presented as reliable merely because a UI can be built for it.

## 4. Foundational decisions

### 4.1 Repository and application foundation

The War Room repository is the base for the combined application.

Rationale:

- It already contains the local backend, frontend, snapshot lifecycle, simulations, persistence, background jobs, run history, validation boundaries, and decision-analysis architecture.
- The Record Book is primarily a static public application and data pipeline. Adding the War Room's infrastructure to it would be more disruptive than integrating historical and editorial capabilities into the War Room.
- The more complex system should absorb the simpler presentation/data layer, not the reverse.

Record Book improvements may continue independently during migration. Useful changes should later be reconciled into the combined plan rather than frozen.

### 4.2 Authoritative responsibilities

| Domain | Initial authority |
| --- | --- |
| Settled historical seasons and official historical records | Record Book pipeline and verified historical dataset |
| Current canonical ESPN snapshot | War Room snapshot system |
| Forward-looking player and season simulation | War Room analytical engine |
| Acquisition, roster-change, and trade evaluation | War Room engine |
| League editorial content and CGL culture | Dedicated CGL content layer |
| Combined user experience | War Room-based frontend after integration |

Observed history must never be overwritten by forecasts. Forecasts must identify their snapshot, completed-through week, source/model, configuration, and run evidence.

### 4.3 Manager perspective

The combined application will initially use a manager selector rather than accounts.

Selecting a manager changes the perspective for:

- dashboard priorities;
- team overview;
- matchup focus;
- waiver and acquisition recommendations;
- trade opportunities;
- schedule planning;
- playoff scenarios and rooting interests;
- alerts and recommendations.

The selector does not establish privacy or ownership. Any manager perspective may be inspected in the trusted local application.

### 4.4 Editorial content

Game of the Week and league culture belong in an optional editorial layer. The reusable structure should support manual authoring for any league; CGL may include richer preconfigured context.

Future LLM assistance may draft previews, recaps, awards, interviews, obituaries, or commissioner reports. Generated content must be grounded in structured league data and saved context, must not invent quotes or relationships, and must require human review before publication.

## 5. Product principles

- One authoritative home per concept. Playoff odds, power rankings, manager profiles, and other major concepts must not have competing implementations without explicit model identity and purpose.
- Progressive disclosure. Ordinary managers see conclusions and actionable context first; advanced settings, coverage, provenance, and run controls remain available without dominating the experience.
- Current decisions and historical storytelling remain distinct. They may connect, but must not be conflated.
- Summaries link to detail. The dashboard may show playoff odds, but the Playoff Race owns the complete probability analysis.
- No decorative analytics. Charts and statistics must help explain comparison, trend, uncertainty, distribution, or a decision.
- No invented certainty. Trade value does not imply acceptance. Waiver value does not imply claim success. Projection means do not automatically constitute trustworthy floors and ceilings.
- League configuration is data. Avoid embedding CGL's rules in generic engine behavior when canonical settings can supply them.
- Local-first privacy. Credentials and private source data remain outside the browser and version control.
- Migration must remain reversible. Existing Record Book publishing and War Room workflows remain usable until their replacements pass acceptance.
- Accessibility and responsive use are first-class. Keyboard use, contrast, semantic alternatives, mobile layouts, and reduced motion remain product requirements.

## 6. Target information architecture

The target interface uses six primary navigation groups. The number of underlying views may be large, but navigation should remain understandable through grouping, tabs, contextual links, and progressive disclosure.

### 6.1 Home

Manager Dashboard

Purpose: show what matters to the selected manager now.

Target content:

- current record, standing, seed, points for, and recent form;
- current matchup and forecast;
- current lineup projection and alerts;
- playoff, bye, finalist, and championship probabilities;
- probability change since the previous valid snapshot;
- top acquisition opportunity;
- top trade opportunity;
- upcoming schedule and bye-week concerns;
- relevant league activity;
- current Game of the Week;
- latest commissioner content;
- data freshness and model/run context;
- direct links to the authoritative detailed views.

The dashboard performs no independent analytics. It composes results owned elsewhere.

Weekly Briefing

Purpose: provide a personalized and league-wide narrative of the previous and upcoming week.

Potential content:

- biggest upset and closest matchup;
- highest and lowest scores;
- standings and probability movement;
- best and worst lineup outcomes;
- impactful transactions;
- pivotal upcoming matchups;
- rooting interests;
- roster, injury, and bye concerns;
- selected-manager priorities.

Initial implementation may use deterministic templates. LLM drafting is later and review-gated.

### 6.2 My Team

Team Overview

- roster, starters, bench, reserve, and IR;
- official record, standing, points for/against, and seed;
- playoff and placement forecast;
- positional strength and replacement-level advantage;
- starter strength, bench depth, and roster balance;
- remaining schedule and future-week risks;
- recent transactions and roster turnover;
- links to the manager's career profile and history.

Use Current and Career contexts rather than mixing current recommendations with historical accomplishments.

Lineup Center

- current legal lineup;
- optimized projected lineup;
- recommended changes and expected gain;
- starter-versus-bench comparisons;
- weekly projections and status;
- bye, lock, injury, and eligibility context;
- safe versus upside choices when supported;
- completed-week lineup efficiency and points left on the bench.

Current-week advice is not authorized until partial-week, lock-state, status, and source requirements are met.

Matchup Center

- selected matchup lineups and projections;
- matchup probability;
- remaining players and expected remaining points;
- swing players and key NFL games;
- paths to victory;
- bench comparison;
- completed matchup recap, expected result, and matchup luck;
- Game of the Week treatment when applicable.

Live probability movement remains a gated future capability.

Schedule Planner

- remaining opponents and strength;
- weekly matchup probability;
- roster-aware future-week outlook;
- bye-week gaps;
- playoff-week schedules;
- streaming needs;
- NFL postseason/rest-risk flags when supported;
- what-if record paths.

### 6.3 Players & Moves

Player Explorer

- searchable rostered and available player universe;
- ownership and eligibility;
- weekly and rest-of-season projections;
- recent scoring and usage where sourced;
- replacement-level value;
- upcoming opponents and bye week;
- injury/status information where supported;
- trend, role, uncertainty, and playoff-week context;
- comparison tray and watchlist when persistence exists.

Rankings, projections, search, and comparison are views of this destination rather than separate pages.

Waiver Wire

- best available players;
- selected-manager roster improvement;
- immediate-week and rest-of-season impact;
- championship/playoff probability change where evaluated;
- position-of-need and streaming views;
- playoff stashes and injury replacements;
- drop candidates and add/drop pairs;
- roster-capacity consequences;
- conditional availability and three strong alternatives;
- clear distinction between acquisition value and claim-success probability.

This absorbs the acquisition-oriented portions of Opportunity Explorer.

Trade Center

Tabs or subviews:

- Trade Finder — complementary teams, candidate exchanges, multi-team chains, all-team impact, coverage, and unresolved ranking evidence.
- Trade Builder — the existing Trade Lab interaction for constructing exchanges, pins, final ownership, and deterministic completion.
- Trade Evaluator — focused evaluation of a proposed exchange, roster construction, weekly consequences, playoff impact, risk, and counterproposal concepts.

The product may evaluate modeled competitive impact. It must not claim to know whether a manager will accept without a separately validated behavioral model.

Streaming Planner

Likely a Waiver Wire tab rather than a primary page:

- QB, TE, D/ST, and kicker options;
- single-week and multiweek combinations;
- bye replacements;
- playoff schedule;
- add/drop cost and fallback choices.

### 6.4 League

League Overview

The factual public center:

- standings, current seeds, records, and points;
- weekly scoreboard and recent results;
- upcoming schedule;
- champion and current leaders;
- short power and playoff-race summaries;
- Game of the Week;
- recent transactions;
- data freshness.

Standings & Power

- official standings;
- power rankings;
- all-play record;
- expected wins;
- luck differential;
- schedule strength and schedule-swap results;
- points, recent-form, roster-strength, and model-based ranks;
- movement over time;
- actual position versus underlying strength.

There should be one official presentation of each calculation. Multiple models must be named and intentionally compared, not silently blended.

League Matchups

- complete weekly scoreboard;
- matchup forecasts;
- league-wide win probabilities;
- completed results;
- leverage, upset potential, closest projection, and largest mismatch;
- Game of the Week;
- weekly matchup archive and head-to-head links.

Playoff Race

The single canonical home for forward-looking league outcomes:

- playoff and bye probability;
- seed and placement distributions;
- expected seed and projected record;
- finalist and championship probability;
- current and most likely brackets;
- alternative bracket paths;
- clinched and eliminated status;
- magic-number logic when implemented;
- bubble and contender tiers;
- remaining schedule strength;
- probability history;
- model, snapshot, completed-through week, assumptions, paths, precision, and limitations.

Subviews:

- Scenario Lab: condition on selected results and compare against baseline.
- Rooting Interests: show which matchup outcomes improve the selected manager's playoff, bye, seed, or title outcomes.
- Probability History: show weekly changes, risers/fallers, turning points, and later forecast accuracy.

The Record Book forecast and War Room baseline must be compared and reconciled before either is silently designated the combined source.

Teams & Managers

Current context:

- current roster, results, form, strength, recent moves, and forecast.

Career context:

- career record, championships, playoff appearances, finishes, scoring history, draft history, transactions, records held, rivalries, head-to-head results, awards, and notable seasons.

League Activity

- adds, drops, and trades;
- transaction timeline;
- roster movement and turnover;
- manager activity;
- impact analysis;
- draft-to-current roster evolution;
- weekly activity recap;
- Moves That Mattered integration.

### 6.5 History

Season Archive

- regular-season and final standings;
- schedule and results;
- playoff bracket;
- champion and runner-up;
- weekly scores;
- draft and transactions;
- awards, records, leaders, upsets, and recaps;
- commissioner report archive.

Records & Leaderboards

- weekly, seasonal, career, matchup, streak, playoff, draft, transaction, luck, and all-play records;
- season, manager, regular-season, and postseason filters.

Record categories should remain consolidated rather than becoming many low-value pages.

Head-to-Head & Rivalries

- manager-versus-manager record and scoring;
- closest games and blowouts;
- playoff/championship meetings;
- streaks and recent meetings;
- rivalry story and full matchup history.

Draft History

- season draft boards;
- player and manager filters;
- position runs;
- best/worst picks when methodology exists;
- draft retention and roster origin;
- championship-roster origins;
- manager drafting tendencies;
- future draft return/value methodology.

Moves That Mattered

Retrospective only:

- best additions, worst drops, valuable trades, and key free agents;
- starter points produced after acquisition;
- points lost after a drop;
- championship-impacting moves;
- season and manager transaction profiles.

This must remain distinct from forward-looking waiver advice.

League Honors & Culture

- champions gallery and trophy history;
- league awards;
- Game of the Week archive;
- commissioner reports;
- midseason and playoff previews;
- team-name history;
- rivalries and memorable moments;
- League Legend content;
- eliminations/obituaries;
- approved photos and graphics;
- league rules and format.

### 6.6 Tools

Simulation Center

Retain as an advanced workspace:

- baseline and action-search setup;
- quick/balanced/detailed presets;
- custom statistical and resource controls;
- run monitoring, cancellation, and history;
- completed/incomplete paths and capabilities;
- precision, errors, and limitations;
- reopen retained results;
- snapshot and model identity.

Most managers should consume results through the relevant product view rather than needing to understand simulation execution.

Scenario Library

Future persistence layer:

- saved playoff scenarios;
- saved transaction comparisons and trade proposals;
- saved player comparisons;
- named runs;
- baseline comparisons;
- share links and staleness warnings.

Data & Model Center

- active league, season, and snapshot;
- refresh time and data coverage;
- missing projections and validation status;
- model, code, configuration, paths, and assumptions;
- ESPN connection/refresh controls;
- historical snapshots;
- known limitations and diagnostics.

Settings

- selected/default manager;
- theme;
- default season/model view;
- display and accessibility preferences;
- future notifications;
- storage/privacy information.

## 7. Cross-cutting capabilities

These should be shared components or contextual functions rather than independent destinations:

- global search;
- global season/week/manager context where appropriate;
- shareable filtered URLs;
- player, team, and scenario comparison trays;
- export image, table, CSV, or commissioner-ready summary;
- freshness, provenance, and model badges;
- accessible explanations and semantic table alternatives;
- “Why this recommendation?” evidence;
- saved filters and recent views after persistence is approved;
- mobile-friendly controls;
- glossary and metric definitions;
- error/data-quality reporting;
- weekly email or commissioner-report export.

## 8. Existing capability inventory and disposition

### 8.1 Record Book capabilities

| Existing capability | Target disposition |
| --- | --- |
| Overview/current season | Merge into League Overview and Home summaries |
| Standings | League Overview; deeper analysis in Standings & Power |
| Season archive | Preserve under History |
| Playoff bracket | Playoff Race for current; Season Archive for completed seasons |
| Manager profile | Teams & Managers with Current/Career contexts |
| Head-to-head | Preserve and expand under History |
| Draft history | Preserve and expand under History |
| Records leaderboards | Preserve under Records & Leaderboards |
| All-play/expected wins/luck | Standings & Power |
| Schedule swap | Standings & Power |
| Power rankings | Standings & Power; one canonical presentation |
| Moves That Mattered | History and retrospective League Activity |
| Playoff forecast | Reconcile with War Room, then merge into Playoff Race |
| Static public publishing | Preserve during migration |

### 8.2 War Room capabilities

| Existing capability | Target disposition |
| --- | --- |
| Canonical snapshot refresh/selection | Shared application foundation and Data & Model Center |
| League Overview baseline results | Merge into Playoff Race and team-focused views |
| Championship/playoff/placement estimates | Playoff Race |
| Opportunity Explorer | Split into Waiver Wire, Trade Finder, and detailed state comparison |
| Trade Lab | Trade Center/Trade Builder |
| Simulation Center | Retain as advanced Tools workspace |
| Background run monitoring/history | Simulation Center and contextual status |
| Lineup optimizer engine | Future Lineup Center after current-week gates |
| Acquisition candidates | Waiver Wire |
| Trade/chain search | Trade Finder |
| Roster-value and deterministic completion | Team, waiver, and trade analysis |
| Partial-week engine interfaces | Research foundation; not yet a user-facing live tool |
| Source/build/snapshot context | Data & Model Center and compact badges |
| League Insights placeholder | Remove or replace only when a defined product purpose exists |

## 9. Capability feasibility classification

### 9.1 Reuse or consolidate existing functionality

These have meaningful existing implementation and should primarily require integration, reconciliation, presentation, or migration:

- official standings and results;
- season archives and playoff brackets;
- manager profiles and head-to-head history;
- draft history and record leaderboards;
- all-play, expected wins, schedule luck, schedule swap, and power rankings;
- Moves That Mattered;
- playoff, bye, finalist, championship, and placement estimates;
- snapshot refresh and selection;
- baseline simulations and run history;
- acquisition and trade candidate exploration;
- Trade Lab construction and evaluation;
- model/provenance presentation.

### 9.2 Build with existing data or model outputs

These likely require new product logic or modest calculations but not a new foundational forecasting model:

- manager dashboard;
- current team overview;
- roster strength and bench depth summaries;
- remaining schedule planner;
- future matchup probabilities exposed by week;
- probability history from retained snapshots;
- rooting interests and matchup leverage through conditioned simulations;
- playoff Scenario Lab;
- deterministic clinching/elimination and magic-number analysis;
- league activity timeline;
- draft retention and roster origin;
- manager tendencies and transaction profiles;
- player comparison and rest-of-season aggregation;
- personalized acquisition recommendations;
- add/drop pairing and fallback targets;
- playoff-week player value;
- deterministic weekly briefings.

Each still requires its own input contract, methodology, validation, and acceptance evidence.

### 9.3 Requires substantial data, modeling, or validation expansion

- trustworthy current-week lineup advice;
- live matchup probabilities;
- conditional in-game player distributions;
- comprehensive injury/practice/inactive intelligence;
- defensible player floors and ceilings;
- advanced streaming and role analysis;
- NFL playoff-clinching and starter-rest risk;
- trade-acceptance prediction;
- waiver competition/claim-success probability;
- automatic manager behavior modeling;
- LLM-authored league content;
- sophisticated draft return/value grades;
- player/NFL correlation models beyond currently approved assumptions.

These must not be smuggled into an interface as if existing projections already prove them.

### 9.4 Primarily infrastructure expansion

Not part of the initial local product:

- user authentication and accounts;
- hosted credential storage;
- public API/backend hosting;
- hosted simulation workers and job queues;
- multi-tenant data isolation;
- per-user quotas and billing;
- league onboarding and membership;
- account deletion/export;
- hosted monitoring, backup, and support operations.

## 10. Delivery roadmap

### Phase 0 — Planning, inventory, and contracts

Goal: establish the integration baseline before changing either product materially.

Deliverables:

- approved feature inventory and page ownership;
- authoritative data/source matrix;
- Record Book-to-War Room identity mapping for leagues, seasons, managers, teams, players, matchups, drafts, and transactions;
- current feature-parity matrix;
- forecast comparison plan;
- public versus local/private data classification;
- migration compatibility requirements;
- documented non-goals and open decisions;
- backlog conversion conventions.

Acceptance gate:

- both repositories' current features, data products, workflows, and known limitations are accounted for;
- no planned destination depends on an unidentified source of truth;
- the migration can proceed without breaking the public Record Book.

### Phase 1 — Integration foundation

Goal: make historical Record Book data and War Room current analysis coexist safely in the War Room-based application.

Deliverables:

- shared navigation shell and route structure;
- manager selector with local persistence;
- shared league/season/team/manager/player identity contracts;
- historical dataset ingestion or adapter boundary;
- source-type labels: observed, derived historical, forecast, scenario, editorial;
- global data freshness and provenance pattern;
- initial combined League Overview vertical slice;
- compatibility plan for existing Record Book links;
- no regression to existing War Room snapshot/run behavior.

Acceptance gate:

- a selected CGL manager and season resolve consistently across current and historical data;
- current observed data and historical records are shown without mixing them with simulation results;
- the local application can render at least one historical Record Book view and one retained War Room analysis from their proper authorities;
- existing applications remain runnable.

### Phase 2 — Existing-feature parity and consolidation

Goal: move or expose all valuable existing functionality in its agreed destination before inventing major new models.

Workstreams:

- League Overview and current season.
- Standings & Power.
- Playoff Race baseline and brackets.
- Teams & Managers.
- Season Archive, Records, Head-to-Head, Draft History, and Moves That Mattered.
- Waiver/Trade restructuring of Opportunity Explorer and Trade Lab.
- Simulation Center and Data & Model Center consolidation.

Acceptance gate:

- every retained Record Book and War Room feature has an implemented destination or an explicit, approved exclusion;
- duplicate calculations are removed or intentionally labeled as model comparisons;
- Playoff Race identifies one baseline source and explains alternatives;
- feature parity is demonstrated against representative current and historical cases.

### Phase 3 — Personalized experience using existing evidence

Goal: turn the combined data into a coherent manager workflow.

Deliverables:

- Manager Dashboard;
- Team Overview;
- Schedule Planner;
- player search/comparison foundation;
- personalized acquisition and trade entry points;
- probability history;
- scenario controls and rooting interests;
- league activity timeline;
- deterministic Weekly Briefing;
- contextual navigation among summaries and authoritative detail pages.

Acceptance gate:

- changing the manager selector updates every personalized view consistently;
- recommendations show their input snapshot, assumptions, coverage, and limitations;
- dashboard summaries reconcile exactly with their authoritative detail views;
- no private-account assumptions have been introduced.

### Phase 4 — Model and data expansion

Goal: add genuinely new decision capabilities only after their evidence requirements are met.

Candidate workstreams, each separately gated:

- current-week Lineup Center;
- partial-week Matchup Center and live probability updates;
- injury/status source integration;
- advanced streaming planner;
- improved player outcome distributions;
- NFL playoff/rest-risk layer;
- stronger schedule and role analytics;
- draft-value methodology;
- behavioral models only if sufficient evidence exists.

Acceptance gate for each workstream:

- source permission and provenance documented;
- methodology approved;
- invalid and unsupported states fail clearly;
- backtesting/calibration completed where applicable;
- uncertainty and limitations are presented accurately;
- no claim exceeds the evidence.

### Phase 5 — Editorial and league culture

Goal: make the application distinctly CGL without contaminating reusable analytical logic.

Deliverables:

- Game of the Week current/archive views;
- commissioner report archive and authoring/export workflow;
- rivalry and relationship context;
- awards, honors, champion gallery, and trophy history;
- memorable moments, team-name history, and League Legend content;
- deterministic story prompts/templates;
- optional LLM-assisted draft workflow after structured grounding and review controls exist.

Acceptance gate:

- editorial content is separated from factual and analytical data;
- unpublished future GOTW plans remain private to the trusted local workflow;
- generated drafts cannot publish automatically;
- stories link to verifiable league facts and do not invent quotes or relationships.

### Phase 6 — Hosting and generic product investigation

Goal: determine whether the successful CGL application should become a hosted or multi-league product.

This is an investigation, not an assumed commitment.

Questions:

- Which views should remain public?
- Which tools require protected access?
- Can ESPN access be handled securely and reliably for external users?
- What compute, storage, retention, and support costs are realistic?
- Are accounts needed for a concrete feature?
- How are multiple leagues and team memberships represented?
- What operational and legal obligations arise?

No hosted multi-tenant implementation should begin without an approved security, privacy, operations, and cost plan.

## 11. Data architecture and reconciliation requirements

### 11.1 Identity

The combined system needs stable mappings for:

- league;
- season;
- manager/person;
- fantasy team by season;
- shared ownership;
- ESPN team and player identifiers;
- NFL team and schedule identity;
- matchup and matchup period;
- draft pick;
- transaction;
- snapshot, forecast, scenario, and simulation run.

Names are display values, not primary identity. Manager identity must survive team-name changes and season changes. Shared teams require explicit ownership representation.

### 11.2 Data classes

Every displayed value should belong to a class:

- Official observed: ESPN results/settings or verified commissioner record.
- Derived historical: deterministic calculation from settled history.
- Current snapshot fact: roster, ownership, schedule, standings, or rules at a recorded snapshot.
- Projection/forecast: future estimate tied to a model and snapshot.
- Scenario: conditioned or hypothetical result.
- Editorial: authored narrative, label, award, relationship, or league context.

The UI and APIs should preserve these distinctions.

### 11.3 Forecast reconciliation

The Record Book currently publishes a historical/projection/blended forecast designed for static publication. The War Room performs retained simulations from canonical snapshots with broader placement and decision-analysis machinery.

Before consolidation:

- compare inputs, rules, roster assumptions, outcome distributions, playoff mechanics, seeds, simulation counts, and snapshot dates;
- compare displayed outputs for the same league state;
- identify whether one model becomes canonical or whether a named model comparison remains useful;
- preserve public forecast continuity until the replacement is validated;
- never hide differences by averaging incompatible results.

### 11.4 Refresh ownership

The long-term plan should converge toward one refresh creating or publishing the canonical current snapshot used by the combined local application. Historical rebuilds, public-safe exports, projections, and forecasts may then derive from that snapshot through explicit pipelines.

Until convergence is proven, both current refresh processes should remain documented and independently recoverable.

## 12. User journeys

### 12.1 Weekly manager workflow

- Open the application.
- Confirm or change the selected manager.
- Review Dashboard alerts and current matchup.
- Inspect lineup, schedule, and playoff implications.
- Explore waiver and trade opportunities.
- Run or inspect scenarios when the decision warrants deeper simulation.
- Return to league context, Game of the Week, or weekly briefing.

### 12.2 Commissioner workflow

- Refresh and validate the current snapshot.
- Review data coverage and held/failed analytical work.
- Inspect league-wide results and forecast movement.
- Select or confirm Game of the Week.
- Draft and review commissioner content.
- Publish only public-safe historical/editorial outputs.

### 12.3 Research/technical workflow

- Select canonical snapshot and model configuration.
- Launch bounded work in Simulation Center.
- Monitor coverage, precision, failure, cancellation, and retained evidence.
- Reopen results in the product view that owns their interpretation.
- Preserve model/snapshot/configuration identity for reproducibility.

### 12.4 Historical exploration

- Select season, manager, opponent, draft, or record category.
- Inspect official results and derived historical metrics.
- Move between manager career, head-to-head, season, draft, and transaction contexts without losing identity.

## 13. Explicit exclusions and deferred ideas

The following are excluded from the initial combined scope or require separate approval:

- accounts and authentication;
- direct ESPN transaction submission;
- a generic NFL news feed;
- a fantasy social network or chat system;
- betting lines, player props, or gambling features;
- claims that a manager will accept a trade;
- waiver-success probabilities without a validated behavioral model;
- separate pages for every metric;
- duplicate Record Book and War Room playoff odds without named purposes;
- multiple unlabeled power rankings;
- a vague League Insights destination without defined decisions or evidence;
- dynasty/keeper tooling unless the league format changes or generic product work is approved;
- full mock-draft infrastructure during the initial regular-season product work;
- autonomous AI recommendations or editorial publication without evidence/review;
- live in-game recommendations before partial-week modeling is accepted;
- automatic injury forecasts;
- unlicensed NFL logos, player imagery, or other protected assets.

## 14. Quality and acceptance standards

Every backlog item derived from this plan should identify:

- user problem and intended decision;
- authoritative input source;
- output and UI destination;
- calculation or methodology owner;
- invalid, missing, ambiguous, and unsupported behavior;
- provenance and freshness requirements;
- assumptions and limitations;
- unit, negative, adversarial, and integration tests;
- representative fixture or snapshot;
- migration/backward-compatibility impact;
- performance expectations;
- accessibility and responsive behavior;
- documentation that must be updated;
- explicit acceptance evidence.

Features must not be marked complete merely because a component renders or a synthetic test passes.

## 15. Backlog conversion strategy

When this plan is approved, convert it into backlog work in dependency order rather than creating one issue per bullet above.

Recommended hierarchy:

- Program/roadmap entry: CGL Record Book integration and combined manager platform.
- Milestones or workstreams: phases from this document.
- Epics: identity/data reconciliation, navigation, League Overview, Playoff Race, history, transactions, personalization, editorial, etc.
- Implementation tasks: small, testable increments with explicit input/output and acceptance gates.
- Research tasks: data/model questions that must be answered before implementation authorization.

Backlog entries should distinguish:

- approved implementation;
- design proposal;
- research/evidence gathering;
- technical debt;
- deferred idea;
- blocked dependency.

Do not copy the entire target feature inventory into the active backlog as if every feature is immediately authorized.

## 16. Open decisions to resolve during planning and implementation

These do not block creation of the roadmap but must be resolved before affected work begins:

- Exact historical data adapter/import approach.
- Canonical manager and shared-team identity representation.
- Whether historical data remains a generated JSON artifact, moves into the War Room database, or uses a layered approach.
- Canonical current playoff model after comparison.
- Public-safe export path from the combined data model.
- Long-term ownership of current-season refresh.
- Routing and URL compatibility for existing Record Book links.
- Persistence design for scenarios, watchlists, comparisons, and editorial drafts.
- Whether private/local tools and public pages ultimately share one frontend build.
- Sources and permissions for injuries, news, weather, usage, and NFL postseason context.
- Minimum evidence for player-level uncertainty and current-week recommendations.
- Commissioner editorial storage and publication workflow.
- Whether any future hosted access creates a real need for accounts.

## 17. Measures of success

Integration success

- One application can traverse current analysis and league history without identity confusion.
- Record Book and War Room features have clear, nonduplicative destinations.
- Existing public and local workflows remain available until replacements pass acceptance.

Manager value

- A selected manager can understand current position, upcoming matchup, playoff path, roster opportunities, and relevant history without assembling information manually across multiple tools.
- Recommendations are actionable, explainable, and honest about uncertainty.

Commissioner value

- Refresh, league analysis, Game of the Week, and commissioner reporting require less manual assembly.
- Public content remains shareable and historically trustworthy.

Analytical quality

- Every forecast and scenario is reproducible from retained identity and configuration.
- Partial work and unsupported capabilities remain visible.
- No displayed claim exceeds its data or validation.

Product quality

- Abundant information does not create redundant navigation.
- Mobile, keyboard, theme, and accessible alternatives remain supported.
- Advanced controls are available without obstructing routine use.

## 18. Immediate next actions

- Review and amend this master plan.
- Create a detailed feature-parity matrix from both repositories.
- Define the shared identity and data-authority contract.
- Compare the two current forecast approaches on the same CGL snapshot.
- Design the combined navigation and first League Overview vertical slice.
- Translate Phase 0 and Phase 1 only into actionable War Room backlog entries.
- Preserve later phases as roadmap direction until their dependencies and approvals are satisfied.

## 19. Current planning summary

The project is not a simple website merge. It is a staged transformation of the War Room into the primary CGL manager platform while preserving the Record Book as the trusted historical/public experience during migration.

The first objective is integration and clarity, not maximum feature count. Once sources, identities, and existing features are reconciled, the product can add personalization using current evidence. Only after that foundation is stable should it expand into current-week decision support, new data sources, richer modeling, editorial automation, or hosted multi-league operation.

This sequencing protects the parts that already work, prevents duplicate analytical truth, and provides a realistic path from two valuable projects to one complete fantasy-football headquarters.
