# XASP Project Status

Updated: 2026-07-23

## Current verdict

XASP now has a clear dual-model product definition and a substantial real-data scaffold, but it is **not yet scientifically complete or validated for trading use**.

Official action: `WAIT`.

The code can start, backfill Binance minute candles, build features and targets, fit research models, persist predictions, and expose a dual-model dashboard. However, several critical implementation gaps still prevent a production-readiness claim.

---

## Model definitions

### Model A — XRP Adaptive Shock Predictor

Current implementation:

- future high/low excursion targets from observed OHLC candles;
- quantile regression by 15/30/45/60-minute horizon;
- persisted model bundle, report, and prediction file;
- empirical interval-coverage gate;
- separate API and dashboard section.

Current limitation:

- it is currently a future-excursion quantile baseline, not yet a full multi-source adaptive shock ensemble;
- purge/embargo is not yet integrated into its trainer;
- no independent production ledger maturation equivalent to Model B;
- derivatives, BTC/ETH, trade-flow, and order-book features are not yet joined.

### Model B — ±10% First-Touch Predictor

Current implementation:

- deterministic first-touch label states;
- 15/30/45/60-minute anchors;
- calibrated multinomial logistic baseline;
- persisted model bundle, report, and immutable prediction ledger;
- delayed outcome maturation and production reporting;
- separate API and dashboard section.

Current limitation:

- historical labels and ledger maturation still use close-only minute points in the current path;
- candle highs/lows must be used so intraminute touches are not missed;
- same-candle dual touches must remain `AMBIGUOUS`;
- purge/embargo helpers exist but are not yet used by the actual trainer;
- rare-event support for ±10% may be insufficient even with one year of minute data.

---

## Completed with code

- Restart-safe incremental Binance `XRPUSDT` one-minute kline pipeline.
- Local Parquet storage with deduplication and atomic writes.
- One-year bootstrap start calculation in the Windows launcher.
- Causal price and volume feature generation.
- Log returns, rolling volatility, z-scores, robust median/IQR scores, and feature diagnostics.
- Independent target/model paths for future excursion and first touch.
- Independent API endpoints and visibly separate dashboard sections.
- First-touch probability calibration compatibility across supported scikit-learn releases.
- Model persistence and restart loading.
- Prediction ledger and delayed first-touch outcome reporting.
- Production report endpoint and report history.
- Order-book proximity feature module with near-price bands and distance decay.
- Unit/integration tests and fail-closed Windows startup verification.

---

## Critical findings from the refactor audit

### P0 — correctness

1. **Candle timestamp semantics**: Binance kline close timestamps end in `59,999`; anchor timestamps are minute boundaries. The ingestion path must normalize completed candles to an exact availability boundary and migrate legacy local rows.
2. **Model B OHLC labeling**: close-only first-touch logic can miss a +10% or -10% touch that occurred inside a minute candle.
3. **Internal path gaps**: reaching the final horizon timestamp is not enough if intermediate minutes are missing.
4. **Purged validation not wired**: helper code exists, but current trainers still use simple chronological fractions.
5. **Implicit feature selection**: the runtime currently treats most numeric feature columns as model inputs. An explicit registry is required so raw price, total far-book quantity, diagnostic context, and accidental columns cannot enter training.

### P1 — operating lifecycle

6. The first one-year backfill currently runs inside one worker cycle and does not expose detailed progress.
7. The pipeline materializes the full requested history before the final write; checkpointed page/chunk persistence is required.
8. The API does not yet expose separate lifecycle progress for collection, feature building, Model A training, and Model B training.
9. Champion/challenger comparison, quarantine, and rollback are not complete.
10. Production readiness currently depends too much on file/model presence rather than independent model-specific evidence.

### P1 — data and features

11. Binance kline payload includes quote volume, trade count, and taker-buy fields, but the current compact price table discards them.
12. Historical BTC/ETH and derivatives clients exist only partially and are not joined into the runtime matrix.
13. The order-book proximity module is not connected to a restart-safe live depth collector.
14. Historical public Binance REST does not provide a year of order-book snapshots; these features must begin live collection and use availability masks rather than fabricated backfill.
15. Raw total-book quantities and far-away walls must be excluded from model pressure features.

### P2 — evaluation and economics

16. Model A and Model B still need full walk-forward evaluation across multiple contiguous periods.
17. Model B needs PR-AUC and calibration-error reporting in addition to current class metrics.
18. Model A needs pinball loss, quantile crossing, and regime-specific coverage.
19. Prediction quality has not been proven to survive fees, spread, slippage, latency, and fill constraints.
20. No clean one-year benchmark artifact or successful CI evidence is committed yet.

---

## Refactor currently in progress

- Documentation and project contract frozen for two independent models.
- Explicit feature registry and fail-closed feature selection.
- Near-price order-book safety tests, including far-wall non-influence.
- Richer historical kline schema using real taker/trade-flow fields.
- OHLC-aware Model B target creation and production maturation.
- Purge/embargo integration in both trainers.
- Visible bootstrap/training lifecycle.

---

## Readiness gates

### Data ready

- 365-day minimum minute history stored;
- coverage and gap report passes;
- timestamps normalized and monotonic;
- restart/resume proven;
- no fabricated unavailable features.

### Model A research ready

- purged chronological evaluation complete;
- untouched-test interval coverage and error thresholds pass;
- quantiles ordered and stable by horizon/regime;
- production predictions mature and remain within tolerance.

### Model B research ready

- OHLC first-touch labels complete and gap-safe;
- sufficient `UP_10` and `DOWN_10` support;
- purged chronological calibration/testing complete;
- high-confidence empirical precision and class-wise metrics pass;
- live production calibration remains stable.

### Trading advisory candidate

Requires both statistical validation and a separate net-of-cost paper-trading review. Code startup, visible boxes, or a single favorable period are not sufficient.

---

## Honest status label

**Refactor phase — real-data dual-model research platform, not validated trading software.**