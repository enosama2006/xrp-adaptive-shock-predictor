# XASP — Execution & Review Checklist

This file is the single source of truth for completing the XRP Adaptive Shock Predictor.

Status legend:
- `[ ]` not started
- `[-]` in progress
- `[x]` completed with evidence
- `[!]` blocked or failed review

A task may be marked complete only when code, tests, reproducibility evidence, and review notes all exist.

---

## 0. Program governance

- [ ] Freeze the scientific objective: first-touch `+10%`, first-touch `-10%`, or `NO_EVENT` within 15/30/45/60 minutes.
- [ ] Freeze the rule that user-facing trade state remains `WAIT` until all promotion gates pass.
- [ ] Define approved data sources, licensing, rate limits, retention, and failure handling.
- [ ] Define model-risk roles: builder, reviewer, approver, and rollback owner.
- [ ] Define immutable experiment IDs, dataset hashes, feature schema version, and model version.
- [ ] Define incident classes: stale data, sequence gap, leakage, numerical divergence, calibration failure, drift, and execution mismatch.
- [ ] Add a decision log for every material methodology change.

### Completion gate G0

- [ ] Governance documents exist and conflict checks pass.
- [ ] No code path can emit executable LONG/SHORT.
- [ ] Every future phase references explicit acceptance criteria.

---

## 1. Data acquisition and storage

### 1.1 Spot market

- [ ] Build historical XRPUSDT and BTCUSDT trade collector.
- [ ] Build live trade collector with reconnect, backfill, deduplication, and monotonic sequence checks.
- [ ] Collect best bid/ask and multi-level order-book snapshots and deltas.
- [ ] Reconstruct order books and detect sequence gaps.
- [ ] Record exchange timestamp, receive timestamp, latency, and source health.

### 1.2 Derivatives

- [ ] Collect XRP perpetual trades and book data.
- [ ] Collect open interest and change in open interest.
- [ ] Collect funding, basis, mark price, index price, and premium.
- [ ] Collect liquidation events where available.
- [ ] Validate timestamp alignment with spot data.

### 1.3 Cross-market context

- [ ] Collect BTC, ETH, broad-market volume, breadth, and dominance proxies.
- [ ] Add session/time features without future information.
- [ ] Create an optional event/news ingestion contract, isolated from the core model.

### 1.4 Storage and quality

- [ ] Design append-only raw schemas.
- [ ] Partition by source, symbol, date, and event type.
- [ ] Add dataset manifests, row counts, min/max timestamps, checksums, and gap reports.
- [ ] Add idempotent backfill jobs.
- [ ] Add source-quality dashboards and alerts.

### Completion gate G1

- [ ] Seven consecutive days of live collection without silent gaps.
- [ ] Historical replay reproduces identical event ordering and hashes.
- [ ] Order-book reconstruction tests pass on normal, missing, duplicate, and out-of-order messages.
- [ ] All unavailable or stale sources fail closed.

---

## 2. Event labeling

- [ ] Implement immutable first-touch labels for 15, 30, 45, and 60 minutes.
- [ ] Record reference price, upper/lower barriers, first-hit side, first-hit time, MFE, MAE, and path completeness.
- [ ] Define same-timestamp dual-hit handling as `AMBIGUOUS` unless ordering is provable.
- [ ] Define incomplete windows and data-gap exclusion rules.
- [ ] Group overlapping forecasts into event clusters for evaluation.
- [ ] Create label-distribution reports by year, month, regime, and horizon.
- [ ] Test label invariance under replay and chunk boundaries.

### Completion gate G2

- [ ] Golden test fixtures cover `UP_10`, `DOWN_10`, `NO_EVENT`, `AMBIGUOUS`, and `INCOMPLETE`.
- [ ] Repeated labeling over the same raw data produces byte-identical output.
- [ ] No future-close shortcut replaces first-touch logic.

---

## 3. Feature engineering

### 3.1 Price and volatility

