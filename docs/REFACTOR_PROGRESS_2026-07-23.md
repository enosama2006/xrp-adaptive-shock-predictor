# XASP Refactor Progress — 2026-07-23

This note updates `REFACTOR_AUDIT_2026-07-23.md`. The audit records the defects found before refactoring; this document records what has since changed in code and what remains unproven.

## Implemented in code

### Product and documentation

- Frozen the two-model contract across README, master plan, status, TODO, and the phased execution plan.
- Model A and Model B remain independent in names, targets, artifacts, reports, and UI/API outputs.

### Feature governance

- Added an explicit, versioned, fail-closed feature registry.
- Unknown numeric columns are excluded from training and recorded in diagnostics.
- Raw price/OHLC, raw size fields, targets, identifiers, far-book context, and diagnostics are prohibited from automatic model selection.

### Historical candle schema and point-in-time safety

- Normalized Binance close timestamps ending in `59,999` to the exact completed-candle availability boundary.
- Excluded a currently forming candle when its availability timestamp is after the collection cutoff.
- Preserved real quote volume, trade count, taker-buy base volume, and taker-buy quote volume.
- Added causal log/normalized trade-flow features, taker-buy ratio, signed-volume proxy, trade intensity, average trade size, and availability masks.

### Restart-safe bootstrap and lifecycle — Phase 1 started

- Replaced the single unbounded historical materialization with bounded checkpoint batches.
- Added configurable `checkpoint_rows` with a default of 10,000 records.
- Persisted each accepted batch atomically before the next batch is requested.
- Advanced the raw-data watermark after every successful checkpoint.
- Added restart tests that simulate a connection failure after completed checkpoints and prove the next run resumes from the persisted tail.
- Added lifecycle stages for bootstrap, missing-tail synchronization, anchor creation, feature creation, Model A/B targets and training, prediction, maturation, reporting, idle, and errors.
- Added lifecycle progress, expected rows, processed rows, checkpoint count, and current watermark to persisted status and API responses.
- Added backward-compatible migration for older status JSON files.
- Added a visible lifecycle progress panel to the Arabic dashboard.
- Added separate health flags for Model A and Model B research readiness rather than requiring both models to exist.
- Added fail-closed `ERROR` lifecycle handling while preserving confirmed checkpoints.

### Model B target correctness

- Added OHLC-aware first-touch labeling.
- Upper touch uses candle high; lower touch uses candle low.
- Same-candle dual touch is `AMBIGUOUS`.
- Missing internal minutes force `INCOMPLETE`/exclusion.
- Historical anchor building now uses the OHLC path.
- Production prediction maturation now uses the same OHLC/gap-safe method.

### Model validation

- Model B now uses horizon-specific purge and embargo across train/calibration/test boundaries.
- Model B reports PR-AUC by class when defined and expected calibration error in addition to existing class metrics and Brier scores.
- Model A now uses horizon-specific purge and embargo.
- Model A reports pinball losses, median excursion error, interval coverage, interval width, quantile ordering, and split audit information.

### Order-book safety

- Primary model bands are restricted to 5/10/25/50/100/200 bps.
- 500/1000-bps fields are explicitly context-only.
- 2000-bps fields are diagnostic-only.
- 5000 bps and farther are omitted from the feature engine.
- Distance-weighted imbalance is truncated at the widest primary band, giving far-away orders zero influence.
- Added best-level imbalance, microprice, and microprice deviation.
- Added tests designed to prove that a huge far-away order cannot flip primary pressure.

## Added tests

- Binance timestamp normalization and historical trade-flow preservation.
- Rejection of the currently forming candle after the request cutoff.
- OHLC intraminute barrier touch, same-candle ambiguity, internal-gap exclusion, and anchor integration.
- Trade-flow feature transformation and invalid taker-volume rejection.
- Order-book far-wall non-influence and context exclusion.
- Historical checkpoint persistence after simulated connection loss.
- Restart from the last confirmed checkpoint and completion of the requested range.
- Legacy status migration and lifecycle progress persistence.
- CI JavaScript syntax validation for the lifecycle dashboard.

## Still pending

- Run the complete test suite, lint, type checks, JavaScript syntax check, and import smoke check from a clean checkout after these commits.
- Record successful CI evidence; no successful workflow status has yet been returned through the connector.
- Improve historical storage from one compact Parquet file to partitioned raw/event storage with manifests and hashes.
- Refine bootstrap classification so an interrupted first-year bootstrap remains explicitly `BOOTSTRAP_HISTORY` until minimum coverage is proven.
- Add fully independent lifecycle state and production ledger/report for Model A.
- Add restart-safe live depth collection, snapshot/delta sequence validation, OFI, depletion, replenishment, cancellation, and wall persistence.
- Join BTC/ETH, funding, open interest, basis, mark/index price, and liquidations through point-in-time availability masks.
- Add independent-event clustering and full walk-forward folds beyond one train/calibration/test split.
- Implement non-blocking challenger training, drift review, quarantine, promotion evidence, and rollback.
- Produce a reproducible 365-day real-data benchmark and paper-trading evidence.

## Verification status

The code changes above are committed to `main`, but they have not been executed in this environment. They must be treated as **implemented but unverified** until the local integration launcher or GitHub Actions completes successfully.

Official action remains `WAIT`.