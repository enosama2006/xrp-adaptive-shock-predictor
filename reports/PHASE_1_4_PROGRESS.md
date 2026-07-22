# XASP Progress Review — Data, Labels, Features, Baseline

## Implemented

- Restart-safe raw-data watermarks and atomic state persistence.
- Historical and live market-data ingestion scaffolding.
- Incremental minute anchor dataset with 15/30/45/60 minute horizons.
- Immutable first-touch outcomes: UP_10, DOWN_10, NO_EVENT, AMBIGUOUS, INCOMPLETE.
- Pending-to-final label maturation without full historical rebuild.
- Causal minute-price feature engine with explicit availability timestamps.
- Feature-to-anchor leakage guard.
- Temporal multinomial logistic baseline with train/calibration/test ordering.
- Validation-only probability calibration when label diversity permits.
- Class-wise precision, recall, F1, support, and Brier reporting.
- Strict WAIT on insufficient rows or insufficient label diversity.
- Research-only model artifact; no trade promotion or execution path.

## Evidence added

- Unit tests for restart safety and monotonic watermarks.
- Unit tests for incremental anchor maturation and deduplication.
- Unit tests for first-touch outcomes and temporal purge/embargo primitives.
- Unit tests for causal feature construction and future-availability rejection.
- Unit tests for baseline WAIT gates, exclusions, and temporal training.

## Not yet scientifically complete

1. Run CI from a clean environment and resolve all lint/type/test failures.
2. Execute real Binance historical backfill and publish dataset manifest/hash.
3. Complete seven consecutive days of live collection without silent gaps.
4. Add reconstructed multi-level order book and sequence-gap recovery.
5. Add XRP perpetual, funding, open interest, basis, mark/index price, and liquidations.
6. Add BTC/ETH and market-regime features.
7. Implement walk-forward folds using the existing purge/embargo primitive.
8. Add gradient-boosted baseline and compare with unconditional event rates.
9. Add PR-AUC, calibration curves, confidence intervals, and ablation studies.
10. Add conservative fees, spread, slippage, latency, and liquidity simulation.
11. Add immutable live prediction ledger and delayed online evaluation.
12. Add champion/challenger, drift detection, rollback, and quarantine.
13. Integrate validated outputs into the dashboard.
14. Complete frozen-model paper trading and independent review.

## Current governance verdict

**CONDITIONAL FAIL / WAIT**

The repository now supports reproducible research construction, but no validated edge has been demonstrated on real untouched data. The user-facing trading state must remain WAIT.