- [ ] Multi-scale returns: seconds to 60 minutes.
- [ ] Momentum, acceleration, jump score, realized volatility, and volatility-of-volatility.
- [ ] VWAP distance, range position, breakout strength, and trend consistency.

### 3.2 Trade flow

- [ ] Signed volume, trade imbalance, CVD, large-trade ratio, burst intensity, and price impact.
- [ ] Absorption and exhaustion proxies with explicit formulas.

### 3.3 Order-book microstructure

- [ ] Multi-level imbalance.
- [ ] Microprice, depth slope, convexity, spread, replenishment, cancellation intensity, and wall persistence.
- [ ] Liquidity-vacuum and book-depletion scores.

### 3.4 Derivatives and cross-market

- [ ] OI level/change and price–OI states.
- [ ] Funding z-score, basis, liquidations, and perpetual/spot ratios.
- [ ] BTC/ETH lead-lag, rolling beta, correlation breakdown, breadth, and dispersion.

### 3.5 Feature governance

- [ ] Create feature registry with owner, formula, source, lookback, availability time, missing policy, scaling policy, and version.
- [ ] Prove each feature uses only information available at prediction time.
- [ ] Fit scaling on training windows only.
- [ ] Add robust clipping without suppressing true market shocks.
- [ ] Add missingness indicators and source-availability masks.

### Completion gate G3

- [ ] Leakage audit passes for every feature.
- [ ] Offline and online feature calculations match within tolerance.
- [ ] Feature replay is deterministic.
- [ ] Redundant and unstable features are documented, not silently retained.

---

## 4. Baseline models

- [ ] Build unconditional event-rate baseline.
- [ ] Build multinomial logistic baseline.
- [ ] Build gradient-boosted tree baseline.
- [ ] Build separate `shock gate`, `direction`, and `time-to-barrier` baselines.
- [ ] Add class weights or focal strategy only after documented imbalance analysis.
- [ ] Calibrate probabilities using validation-only data.
- [ ] Produce feature importance and stability reports.

### Completion gate G4

- [ ] Baselines beat unconditional rates on untouched test periods.
- [ ] Class-wise precision, recall, PR-AUC, Brier score, and calibration are reported.
- [ ] Overall accuracy is never the primary success metric.
- [ ] No model is promoted because of one favorable period.

---

## 5. Temporal validation

- [ ] Implement rolling walk-forward splits.
- [ ] Implement purge and embargo for overlapping horizons.
- [ ] Reserve final untouched test periods.
- [ ] Add blocked bootstrap confidence intervals.
- [ ] Evaluate by market regime, session, liquidity, volatility, and event cluster.
- [ ] Compare against simple non-ML strategies and `NO_TRADE`.
- [ ] Run ablations: no derivatives, no order book, no BTC/ETH, no news.

### Completion gate G5

- [ ] Every result can be reproduced from dataset hash + config + commit SHA.
- [ ] Confidence intervals accompany headline metrics.
- [ ] Performance survives multiple contiguous test windows.
- [ ] Leakage and overlap audits pass independently.

---

## 6. Economic simulation

- [ ] Model fees, spread, slippage, latency, partial fills, and liquidity limits.
- [ ] Define entry, cancellation, invalidation, and expiry semantics.
- [ ] Evaluate fixed-size and risk-capped sizing separately.
- [ ] Measure expected value, profit factor, drawdown, tail loss, and false-alarm cost.
- [ ] Stress-test worse fees, wider spread, delayed entry, and exchange outages.
- [ ] Separate predictive quality from execution quality.

### Completion gate G6

- [ ] Positive expected value remains after conservative costs in multiple test windows.
- [ ] Drawdown and tail-loss limits are documented and respected.
- [ ] Results remain acceptable under adverse execution assumptions.
- [ ] No simulated fill uses unavailable future liquidity.

---

## 7. Continuous learning

