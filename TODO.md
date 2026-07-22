# XASP — Refactor & Production-Readiness Checklist

Updated: 2026-07-23

Status legend:

- `[ ]` not started
- `[-]` in progress / code exists but evidence is incomplete
- `[x]` completed with code, tests, and reproducible evidence
- `[!]` blocked or failed review

Nothing is complete merely because a file or dashboard box exists.

---

## G0 — Freeze the product contract

- [x] Define Model A as independent future-excursion / adaptive-shock magnitude forecasting.
- [x] Define Model B as independent ±10% first-touch classification.
- [x] Separate the two models in the dashboard and API names.
- [x] Document that both models share data/features only, not targets, artifacts, reports, or readiness.
- [x] Preserve `WAIT` as the official action until evidence gates pass.
- [x] Prohibit fabricated market rows, labels, order books, probabilities, and fills.
- [ ] Add a formal decision log for every methodology change.

## G1 — Historical and live data lifecycle

- [-] Backfill at least 365 days of `XRPUSDT` one-minute completed candles.
- [ ] Normalize Binance close timestamps to exact completed-candle availability boundaries.
- [ ] Migrate legacy local timestamps ending in `59,999` safely and idempotently.
- [ ] Persist backfill in restart-safe pages/chunks rather than only after full materialization.
- [ ] Expose bootstrap progress and retry/failure state through API and UI.
- [-] Resume only the missing tail with overlap and deduplication.
- [ ] Preserve quote volume, trade count, taker-buy base, and taker-buy quote.
- [ ] Add historical BTCUSDT and ETHUSDT minute context.
- [ ] Join funding, OI, mark/index/basis, and liquidation streams by availability time.
- [ ] Produce manifests, hashes, gap reports, and source coverage reports.
- [ ] Prove seven consecutive days of restart-safe live collection.

### G1 acceptance

- [ ] >=365 days stored or source limitation explicitly documented.
- [ ] >=99.5% justified minute coverage.
- [ ] no silent duplicates, backward timestamps, or unresolved gaps.
- [ ] startup after interruption resumes from the correct watermark.

---

## G2 — Feature engineering and governance

### Historical price/trade-flow features

- [x] returns and log returns.
- [x] rolling volatility, range position, drawdown, and distance from low.
- [x] rolling price z-score and robust return z-score.
- [x] `log1p(volume)` and rolling volume normalization.
- [x] feature histograms, quantiles, skewness, IQR, and missingness diagnostics.
- [ ] quote-volume normalization.
- [ ] taker-buy ratio and signed-volume proxy.
- [ ] trade intensity and average trade size.
- [ ] volatility-of-volatility, VWAP distance, breakout strength, and trend consistency.

### Explicit feature registry

- [-] Replace implicit "all numeric columns" selection with a fail-closed registry.
- [ ] Record formula, source, lookback, availability, missing policy, scaling, and model eligibility.
- [ ] Unknown numeric columns excluded by default and listed in diagnostics.
- [ ] Add source-availability masks and missingness indicators.
- [ ] Add offline/live feature parity tests.

### Order-book and supply/demand rules

- [x] Implement near-price bands: 5/10/25/50/100/200/500/1000 bps.
- [x] Implement exponential distance-weighted depth.
- [ ] Restrict primary pressure features to <=200 bps.
- [ ] Treat 500/1000 bps as context, not dominant pressure.
- [ ] Treat 2000 bps as diagnostics only.
- [ ] Exclude 5000 bps and farther from model influence.
- [ ] Remove raw total-book quantity from model-eligible features.
- [ ] Add best-level imbalance and microprice.
- [ ] Add sequential OFI, depletion, replenishment, cancellation, and persistence.
- [ ] Add test: a huge far-away bid cannot flip near-price imbalance.
- [ ] Add test: one snapshot cannot claim persistent wall support/resistance.
- [ ] Build restart-safe live depth collector with sequence-gap detection.
- [ ] Use availability masks; never fabricate historical book snapshots.

### G2 acceptance

- [ ] every model feature is explicitly registered.
- [ ] no future information in feature calculation or learned scaling.
- [ ] raw price precision preserved, but raw price is not an ungoverned model input.
- [ ] far-book spoof-like quantities cannot dominate direction features.

---

## G3 — Model B target correctness

- [x] Define `UP_10`, `DOWN_10`, `NO_EVENT`, `AMBIGUOUS`, and `INCOMPLETE`.
- [x] Create 15/30/45/60-minute anchors.
- [ ] Use candle high/low for barrier touches instead of close-only points.
- [ ] Mark same-candle dual hit as `AMBIGUOUS` unless finer ordering is available.
- [ ] Require contiguous minute coverage across the full label horizon.
- [ ] Use OHLC-aware maturation for the production prediction ledger.
- [ ] Record exact label methodology version in every dataset/model/prediction.
- [ ] Add replay and chunk-boundary invariance tests.

### G3 acceptance

