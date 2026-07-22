# Completion Matrix

| ID | Deliverable | Evidence required | Current state |
|---|---|---|---|
| P0-01 | Mission and target contract | README + master plan | Complete |
| P0-02 | Governance and lifecycle | Governance document | Complete |
| P0-03 | Data contracts | Contract document | Complete |
| P0-04 | Deterministic first-touch labeler | Source + unit tests | Complete |
| P0-05 | Automated quality gate | CI workflow | Complete, awaiting first run |
| P1-01 | Raw spot collector | Code, replay test, manifest | Not started |
| P1-02 | Order-book reconstruction | Gap tests, checksum report | Not started |
| P1-03 | Derivatives collector | Source definitions, coverage report | Not started |
| P1-04 | Dataset manifest and hashing | Generated manifest | Not started |
| P1-05 | Data quality report | Automated report meeting thresholds | Not started |
| P2-01 | Feature registry | Versioned registry and lineage | Not started |
| P2-02 | Causal feature pipeline | Parity and leakage tests | Not started |
| P2-03 | Temporal split engine | Purge/embargo tests | Not started |
| P3-01 | Class-prior and logistic baselines | Walk-forward report | Not started |
| P3-02 | Gradient-boosted baseline | Walk-forward report | Not started |
| P3-03 | Calibration | Reliability report and Brier score | Not started |
| P3-04 | Untouched final test | Frozen test report | Not started |
| P4-01 | Forecast ledger | Immutability and maturity tests | Not started |
| P4-02 | Guarded online adaptation | Drift, rollback, and bounds tests | Not started |
| P4-03 | Champion–challenger | Promotion evidence and manual approval | Not started |
| P5-01 | Economic simulation | Fees, spread, slippage, latency | Not started |
| P5-02 | Paper trading | >=500 matured eligible forecasts | Not started |
| P5-03 | Advisory review | Independent report + founder approval | Not started |

## Status rules

- `Complete` means all required evidence exists and is reproducible.
- `Complete, awaiting first run` means implementation exists but no successful CI evidence has yet been observed.
- `In progress` requires committed code and an open task with explicit remaining work.
- `Not started` is used even when a concept is described but no executable artifact exists.
