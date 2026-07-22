# XASP Master Plan

## Mission
Build a scientifically governed research system that estimates whether XRP will touch +10% or -10% first within 15, 30, 45, or 60 minutes, learns only from matured outcomes, and refuses to emit tradable signals until strict evidence gates pass.

## Product contract
At every prediction timestamp the system must return:

- `p_up_10`
- `p_down_10`
- `p_no_event`
- `predicted_time_bucket`
- `uncertainty`
- `data_freshness`
- `model_version`
- `governance_state`

The probabilities must sum to 1 within numerical tolerance. A user-facing trade state remains `WAIT` until all promotion gates pass.

## Workstreams

### WS1 — Data acquisition and integrity
**Objective:** create reproducible, timestamp-correct datasets.

Tasks:
1. Collect XRP and BTC spot trades.
2. Collect top-of-book and, later, multi-level order-book snapshots and deltas.
3. Collect XRP perpetual-market data: funding, open interest, basis, liquidations, and taker flow where available.
4. Normalize timestamps to UTC milliseconds.
5. Detect duplicates, gaps, out-of-order events, stale messages, and sequence breaks.
6. Write immutable raw partitions and manifests with hashes.

**Completion criteria:**
- At least 30 consecutive days of valid spot data for baseline research.
- Data coverage >= 99.5% for required minute buckets.
- Duplicate rate after ingestion < 0.01%.
- Every partition has source, retrieval time, row count, min/max timestamp, and SHA-256 hash.
- Data-quality report generated automatically.

### WS2 — Event labeling
**Objective:** generate immutable first-touch labels.

For each anchor timestamp and horizon, compute whether +10%, -10%, or neither barrier is touched first. Record exact touch time and maximum favorable/adverse excursion.

**Completion criteria:**
- Labels are deterministic and idempotent.
- Unit tests cover upper-first, lower-first, no-event, simultaneous-touch ambiguity, missing-path, and boundary timestamps.
- Labels never use data beyond the selected horizon.
- Overlapping event clusters are identified.

### WS3 — Feature engineering
**Objective:** build causally available features with documented lineage.

Feature families:
- returns and momentum across multiple windows;
- realized volatility and jump intensity;
- trade-flow imbalance and CVD;
- spread, microprice, depth imbalance, depletion, and replenishment;
- BTC/ETH lead-lag and rolling correlation;
- open-interest, funding, basis, and liquidation pressure;
- liquidity and execution-feasibility features;
- session and event-time features.

**Completion criteria:**
- Every feature has formula, source, lookback, availability timestamp, missing-value policy, and leakage classification.
- Scaling is fitted on training data only.
- No feature references future rows.
- Feature parity test passes between training and live paths.

### WS4 — Baseline models
**Objective:** prove whether a measurable edge exists before deep learning.

Models:
1. class-prior baseline;
2. multinomial logistic regression;
3. gradient-boosted trees;
4. calibrated ensemble.

**Completion criteria:**
- Temporal walk-forward evaluation with purge and embargo.
- Untouched final test period.
- Class-wise precision, recall, PR-AUC, Brier score, calibration error, and false-alert rate reported.
- Baseline comparison includes `always NO_EVENT` and high-volatility random-entry controls.

### WS5 — Time-to-event and regime modeling
**Objective:** estimate when a barrier may be touched and adapt weights to market regime.

**Completion criteria:**
- Separate time-to-event metric by 15/30/45/60-minute bucket.
- Regime assignments are reproducible and documented.
- Regime model adds value on validation without degrading untouched test performance.

### WS6 — Continuous learning
**Objective:** safely learn from matured forecasts.

Rules:
- Prediction labels are delayed until horizon expiry.
- Online updates are bounded and reversible.
- Champion and challenger models are isolated.
- Drift detection can reduce confidence or force `WAIT`, but cannot autonomously promote a model.

**Completion criteria:**
- Immutable forecast ledger.
- Rollback to last-known-good checkpoint.
- Drift, calibration, and performance alerts.
- Promotion requires documented review and approval.

### WS7 — Economic simulation
**Objective:** determine whether statistical performance survives realistic execution costs.

Include fees, spread, slippage, latency, partial fills, missed fills, and adverse movement.

**Completion criteria:**
- Results reported net of costs.
- Maximum drawdown, profit factor, expected value, tail loss, and trade frequency reported.
- Sensitivity analysis across conservative cost assumptions.
- No live execution code before explicit founder approval.

### WS8 — Product interface
**Objective:** communicate evidence, uncertainty, and limitations clearly.

**Completion criteria:**
- No unsupported LONG/SHORT label.
- Each forecast shows data freshness, model state, probabilities, sample size, uncertainty, supporting/opposing factors, and invalidation conditions.
- Stale or incomplete data forces `UNCERTAIN` or `WAIT`.

## Phase gates

### Gate A — Data ready
All WS1 completion criteria pass.

### Gate B — Labels and features ready
WS2 and WS3 pass, including leakage and parity tests.

### Gate C — Research baseline ready
WS4 passes on walk-forward validation and untouched test data.

### Gate D — Paper-trading candidate
Calibration and economic simulation pass predefined thresholds documented before viewing final test results.

### Gate E — Limited advisory signal
Requires at least 500 matured candidate events, stable calibration, positive expected value after conservative costs, acceptable drawdown, and founder approval.

### Gate F — Automation
Out of scope until a separate risk, legal, security, and execution-governance review is approved.

## Definition of done
A task is done only when code, tests, documentation, reproducible evidence, failure behavior, and review status are all present. A dashboard screenshot or apparent runtime success is not completion evidence.
