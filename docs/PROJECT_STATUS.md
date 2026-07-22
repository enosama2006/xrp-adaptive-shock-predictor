# XASP Project Status

Updated: 2026-07-22

## Current verdict

The repository has a working scientific scaffold and a restart-safe incremental minute pipeline. It is not yet a validated trading system. User-facing action remains `WAIT`.

## Completed with code

- Scientific objective and governance gates.
- Arabic RTL dashboard prototype.
- Live public spot stream prototype for XRP/BTC.
- Canonical market-data contracts and Parquet storage.
- Historical Binance spot kline, funding, and open-interest clients.
- Live stream recording contracts for trades, book ticker, and liquidations.
- Data-quality checks for duplicates, ordering, latency, and sequence gaps.
- Durable dataset state and monotonic watermarks.
- Deterministic first-touch labels: `UP_10`, `DOWN_10`, `NO_EVENT`, `AMBIGUOUS`, `INCOMPLETE`.
- Rolling anchors every minute for 15/30/45/60-minute horizons.
- Pending-to-final label maturation without rebuilding all history.
- Causal price-feature engine and leakage guard.
- Temporal multinomial logistic baseline and probability calibration scaffold.
- Purge and embargo primitives.
- Restart-safe end-to-end pipeline that backfills only the missing tail, merges prices, updates anchors, and advances state.
- Unit tests for labeling, state, anchors, cadence, features, temporal splitting, and incremental resume.

## Partially completed

- Historical ingestion exists, but no committed real dataset or benchmark report exists yet.
- Derivatives endpoints exist for funding and open interest, but are not yet joined into the training matrix.
- Baseline code exists, but has not been trained and evaluated on a real large out-of-sample dataset.
- CI configuration exists, but current GitHub connector has not returned successful workflow evidence.
- Dashboard exists, but is not yet connected to a validated stored model and immutable prediction ledger.

## Remaining critical work

### Data gate

- Run real XRP/BTC backfill and record manifests, hashes, row counts, gaps, and runtime.
- Add ETH and XRP perpetual price/volume series.
- Add mark price, index price, premium/basis, liquidations, and multi-level order book.
- Prove seven consecutive days of restart-safe live collection.
- Benchmark 100k, 1m, and 10m rows.

### Dataset and features gate

- Join spot, derivatives, BTC/ETH, and microstructure features by availability time.
- Add signed volume, CVD, trade intensity, OFI, depth imbalance, microprice, spread, and liquidity-vacuum features.
- Add feature registry and offline/online parity tests.
- Generate class and event-cluster distribution reports.

### Model gate

- Execute purged walk-forward evaluation over multiple contiguous periods.
- Train unconditional, multinomial logistic, and gradient-boosting baselines.
- Add PR-AUC, calibration curves, blocked-bootstrap confidence intervals, and ablations.
- Add separate shock, direction, and time-to-hit heads.
- Prove improvement over unconditional and `NO_TRADE` baselines.

### Economic gate

- Simulate fees, spread, slippage, latency, partial fills, and adverse conditions.
- Separate prediction quality from execution quality.
- Define trade/no-trade meta-policy and risk caps.

### Continuous-learning gate

- Add immutable prediction ledger.
- Mature labels only after horizon end.
- Add drift detection, challenger training, promotion evidence, checkpoints, quarantine, and rollback.
- Never update the champion blindly from every tick.

### Product and final review gate

- Connect dashboard to stored predictions, source health, uncertainty, and explicit WAIT reasons.
- Run paper trading long enough to gather independent event clusters.
- Perform clean-room reproduction, leakage red-team, model-risk, and operational review.

## Completion definition

The project is complete only when all gates pass with reproducible evidence. Code presence alone is not completion. Until then, the official decision is `WAIT`.