- [ ] no intraminute barrier touch is silently missed.
- [ ] no gap-containing path becomes `NO_EVENT`.
- [ ] repeated builds are deterministic and idempotent.

---

## G4 — Model A target correctness

- [x] Use observed high for future maximum and observed low for future minimum.
- [x] Create independent targets and model artifact path.
- [x] Train horizon-specific quantile models.
- [ ] Require and report contiguous path coverage explicitly.
- [ ] Add time-to-high and time-to-low heads or document why deferred.
- [ ] Add pinball loss, median excursion error, and quantile-crossing report.
- [ ] Add independent Model A prediction maturation ledger.

---

## G5 — Temporal validation

- [x] Implement purge/embargo helper primitives.
- [ ] Integrate purge/embargo into Model A trainer.
- [ ] Integrate purge/embargo into Model B train/calibration/test split.
- [ ] Add rolling walk-forward folds.
- [ ] Preserve a final untouched chronological test period.
- [ ] Add independent event-cluster evaluation.
- [ ] Add blocked-bootstrap confidence intervals.
- [ ] Run ablations by feature family.

### Model A metrics

- [x] empirical interval coverage gate scaffold.
- [ ] coverage by horizon and regime.
- [ ] pinball loss and quantile crossing.
- [ ] comparison with simple historical-volatility/range baselines.

### Model B metrics

- [x] class-wise precision/recall/F1 and Brier scores.
- [x] high-confidence empirical precision gate scaffold.
- [ ] PR-AUC by class.
- [ ] expected calibration error and reliability curves.
- [ ] false-alert rate for `UP_10` and `DOWN_10`.
- [ ] comparison with unconditional and always-`NO_EVENT` baselines.

---

## G6 — Runtime lifecycle and model isolation

- [x] Persist Model A and Model B separately.
- [x] Expose separate latest-result endpoints.
- [x] Display separate dashboard sections.
- [ ] Add lifecycle states: `BOOTSTRAP_HISTORY`, `BUILD_FEATURES`, `TRAIN_A`, `TRAIN_B`, `LIVE`, `ERROR`.
- [ ] Expose separate progress and `WAIT` reasons per model.
- [ ] Allow one model to be research-ready while the other remains `WAIT`.
- [ ] Load valid champions immediately on restart while missing data backfills in background.
- [ ] Prevent first bootstrap/training cycle from freezing progress visibility.
- [ ] Add independent Model A and Model B report/ledger endpoints.

---

## G7 — Continuous learning

- [-] Append new minute data and mature delayed outcomes.
- [-] Trigger governed retraining after new finalized rows.
- [ ] Train challengers without interrupting champion inference.
- [ ] Compare challengers on purged recent walk-forward windows.
- [ ] Add drift checks for features, labels, calibration, and performance.
- [ ] Add model quarantine and rollback.
- [ ] Log promotion/rejection evidence.
- [ ] Never promote merely because daily training completed.

---

## G8 — Product interface

- [x] Separate Model A and Model B visually.
- [x] Show independent outputs for 15/30/45/60 minutes.
- [ ] Show bootstrap coverage and progress.
- [ ] Show independent model state, version, training range, and sample count.
- [ ] Show model-specific accuracy/coverage reports.
- [ ] Show source health and missing feature families.
- [ ] Show explicit human-readable `WAIT` reason.
- [ ] Reconcile every visible value with a persisted record.
- [ ] Add browser tests for long bootstrap, restart, missing API, stale data, and one-model-only readiness.

---

## G9 — Economic and operational evaluation

- [ ] Separate statistical prediction quality from trade execution quality.
- [ ] Simulate fees, spread, slippage, latency, partial fills, missed fills, and liquidity caps.
- [ ] Use only liquidity available at decision time.
- [ ] Run frozen-champion paper trading.
- [ ] Track expected value, drawdown, tail loss, profit factor, and false-alert cost.
- [ ] Complete independent leakage, model-risk, and operational review.

---

# Immediate refactor order

1. [-] Freeze documentation and publish repository audit.
2. [-] Add explicit feature registry and order-book non-influence tests.
3. [ ] Correct timestamp semantics and preserve historical trade-flow fields.
4. [ ] Make Model B labels and ledger maturation OHLC/gap aware.
5. [ ] Wire purge/embargo into both trainers.
6. [ ] Add checkpointed bootstrap progress and lifecycle API.
7. [ ] Separate model reports/ledgers/readiness fully.
8. [ ] Add live order-book collection and availability masks.
9. [ ] Add BTC/ETH and derivatives joins.
10. [ ] Implement champion/challenger, drift, rollback, and clean one-year evidence run.

# Review questions after every change

- What real source supports this field?
- Was it available at prediction time?
- Could future information leak through scaling, imputation, labels, or selection?
- Could a far-away or transient order distort the signal?
- Does a missing source become an availability mask or `WAIT`, rather than fabricated data?
- Are Model A and Model B still isolated?
- Is the result reproducible from data ID, feature schema, config, commit, and model version?
- Does failure remain visible and fail closed?