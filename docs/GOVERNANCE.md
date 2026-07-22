# Research and Model Governance

## Authority model

- **Founder approval:** changes to target definition, barriers, signal policy, economic assumptions, or live-execution scope.
- **Model owner:** scientific design, feature registry, training, evaluation, and model cards.
- **Data owner:** source contracts, timestamps, quality, lineage, retention, and incident handling.
- **Independent reviewer:** leakage audit, reproducibility, metric review, and promotion recommendation.

One person may perform several roles during prototyping, but the evidence artifacts and approval decisions must remain distinct.

## Immutable principles

1. The target is first touch of +10% or -10% from the reference price within a declared horizon.
2. A prediction is not a result until its full path window has matured.
3. Historical performance must be evaluated in chronological order.
4. Training, validation, calibration, and final testing are separate periods.
5. Overlapping labels require purge and embargo.
6. Scaling, imputation, feature selection, and calibration are fitted on training data only.
7. Missing or stale data reduces confidence and can force `WAIT`.
8. No model promotes itself.
9. No hidden change to thresholds after viewing final-test results.
10. Every forecast and model version is auditable.

## Model lifecycle

`DRAFT -> RESEARCH -> VALIDATED -> PAPER_TRADING -> ADVISORY_CANDIDATE -> APPROVED`

A model can also enter:

- `QUARANTINED`
- `REJECTED`
- `RETIRED`

## Promotion gates

### Research to validated
- data and leakage audits pass;
- walk-forward results are reproducible;
- probabilities are calibrated or explicitly marked uncalibrated;
- class-specific performance is reported;
- performance exceeds trivial baselines on validation.

### Validated to paper trading
- untouched test period completed;
- economic simulation includes conservative costs;
- failure modes and uncertainty documented;
- no critical reproducibility or integrity issue remains.

### Paper trading to advisory candidate
Pre-registered thresholds must all pass:
- at least 500 matured eligible forecasts;
- minimum 100 eligible forecasts in each emitted directional class, unless founder approves a longer observation period instead;
- directional precision and calibration thresholds defined before evaluation;
- positive net expected value under conservative costs;
- maximum drawdown below approved risk limit;
- no unresolved data-quality incident;
- stable results across more than one market regime.

### Advisory candidate to approved
Requires explicit founder approval and an independent review report. Approval can be revoked immediately.

## Continuous-learning controls

- Online samples are accepted only after label maturity.
- Ambiguous, incomplete, stale, or anomalous samples are excluded with a reason code.
- Updates are bounded by target, feature, gradient, step, and weight-norm limits.
- Last-known-good and bootstrap checkpoints are retained.
- Challenger models cannot replace the champion automatically.
- Drift can demote the system to `WAIT` automatically.

## Required evidence artifacts

Every candidate release must include:

- dataset manifest and hashes;
- feature registry;
- label specification;
- leakage audit;
- split manifest;
- model card;
- calibration report;
- walk-forward report;
- economic simulation;
- failure-mode report;
- review decision and approver.

## Incident policy

Critical incidents include timestamp corruption, future leakage, sequence gaps, probability non-finiteness, model divergence, ledger mutation, or unsupported signal emission.

On a critical incident:
1. force `WAIT`;
2. quarantine affected models;
3. preserve logs and state;
4. identify affected forecasts;
5. repair and rerun evidence;
6. require review before reactivation.

## Forbidden claims

The product must not claim guaranteed profit, guaranteed direction, deterministic knowledge of a future price, or production readiness based only on backtesting.
