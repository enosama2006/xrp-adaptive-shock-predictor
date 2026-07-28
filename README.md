# XRP Adaptive Shock Predictor (XASP)

## Governed target

XASP 2.0.0 presents one readable eight-hour price path built from two
scientifically separated components:

- Model A predicts the XRP closing-price distribution at the end of every hour,
  plus the maximum upside and downside excursion inside that hour;
- Model B fits one joint competing-risk distribution over
  `UP_H1..UP_H8`, `DOWN_H1..DOWN_H8`, and `NO_EVENT`;
- hourly `+2%`/`-2%` first-touch probabilities are cumulative projections of
  that one distribution and therefore cannot contradict one another;
- sub-hour 15/30/45-minute targets and interface outputs are removed;
- the main response is an eight-point price timeline with Q05/Q50/Q95 bands,
  both barrier prices, directional probability, event probability, most likely
  arrival hour, and a governed `LONG`, `SHORT`, or `WAIT` research signal.

On the first startup after this target change, XASP preserves raw observed
Binance candles and invalidates old target-derived anchors, models, reports,
and predictions before rebuilding them under the 2% definition.

XASP is a research-first, continuously evaluated XRP forecasting platform built around **two independent models** that share a governed real-data layer but do not share targets, model artifacts, prediction ledgers, quality gates, or user-facing outputs.

## The two models

### Model A — XRP Adaptive Shock Predictor

**Technical form:** future-excursion / shock-magnitude regression.

For each prediction timestamp and each cumulative hourly horizon from one
through eight hours, Model A estimates:

- the likely closing price at the end of the hour;
- a Q05/Q50/Q95 uncertainty band for that hourly close;
- the likely maximum upside excursion;
- the likely maximum downside excursion;
- uncertainty bands for both excursions;
- the corresponding likely high and low prices;
- whether evidence is strong enough to publish a research forecast or the model must remain `WAIT`.

Model A is not a renamed copy of Model B. It has its own targets, fitted models, model version, report, prediction store, and acceptance criteria.

### Model B — Joint ±2% First-Touch Time Predictor

**Technical form:** calibrated multiclass competing-risk event-time classification.

At every eligible timestamp, Model B estimates one probability distribution over:

- touch `+2%` first in hour 1 through hour 8 (`UP_H1..UP_H8`);
- touch `-2%` first in hour 1 through hour 8 (`DOWN_H1..DOWN_H8`);
- touch neither barrier within eight hours (`NO_EVENT`).

The user-facing cumulative probability at each hourly deadline is derived by
summing the relevant earlier event-time classes. It is not produced by eight
unrelated classifiers.

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

1. Backfill **1,825 days (five years)** of observed Binance `XRPUSDT`
   one-minute candles by default.
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
- mature delayed outcomes at each hourly deadline from one through eight hours;
- create a new prediction every completed minute when the corresponding model is available;
- train a challenger after a governed amount of new data, normally once per day;
- replace a champion only after the challenger passes the predefined temporal gates.

## Feature engineering contract

Raw exchange prices are preserved at full precision for auditability. Models should learn primarily from causal, scale-stable transformations:

- percentage returns and log returns;
- realized volatility, jump intensity, momentum, and acceleration;
- RSI, ATR as a fraction of price, and Bollinger position/bandwidth;
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
- 2% as the governed target-corridor context;
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
6. Model B reports multiclass log loss, Brier score, calibration, directional
   policy support/precision, and comparison with the historical class-prior baseline.
7. Model A reports interval coverage, quantile ordering, excursion error, and stability by horizon.
8. Metrics are reported by horizon, market regime, liquidity state, and independent event cluster.
9. Every forecast is written before its outcome is known and evaluated only after maturity.
10. Statistical quality is separate from profitability after fees, spread, slippage, latency, and fills.

## User-interface contract

The dashboard must visibly separate Model A and Model B. Each section shows only that model's:

- status and explicit `WAIT` reason;
- model version and training time;
- data range and sample size;
- one primary directional decision plus an hourly price-path chart and a
  coherent cumulative first-touch timeline;
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

The 2.0.0 refactor passes the repository's complete local test, lint, type,
compile, and JavaScript checks. CI and a reproducible benchmark on the user's
full five-year stored history remain required before any operational claim.

The project is **not yet a validated trading system**. Critical remaining work
includes restart-safe live order-book collection with sequence validation,
historical BTC/ETH and derivatives joins, drift-governed champion/challenger
promotion and rollback, economic simulation, and paper-trading evidence.

The official action remains `WAIT` until the relevant model passes all documented gates. No live order execution is implemented.
