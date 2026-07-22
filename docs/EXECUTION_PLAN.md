# XASP Execution Plan

Updated: 2026-07-23

This plan converts the scientific contract and refactor audit into ordered implementation phases. Each phase has explicit deliverables, dependencies, tests, evidence, and a fail-closed acceptance gate.

## Program rule

No phase is considered complete because code compiles or a dashboard appears. Completion requires:

- implementation;
- automated tests;
- point-in-time and leakage review;
- reproducible evidence;
- explicit failure behavior;
- updated documentation;
- a recorded `PASS`, `CONDITIONAL PASS`, or `FAIL` result.

The official user-facing action remains `WAIT` until all relevant model gates pass.

---

## Phase 0 — Stabilize the current refactor

### Objective

Prove that the current OHLC, feature-registry, order-book-safety, and purge/embargo changes work together from a clean checkout.

### Deliverables

- clean-environment installation;
- full unit/integration test run;
- static import/compile checks;
- CI workflow evidence;
- migration test for legacy local data;
- failure report for every unresolved test.

### Acceptance gate P0

- all critical tests pass;
- no feature or target leakage finding remains open;
- forming candles cannot enter the dataset;
- OHLC first-touch and gap exclusion tests pass;
- far-away order-book walls cannot influence primary pressure;
- unsuccessful verification keeps the service at `WAIT`.

Status: `IN PROGRESS`.

---

## Phase 1 — Restart-safe historical bootstrap and visible lifecycle

### Objective

Make the first 365-day collection resumable, observable, and safe against interruption.

### Deliverables

- consume Binance history in bounded chunks;
- persist every checkpoint atomically;
- advance the raw-data watermark after each successful checkpoint;
- resume from the last checkpoint after interruption;
- exclude the currently forming candle;
- expose lifecycle stages and progress through API and UI;
- report expected rows, processed rows, persisted rows, checkpoints, and current watermark;
- distinguish fresh bootstrap from missing-tail synchronization.

### Lifecycle stages

- `BOOTSTRAP_HISTORY`
- `SYNC_MISSING_TAIL`
- `BUILD_ANCHORS`
- `BUILD_FEATURES`
- `BUILD_TARGETS_A`
- `TRAIN_MODEL_B`
- `TRAIN_MODEL_A`
- `PREDICT`
- `MATURE_OUTCOMES`
- `REPORT`
- `LIVE_IDLE`
- `ERROR`

### Acceptance gate P1

- interruption after any checkpoint loses no confirmed data;
- restart continues from the correct watermark;
- memory does not grow with the entire unpersisted year;
- progress is visible before training begins;
- stale/incomplete bootstrap remains `WAIT`.

Status: `STARTED`.

---

## Phase 2 — Canonical point-in-time dataset and feature parity

### Objective

Create one governed feature row per completed minute with explicit source availability and identical offline/live calculations.

### Deliverables

- versioned feature registry with formula, source, lookback, availability, missing policy, scaling, and model eligibility;
- historical price/trade-flow features;
- source-availability masks;
- deterministic joins by availability timestamp;
- unknown numeric columns excluded by default;
- offline/live parity tests;
- feature-distribution and missingness reports;
- immutable dataset ID and schema hash.

### Acceptance gate P2

- every selected feature is registered;
- every feature is available no later than its anchor;
- offline and live values match within tolerance;
- transformations learned from data are fitted on training partitions only.

Status: `PARTIALLY IMPLEMENTED`.

---

## Phase 3 — Complete Model B target and evaluation pipeline

### Objective

Make ±10% first-touch targets, training, calibration, prediction, and maturation methodologically identical.

### Deliverables

- OHLC-aware and gap-safe targets;
- `AMBIGUOUS` handling for same-candle dual touch;
- independent event clustering;
- purged walk-forward train/calibration/test folds;
- unconditional, logistic, and boosted-tree baselines;
- PR-AUC, precision, recall, Brier, ECE, false-alert, and support metrics;
- independent prediction ledger and production calibration report.

### Acceptance gate P3

- no close-only shortcut remains;
- insufficient rare-event support forces `WAIT`;
- performance beats unconditional and always-`NO_EVENT` controls on multiple untouched periods;
- probabilities remain calibrated with sufficient independent support.

Status: `CORE CORRECTNESS IMPLEMENTED; WALK-FORWARD AND EVENT CLUSTERS PENDING`.

---

## Phase 4 — Complete Model A target and production pipeline

### Objective

Promote Model A from a quantile baseline into an independently monitored adaptive-shock research model.

### Deliverables

- future high/low excursion targets from observed OHLC;
- time-to-high and time-to-low targets;
- purged walk-forward quantile baselines;
- pinball, MAE, interval coverage, interval width, and quantile-crossing metrics;
- independent immutable Model A prediction ledger;
- delayed maturation of high/low outcomes;
- production coverage/error report by horizon and regime.

