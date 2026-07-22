# XASP Master Plan

Updated: 2026-07-23

## Mission

Build a restart-safe, real-data XRP research platform with two scientifically independent models:

1. **Model A — XRP Adaptive Shock Predictor**: estimate likely future upside/downside excursion ranges and shock magnitude over 15/30/45/60 minutes.
2. **Model B — ±10% First-Touch Predictor**: estimate whether +10%, -10%, or neither barrier is reached first over the same horizons.

Both models share a governed point-in-time data platform, but each owns its targets, trainer, model artifacts, prediction store, production report, and readiness gate.

The platform must fail closed to `WAIT`. It must never fabricate missing historical microstructure, future labels, probabilities, or trading outcomes.

---

## Product contract

### Model A output

For every horizon:

- `max_return_q05/q50/q95`
- `min_return_q05/q50/q95`
- likely high/low prices
- model version and training timestamp
- data freshness and feature availability
- empirical interval coverage and error metrics
- model-specific state: `BOOTSTRAP`, `TRAINING`, `RESEARCH_READY`, `WAIT`, or `ERROR`

### Model B output

For every horizon:

- `p_up_10`
- `p_down_10`
- `p_no_event`
- predicted class and uncertainty
- model version and training timestamp
- data freshness and feature availability
- class-wise metrics and empirical high-confidence precision
- model-specific state: `BOOTSTRAP`, `TRAINING`, `RESEARCH_READY`, `WAIT`, or `ERROR`

### Platform output

- historical-backfill progress;
- stored-data start/end timestamps and row counts;
- gap, duplicate, and freshness status;
- independent state for each model;
- last successful collection, feature build, training, prediction, and report cycle;
- explicit reason for every `WAIT` or `ERROR` state.

---

## Non-negotiable scientific rules

1. No random train/test split for time series.
2. No future leakage in features, targets, scaling, calibration, imputation, or selection.
3. Overlapping 15/30/45/60-minute label windows require purge and embargo.
4. Final model selection cannot inspect the untouched chronological test period.
5. Model B first-touch labels use the observed path, not only the horizon close.
6. A minute candle that touches both barriers is `AMBIGUOUS` unless finer data proves ordering.
7. Model A future extrema use observed candle highs/lows, not close-only shortcuts.
8. Historical features are included only when their source existed and was point-in-time available.
9. Missing historical order-book snapshots are marked unavailable, never synthesized.
10. Scaling is fitted on training rows only and persisted with the model.
11. Overall accuracy is not a promotion metric because `NO_EVENT` can dominate.
12. Statistical quality and net trading profitability are evaluated separately.
13. No model may replace a champion merely because a retraining cycle completed.
14. No live-order execution is allowed in the current program.

---

## Operating lifecycle

### Stage 1 — `BOOTSTRAP_HISTORY`

- On a fresh installation, request at least 365 days of Binance `XRPUSDT` one-minute completed candles.
- Page the API deterministically and persist checkpoints instead of holding the full year only in memory.
- Save OHLCV plus available historical trade-flow fields: quote volume, trade count, taker-buy base, and taker-buy quote.
- Normalize candle timestamps to the exact time the completed candle became available.
- Expose progress: requested range, covered range, rows stored, pages completed, estimated remaining work, and current failure/retry state.

Acceptance:

- at least 365 days or an explicitly documented maximum source range;
- >=99.5% expected minute coverage after justified exclusions;
- no silent duplicates or backward timestamps;
- atomic, restart-safe local storage;
- source and schema manifest written.

### Stage 2 — `BUILD_FEATURES`

Build one point-in-time feature row per completed minute.

Historical base families:

- returns and log returns;
- rolling volatility, jump intensity, momentum, acceleration, range position, drawdown, and breakout features;
- volume and quote-volume `log1p` transforms;
- taker-buy ratio, signed-volume proxy, trade intensity, and average trade size;
- rolling z-score and robust median/IQR scores;
- missingness and availability masks.

Later point-in-time families:

- BTC/ETH lead-lag and correlation;
- funding, OI, basis, mark/index premium, taker flow, and liquidation pressure;
- live trades, CVD, OFI, and near-price order-book microstructure.

Acceptance:

- explicit feature registry;
- unknown numeric columns are excluded by default;
- formula, source, lookback, timestamp availability, missing policy, scaling policy, and model eligibility recorded;
- offline/live parity tests;
- leakage tests and distribution diagnostics generated.

### Stage 3 — `BUILD_TARGETS_A`

For every anchor and horizon, record observed future:

- maximum high and minimum low;
- maximum/minimum return;
- time to maximum/minimum where supported;
- completeness and ambiguity state.

Acceptance:

- contiguous future path required;
- no interpolation or fabricated candles;
- OHLC high/low used;
- deterministic rebuild.

### Stage 4 — `BUILD_TARGETS_B`

For every anchor and horizon, record:

