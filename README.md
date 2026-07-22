# XRP Adaptive Shock Predictor (XASP)

XASP is a research-first, continuously evaluated XRP forecasting platform built around **two independent models** that share a governed real-data layer but do not share targets, model artifacts, prediction ledgers, quality gates, or user-facing outputs.

## The two models

### Model A — XRP Adaptive Shock Predictor

**Technical form:** future-excursion / shock-magnitude regression.

For each prediction timestamp and each horizon (15, 30, 45, and 60 minutes), Model A estimates:

- the likely maximum upside excursion;
- the likely maximum downside excursion;
- uncertainty bands for both excursions;
- the corresponding likely high and low prices;
- whether evidence is strong enough to publish a research forecast or the model must remain `WAIT`.

Model A is not a renamed copy of Model B. It has its own targets, fitted models, model version, report, prediction store, and acceptance criteria.

### Model B — ±10% First-Touch Predictor

**Technical form:** calibrated multiclass first-touch classification.

At every eligible timestamp, Model B estimates whether XRP will:

- touch `+10%` first (`UP_10`);
- touch `-10%` first (`DOWN_10`);
- touch neither barrier within the selected horizon (`NO_EVENT`).

When both barriers occur inside the same minute candle and their true order cannot be proven, the label is `AMBIGUOUS` and is excluded from supervised training and production scoring. Missing or incomplete future paths are also excluded.

## Shared data platform, isolated learning paths

Both models may consume the same **point-in-time feature row**, but each model owns:

- a separate target table;
- a separate training routine;
- a separate model bundle and version;
- a separate validation report;
- a separate prediction history;
- a separate readiness state and `WAIT` reason.

One model may become research-ready while the other remains `WAIT`.

## Startup and continuous data lifecycle

### First startup

1. Backfill at least **365 days** of observed Binance `XRPUSDT` one-minute candles.
2. Store completed candles locally and atomically.
3. Build causal features using only information available at each anchor timestamp.
4. Build the independent targets for Model A and Model B.
5. Run chronological training, calibration, purge/embargo checks, and untouched-test evaluation.
6. Publish only the model that passes its own evidence gate.

A fresh installation must not fabricate an immediate prediction while the historical backfill or first training run is incomplete.

### Later startups

- load the last accepted model bundles immediately;
- display the latest valid research predictions if they are still fresh;
- backfill only missing candles since the last local watermark;
- append each new completed minute candle;
- mature delayed outcomes at 15/30/45/60 minutes;
- create a new prediction every completed minute when the corresponding model is available;
- train a challenger after a governed amount of new data, normally once per day;
- replace a champion only after the challenger passes the predefined temporal gates.

## Feature engineering contract

Raw exchange prices are preserved at full precision for auditability. Models should learn primarily from causal, scale-stable transformations:

- percentage returns and log returns;
- realized volatility, jump intensity, momentum, and acceleration;
- rolling range position, drawdown, distance from highs/lows, and breakout strength;
- rolling z-scores fitted from past values only;
- robust normalization using median and IQR for heavy-tailed features;
- `log1p` compression for volume, depth, liquidation, and other highly skewed non-negative variables;
- missingness indicators and source-availability masks;
- BTC/ETH context, derivatives, trade flow, and microstructure only when their timestamps prove they were available at the prediction time.

Any learned imputer, scaler, calibrator, or quantile transform must be fitted on the training partition only.

## Order-book and supply/demand rules

Order-book features must represent **executable liquidity near the current tradable price**, not total visible quantity across an arbitrary depth snapshot.

Primary bands:

- 0.05%, 0.10%, 0.25%, 0.50%, 1%, and 2% from the mid-price;
- 5% as medium-distance context;
- 10% as target-corridor context;
- 20% as diagnostic context only;
- 50% and farther must not influence model pressure or direction features.

Required protections:

- distance-weighted depth so influence decays rapidly with price distance;
- near-band bid/ask imbalance and spread;
- microprice and best-level pressure;
- order-flow imbalance, depletion, replenishment, and cancellation/persistence measures when sequential book data exists;
- large far-away walls cannot flip the near-price pressure signal;
- a single snapshot cannot prove wall persistence and must not label a wall as durable;
- historical order-book values must never be invented when the exchange API cannot provide them.

## Scientific validation rules

1. No random train/test split for time series.
2. No future leakage in features, labels, scaling, calibration, or model selection.
3. Overlapping horizons require purge and embargo.
4. A final chronological test period remains untouched until model selection is complete.
5. `NO_EVENT` class dominance means overall accuracy is not an acceptance metric.
6. Model B reports class-wise precision/recall, PR-AUC, Brier score, calibration, and high-confidence empirical precision.
7. Model A reports interval coverage, quantile ordering, excursion error, and stability by horizon.
8. Metrics are reported by horizon, market regime, liquidity state, and independent event cluster.
9. Every forecast is written before its outcome is known and evaluated only after maturity.
10. Statistical quality is separate from profitability after fees, spread, slippage, latency, and fills.

## User-interface contract

The dashboard must visibly separate Model A and Model B. Each section shows only that model's:

- status and explicit `WAIT` reason;
- model version and training time;
- data range and sample size;
- latest outputs for 15/30/45/60 minutes;
- uncertainty and quality gate;
- production accuracy/coverage report;
- prediction history and matured outcomes.

A box appearing on screen is not evidence that a model is trained or working.

## Current state

The repository now contains:

- normalized completed-minute Binance OHLCV ingestion with historical quote-volume, trade-count, and taker-flow fields;
- explicit fail-closed feature selection and causal trade-flow transformations;
- OHLC-aware, gap-safe first-touch target creation and production maturation;
- purged and embargoed chronological evaluation paths for both models;
- near-price order-book features whose model pressure cannot be flipped by far-away walls;
- independent model artifacts, APIs, dashboard sections, and evidence gates.

These changes are part of an active refactor and still require a clean local/CI verification run and a reproducible real 365-day benchmark.

The project is **not yet a validated trading system**. Critical remaining work includes checkpointed visible bootstrap progress, independent Model A production maturation/reporting, restart-safe live order-book collection with sequence validation, historical BTC/ETH and derivatives joins, drift-governed champion/challenger promotion and rollback, economic simulation, and paper-trading evidence.

The official action remains `WAIT` until the relevant model passes all documented gates. No live order execution is implemented.