- [ ] Build immutable prediction ledger.
- [ ] Delay labels until horizon completion.
- [ ] Prevent overlapping samples from inflating online metrics.
- [ ] Add drift detection for features, labels, calibration, and performance.
- [ ] Add bounded online calibration and ensemble-weight updates.
- [ ] Add champion–challenger workflow.
- [ ] Add checkpoints, rollback, quarantine, and recovery.
- [ ] Require validation evidence before any challenger promotion.

### Completion gate G7

- [ ] Online updates cannot cause numerical divergence.
- [ ] Rollback restores last-known-good state exactly.
- [ ] Drift triggers review, not blind retraining.
- [ ] Promotion and demotion decisions are logged and reproducible.

---

## 8. Product interface

- [ ] Show `P(UP_10)`, `P(DOWN_10)`, `P(NO_EVENT)`, and uncertainty.
- [ ] Show estimated time-to-barrier bands for 15/30/45/60 minutes.
- [ ] Show model state, dataset freshness, sample size, calibration, and current regime.
- [ ] Show supporting and opposing factors.
- [ ] Show source health and missing data.
- [ ] Show immutable forecast history and evaluated outcome.
- [ ] Show explicit reason for `WAIT`.
- [ ] Prevent UI from displaying implausible or quarantined predictions.

### Completion gate G8

- [ ] UI values reconcile with stored prediction records.
- [ ] Stale or failed data visibly degrades to `WAIT`.
- [ ] Browser tests cover reconnects, missing sources, corrupted state, and long sessions.
- [ ] No visual wording implies certainty or guaranteed profit.

---

## 9. Paper trading

- [ ] Run paper trading with frozen champion model.
- [ ] Preserve all decisions before outcomes are known.
- [ ] Compare live paper results with backtest expectations.
- [ ] Review false positives and missed events individually.
- [ ] Track performance by regime and source quality.
- [ ] Set minimum duration and minimum independent event-cluster counts.

### Completion gate G9

- [ ] Minimum paper-trading duration completed.
- [ ] Minimum independent-event sample achieved.
- [ ] Live calibration and execution costs align with expectations.
- [ ] No unresolved critical incidents.
- [ ] Independent review approves or rejects further use.

---

## 10. Final independent review

- [ ] Re-run all tests from a clean environment.
- [ ] Rebuild datasets and models from manifests.
- [ ] Perform red-team leakage review.
- [ ] Perform model-risk review.
- [ ] Perform execution and operational-failure review.
- [ ] Verify public/private repository and secret handling.
- [ ] Publish limitations, known failure modes, and non-guarantee statement.

### Completion gate G10

- [ ] Clean-room reproduction passes.
- [ ] All critical and high-severity findings are closed.
- [ ] Final review report contains explicit `PASS`, `CONDITIONAL PASS`, or `FAIL`.
- [ ] Until `PASS`, the production action remains `WAIT`.

---

# Immediate execution order

1. [ ] Audit the current repository against this checklist.
2. [ ] Add repository structure for collectors, schemas, research, models, tests, reports, and UI.
3. [ ] Implement deterministic historical trade ingestion first.
4. [ ] Complete label engine and golden fixtures.
5. [ ] Implement feature registry and offline feature pipeline.
6. [ ] Build temporal split, purge, and embargo utilities.
7. [ ] Train and evaluate the first scientific baselines.
8. [ ] Add derivatives and order-book pipelines.
9. [ ] Add economic simulation.
10. [ ] Add guarded continuous-learning workflow.
11. [ ] Integrate validated outputs into the dashboard.
12. [ ] Execute paper trading and independent final review.

# Self-review after every task

For each completed task, record:

- What was implemented?
- What evidence proves it works?
- What tests were added?
- What could still be wrong?
- Was any assumption introduced?
- Could future information leak in?
- Does failure produce `WAIT`?
- Does the implementation preserve reproducibility?
- Is another reviewer able to reproduce the result?

No task is complete until these questions are answered in a linked review note.
