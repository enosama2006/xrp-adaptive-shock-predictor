# XASP Refactor Progress — 2026-07-23

This note updates `REFACTOR_AUDIT_2026-07-23.md`. The audit records the defects found before refactoring; this document records what has since changed in code and what remains unproven.

## Implemented in code

### Product and documentation

- Frozen the two-model contract across README, master plan, status, and TODO.
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

## Still pending

- Run the complete test suite and static checks from a clean checkout after these commits.
- Record successful CI evidence; no commit status is currently available through the connector.
- Implement page/chunk checkpointing and visible bootstrap progress.
- Avoid holding the entire one-year API result only in memory before final persistence.
- Add fully independent lifecycle state and production ledger/report for Model A.
- Add restart-safe live depth collection, snapshot/delta sequence validation, OFI, depletion, replenishment, cancellation, and wall persistence.
- Join BTC/ETH, funding, open interest, basis, mark/index price, and liquidations through point-in-time availability masks.
- Add independent-event clustering and full walk-forward folds beyond one train/calibration/test split.
- Implement non-blocking challenger training, drift review, quarantine, promotion evidence, and rollback.
- Produce a reproducible 365-day real-data benchmark and paper-trading evidence.

## Verification status

The code changes above are committed to `main`, but they have not been executed in this environment because the GitHub repository cannot be cloned from the runtime network and no CI status is currently returned. They must be treated as **implemented but unverified** until the local integration launcher or CI completes successfully.

Official action remains `WAIT`.