- `UP_10`, `DOWN_10`, `NO_EVENT`, `AMBIGUOUS`, or `INCOMPLETE`;
- barrier prices;
- touch timestamp;
- MFE/MAE;
- path completeness.

Acceptance:

- OHLC-aware barrier detection;
- same-minute dual touch excluded unless order is proven by finer observations;
- internal gaps force exclusion;
- deterministic replay.

### Stage 5 — `TRAIN_MODEL_A`

- Train quantile/excursion baselines by horizon.
- Use purged chronological train/validation/test partitions.
- Tune only on train/validation.
- Evaluate interval coverage, pinball/error metrics, quantile ordering, regime stability, and simple historical baselines.

Promotion to research-ready requires predefined coverage and stability thresholds on untouched data.

### Stage 6 — `TRAIN_MODEL_B`

- Train unconditional class-rate, calibrated multinomial logistic, and gradient-boosting challengers.
- Use purged chronological train/calibration/test partitions.
- Report class counts, precision, recall, PR-AUC, Brier score, calibration error, and high-confidence empirical precision.
- Compare with `always NO_EVENT` and other non-ML controls.

Promotion to research-ready requires sufficient support for the published confidence band and stable class-wise performance.

### Stage 7 — `LIVE_RESEARCH`

Every completed minute:

1. append the missing real candle;
2. collect current live streams that are actually available;
3. validate freshness and sequence integrity;
4. calculate the feature row using only available information;
5. issue independent predictions from each available champion;
6. persist predictions before outcomes are known;
7. mature earlier predictions whose horizons ended;
8. update model-specific production reports;
9. keep any stale, unsupported, or drifted model at `WAIT`.

### Stage 8 — `DAILY_CHALLENGER`

- Normally trigger after one completed day of new minute anchors, not every minute.
- Train challengers without blocking the current champion's predictions.
- Compare on governed recent walk-forward periods.
- Promote only with documented evidence; otherwise retain champion.
- Persist rollback checkpoints and quarantine failed candidates.

---

## Order-book and supply/demand policy

### Primary model bands

- 5 bps (0.05%)
- 10 bps (0.10%)
- 25 bps (0.25%)
- 50 bps (0.50%)
- 100 bps (1%)
- 200 bps (2%)

### Context bands

- 500 bps (5%): medium context;
- 1000 bps (10%): target-corridor context;
- 2000 bps (20%): diagnostics only;
- 5000 bps (50%) and farther: prohibited from pressure/direction features.

### Required calculations

- best bid/ask and spread;
- best-level imbalance and microprice;
- depth by near band;
- normalized depth imbalance;
- exponential distance-weighted depth;
- depth slope/convexity;
- OFI from sequential updates;
- depletion, replenishment, cancellation, and persistence features;
- source freshness and sequence integrity.

### Anti-deception constraints

- raw total-book quantity is not a model feature;
- a huge far-away order cannot flip near-price imbalance;
- far depth is context only and must have an availability/method tag;
- one snapshot cannot prove persistence or spoofing;
- transient walls receive low confidence until persistence is observed;
- cancellations and repeated reappearance are modeled only from sequential book data;
- missing historical books remain missing.

---

## Validation and reporting

### Model A

- empirical interval coverage by horizon;
- median excursion error and pinball loss;
- quantile crossing rate;
- coverage by volatility/liquidity regime;
- production coverage after each matured prediction.

### Model B

- class-wise precision/recall/F1 and PR-AUC;
- Brier scores and calibration error;
- high-confidence empirical precision with support count;
- false-alert rate for `UP_10` and `DOWN_10`;
- production metrics by horizon and independent event cluster.

### Shared

- data coverage and freshness;
- feature missingness, skewness, quantiles, and histograms;
- drift alerts;
- dataset ID/hash, feature schema, commit SHA, model version;
- retraining and promotion log;
- net-of-cost paper-trading report only after prediction quality is proven.

---

## Refactor execution order

1. Freeze this dual-model contract in README, plan, status, and TODO.
2. Produce a repository audit with critical defects and evidence.
3. Add explicit feature registry and fail-closed model-feature selection.
4. Correct candle timestamp semantics and migrate legacy 59,999-ms timestamps.
5. Preserve all useful Binance kline trade-flow fields.
6. Make Model B target creation and prediction maturation OHLC-aware.
7. Integrate purge/embargo into both actual trainers, not only helper code.
8. Add visible bootstrap/training progress and non-blocking lifecycle state.
9. Separate Model A and Model B ledgers, reports, health, and readiness completely.
10. Add restart-safe live near-price order-book collection and sequence validation.
11. Join optional microstructure/derivatives/cross-market features through availability masks.
12. Implement daily champion/challenger training, comparison, quarantine, and rollback.
13. Run clean one-year backfill, benchmark, model evaluation, paper trading, and independent review.

---

## Definition of done

A model or subsystem is complete only when code, tests, point-in-time evidence, reproducible data/model IDs, failure behavior, reports, and review notes exist. A dashboard screenshot, passing unit test, or successful server startup alone is not completion evidence.