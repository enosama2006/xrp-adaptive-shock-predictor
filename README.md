# XRP Adaptive Shock Predictor (XASP)

A research-first, continuously evaluated market-event predictor for XRP.

## Research objective

Estimate, at each prediction timestamp, the probability that XRP reaches **+10%** or **-10%** from the current reference price first within a rolling **15–60 minute** horizon.

The product must output one of four states:

- `UP_10`
- `DOWN_10`
- `NO_EVENT`
- `UNCERTAIN`

This repository starts as a transparent browser prototype and evolves only through evidence-gated research phases.

## Non-negotiable scientific rules

1. No random train/test split for time series.
2. No future leakage in features, scaling, labels, or calibration.
3. Labels use first-touch barrier outcomes, not only horizon-close returns.
4. Overlapping prediction windows require purging and embargo in evaluation.
5. Accuracy alone is forbidden as a success metric because `NO_EVENT` dominates.
6. Signals remain `WAIT` until out-of-sample and paper-trading gates pass.
7. Online updates use delayed labels, bounded updates, drift checks, rollback, and champion–challenger promotion.
8. Every displayed probability must expose model state, data freshness, sample size, uncertainty, and evaluation status.
9. No live order execution is implemented in this phase.

## Initial architecture

```text
Market streams
  -> timestamp validation and synchronization
  -> rolling feature engine
  -> barrier-event registry
  -> baseline probabilistic model
  -> probability calibration
  -> execution-feasibility gate
  -> prediction ledger
  -> delayed outcome evaluation
  -> guarded online adaptation
```

## Phase 0 deliverables

- Arabic RTL research dashboard.
- Live public Binance spot streams for XRP and BTC.
- Transparent microstructure and multi-horizon features.
- +10% / -10% barrier monitoring over 15, 30, 45, and 60 minutes.
- Prediction ledger with delayed evaluation.
- Strict `WAIT` policy until sufficient evidence exists.
- Research governance and data contracts.

## Status

**Phase 0 — scientific scaffold.** The repository does not claim a validated trading edge and must not be used for automated execution.
