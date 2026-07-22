# Implementation Backlog

## Epic 1 — Reproducible market data

### T1.1 Spot trade collector
- Persist XRPUSDT and BTCUSDT trades as append-only UTC partitions.
- Reconnect safely and detect duplicate trade ids.
- Emit per-partition manifest and hash.

**Done when:** replay test reproduces identical clean rows and the daily quality report meets WS1 thresholds.

### T1.2 Order-book collector and reconstruction
- Capture initial snapshots and sequential deltas.
- Detect sequence gaps and force snapshot recovery.
- Produce depth summaries and reconstruction checksums.

**Done when:** deterministic replay reconstructs the same book and all injected gap tests are detected.

### T1.3 Derivatives collector
- Capture open interest, funding, mark/index prices, basis, taker flow, and liquidations where available.
- Record source semantics and publication delay.

**Done when:** coverage report and data dictionary exist and missing-source behavior is tested.

## Epic 2 — Research dataset

### T2.1 Anchor generation
- Generate anchors at a fixed cadence without using future information.
- Record reference price and source watermark.

### T2.2 Multi-horizon labels
- Run first-touch labeling for 15/30/45/60 minutes.
- Store ambiguity, incompleteness, MFE, and MAE.

### T2.3 Overlap clustering
- Group heavily overlapping anchors for honest uncertainty estimation.

**Epic done when:** immutable dataset, manifest, hashes, and label audit are reproducible from raw data.

## Epic 3 — Feature system

### T3.1 Feature registry
Every feature declares source, formula, lookback, availability time, missing policy, and version.

### T3.2 Baseline features
Implement returns, volatility, jumps, flow imbalance, spread, microprice, BTC lead-lag, and liquidity.

### T3.3 Training/live parity
The same feature implementation must serve offline and streaming paths.

**Epic done when:** leakage audit and parity suite pass.

## Epic 4 — Scientific baselines

### T4.1 Chronological folds
Implement walk-forward folds with purge and embargo.

### T4.2 Baseline models
Train priors, multinomial logistic regression, and gradient-boosted trees.

### T4.3 Calibration and uncertainty
Generate reliability curves, Brier score, expected calibration error, and blocked-bootstrap intervals.

**Epic done when:** validation and untouched-test reports are reproducible and compared against trivial controls.

## Epic 5 — Continuous learning

### T5.1 Immutable forecast ledger
Persist issued probabilities and source/model watermarks.

### T5.2 Matured-label evaluator
Evaluate only after the full path matures.

### T5.3 Champion–challenger
Bound updates, detect drift, checkpoint, rollback, quarantine, and require manual promotion.

**Epic done when:** adversarial tests cannot mutate history, leak labels, auto-promote, or emit a signal after critical failure.

## Epic 6 — Economic and paper-trading validation

### T6.1 Execution simulator
Model fees, spread, slippage, latency, partial fills, and missed fills.

### T6.2 Pre-registered signal policy
Freeze thresholds before final evaluation.

### T6.3 Paper trading
Accumulate at least 500 matured eligible forecasts and report directional counts separately.

**Epic done when:** all Gate E criteria pass or the model is explicitly rejected.
