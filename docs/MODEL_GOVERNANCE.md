# XASP Model Governance

## Purpose

XASP is a research system for estimating whether XRP reaches +10% or -10% first within 15–60 minutes. It is not approved for automated trading.

## Decision states

- `WAIT`: default and mandatory before all evidence gates pass.
- `LONG`: may only become available after independent validation.
- `SHORT`: may only become available after independent validation.
- `MODEL_UNAVAILABLE`: stale, incomplete, drifted, numerically unsafe, or uncalibrated model.

## Mandatory evidence gates

A candidate model cannot emit actionable signals until all gates pass:

1. **Data integrity**
   - complete timestamped source lineage;
   - no unresolved sequence gaps in reconstructed order books;
   - reproducible feature computation;
   - data freshness and missingness reported.

2. **Label integrity**
   - first-touch +10% / -10% / no-event labels;
   - exact event timestamps;
   - overlapping labels handled explicitly;
   - ambiguous or incomplete paths excluded and reported.

3. **Temporal validation**
   - walk-forward splits only;
   - purge and embargo around overlapping horizons;
   - scalers fitted on training windows only;
   - test windows never used for feature selection, calibration, or threshold tuning.

4. **Probability quality**
   - class-wise precision and recall;
   - PR-AUC for rare events;
   - Brier score and reliability curves;
   - confidence intervals via blocked bootstrap;
   - calibration assessed by market regime.

5. **Economic validation**
   - fees, spread, slippage, and latency included;
   - comparison against no-trade and simple baselines;
   - maximum drawdown and tail loss limits;
   - enough independent event clusters, not merely overlapping minute snapshots.

6. **Paper-trading validation**
   - immutable predictions created before outcomes;
   - independent execution simulator;
   - no manual removal of failed predictions;
   - predeclared promotion criteria.

## Online learning controls

Online learning uses delayed labels only. It must include:

- bounded standardized features;
- bounded target and gradient updates;
- drift detection;
- checkpoint and rollback;
- champion–challenger comparison;
- automatic quarantine for non-finite or implausible outputs;
- no model promotion based solely on recent profit.

## Rare-event controls

Because ±10% within one hour is rare:

- overall accuracy is not a promotion metric;
- event clusters must be counted independently;
- class weights or sampling methods are fitted only on training data;
- synthetic events cannot be used as test evidence;
- a high `NO_EVENT` rate is expected and reported.

## Interface obligations

Every probability shown to a user must include or make available:

- model version;
- model state;
- prediction timestamp;
- data freshness;
- feature coverage;
- number of evaluated independent events;
- calibration state;
- current market regime;
- factors supporting and opposing the estimate;
- explicit reason when the decision remains `WAIT`.

## Promotion authority

No automated code path may self-promote a model to live trading. Promotion requires a documented review and an explicit founder approval recorded in the repository.