### Acceptance gate P4

- Model A can be evaluated without using Model B labels or reports;
- interval coverage and error thresholds pass on untouched periods;
- production performance remains within predefined tolerance;
- failure of Model A does not disable a valid Model B, and vice versa.

Status: `BASELINE IMPLEMENTED; INDEPENDENT PRODUCTION MATURATION PENDING`.

---

## Phase 5 — Live near-price order-book microstructure

### Objective

Collect and use real executable liquidity near the current price without allowing distant walls to distort the model.

### Deliverables

- restart-safe snapshot/delta collector;
- sequence-gap and stale-message detection;
- best bid/ask, spread, microprice, and best-level imbalance;
- primary depth bands at 5/10/25/50/100/200 bps;
- 500/1000-bps context and 2000-bps diagnostics only;
- OFI, depletion, replenishment, cancellation, and persistence;
- feature-availability masks for periods before live collection;
- spoof/far-wall red-team tests.

### Acceptance gate P5

- a huge order outside primary bands cannot change primary pressure;
- sequence breaks force the order-book feature family unavailable or `WAIT`;
- no historical order book is synthesized;
- one snapshot cannot be treated as persistent support or resistance.

Status: `FEATURE CORE STARTED; COLLECTION PENDING`.

---

## Phase 6 — Cross-market and derivatives enrichment

### Objective

Add real point-in-time BTC/ETH and XRP perpetual context.

### Deliverables

- BTC/ETH minute price, returns, volatility, lead-lag, beta, and correlation state;
- XRP perpetual trades and book context;
- funding, OI level/change, basis, mark/index premium, taker flow, and liquidations;
- deterministic as-of joins and availability masks;
- ablation reports comparing each feature family.

### Acceptance gate P6

- every external feature has source and availability evidence;
- missing sources do not become zero-valued fake signals;
- ablation proves whether each family adds stable value.

Status: `PENDING`.

---

## Phase 7 — Champion/challenger, drift, quarantine, and rollback

### Objective

Allow continuous learning without blind daily replacement.

### Deliverables

- frozen champions and isolated challengers;
- non-blocking training;
- feature, label, calibration, and performance drift detection;
- purged recent evaluation and promotion rules;
- model quarantine;
- exact rollback to last-known-good model;
- immutable promotion/rejection log.

### Acceptance gate P7

- daily training alone cannot promote a challenger;
- failed challengers never interrupt champion inference;
- rollback restores exact model, schema, and configuration state;
- drift reduces confidence or forces `WAIT`.

Status: `PENDING`.

---

## Phase 8 — Product interface and operational observability

### Objective

Make the platform state understandable without implying certainty.

### Deliverables

- visible bootstrap/training progress;
- independent Model A and Model B state cards;
- explicit human-readable `WAIT` reasons;
- data coverage, freshness, source health, and missing families;
- training range, sample counts, model version, and metrics;
- independent prediction histories and matured outcomes;
- long-session, restart, stale-source, and one-model-only browser tests.

### Acceptance gate P8

- every visible value reconciles with a persisted record;
- UI never displays a model as ready from file presence alone;
- stale or failed sources visibly degrade the correct model to `WAIT`.

Status: `PARTIALLY IMPLEMENTED`.

---

## Phase 9 — Economic simulation and paper trading

### Objective

Determine whether statistical quality survives realistic execution.

### Deliverables

- fees, spread, slippage, latency, partial/missed fills, and liquidity caps;
- fixed-size and risk-capped policies;
- expected value, drawdown, tail loss, profit factor, and false-alert cost;
- frozen-champion paper trading;
- comparison of live paper results with backtest expectations.

### Acceptance gate P9

- positive results survive conservative costs across multiple periods;
- no fill uses future or unavailable liquidity;
- sufficient independent event clusters are observed;
- unresolved operational incidents block advancement.

Status: `PENDING`.

---

## Phase 10 — Independent final review

### Objective

Reproduce the full system from manifests and issue a formal verdict.

### Deliverables

- clean-room rebuild;
- leakage red team;
- model-risk review;
- operational and execution-failure review;
- secret/licensing/security review;
- limitations and non-guarantee statement;
- final `PASS`, `CONDITIONAL PASS`, or `FAIL` report.

### Acceptance gate P10

No production or advisory claim exists without explicit independent approval.

Status: `PENDING`.

---

## Immediate implementation sequence

1. Complete Phase 1 checkpointed bootstrap and lifecycle API.
2. Add lifecycle UI and restart/interruption tests.
3. Run Phase 0 clean verification against the new bootstrap code.
4. Close remaining Model A/Model B independent-ledger and walk-forward gaps.
5. Begin live order-book collector only after the base lifecycle is stable.
