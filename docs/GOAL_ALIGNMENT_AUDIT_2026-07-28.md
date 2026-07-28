# XASP Goal Alignment Audit — 2026-07-28

## Product question

The platform must answer one readable question:

> How is XRP most likely to move from now through the next eight hours?

The required output is an hourly price path, not merely eight unrelated
first-touch classifications:

- current anchor price;
- Q05/Q50/Q95 closing price at the end of hours 1 through 8;
- likely maximum high and minimum low inside each cumulative horizon;
- probability that +2% is touched first by each hour;
- probability that -2% is touched first by each hour;
- probability that neither barrier has been touched yet;
- most likely direction and arrival hour;
- a research `LONG`, `SHORT`, or `WAIT` signal whose policy was selected on
  validation data and verified unchanged on untouched chronological data.

## Findings in 1.9.0

### 1. The displayed timeline was not a price path

Model A predicted only future maximum and minimum excursion. Those values
describe the envelope inside a horizon; they do not describe where price is
expected to close at the end of each hour.

### 2. Cumulative probabilities came from unrelated models

Model B fitted one classifier for every horizon. A two-hour event probability
could therefore be greater than the three-hour probability. The presentation
layer detected this contradiction but still used the inconsistent values to
estimate the arrival hour.

### 3. Display thresholds were not the trained decision policy

The combined forecast used fixed event/direction thresholds in presentation
code. They were not selected from validation evidence and did not correspond to
the stricter training gate.

### 4. Forecast availability and trading promotion were conflated

A useful calibrated forecast could be hidden when a high-confidence trading
gate failed, while the presentation layer could independently emit LONG/SHORT.
The system did not cleanly separate forecast availability, research signal
eligibility, and live-order execution.

### 5. The implemented data scope remains narrower than the intended platform

The active historical matrix is still dominated by XRPUSDT OHLCV, quote volume,
trade count, and taker flow. BTC/ETH context, funding, open interest, basis,
liquidations, and restart-safe order-book history remain follow-on work. They
must not be described as trained inputs until point-in-time collection and
joins are implemented.

## Version 2 correction

### Hourly price-path target

Every anchor/horizon now stores the observed close price and return at the exact
horizon boundary. Model A trains Q05/Q50/Q95 quantile regressors for:

- future hourly close return;
- future maximum return;
- future minimum return.

The UI plots the eight hourly close medians and their Q05–Q95 band. Maximum and
minimum excursion remain explanatory values, not path points.

### Joint competing-risk target

Only the completed eight-hour first-touch row is required for Model B. Its
observed first-touch timestamp defines one of:

- `UP_H1 .. UP_H8`;
- `DOWN_H1 .. DOWN_H8`;
- `NO_EVENT`.

One calibrated classifier produces the complete distribution. Cumulative
hourly probabilities are sums of its event-time mass, which guarantees:

- `P(up first by h)` never decreases;
- `P(down first by h)` never decreases;
- `P(no touch by h)` never increases;
- the three probabilities sum to one at every hour.

### Decision governance

The model always exposes its probability forecast when the basic forecast model
is trainable. A separate policy:

1. searches candidate event-probability and direction-confidence thresholds
   jointly on the chronological calibration partition;
2. requires minimum total and per-direction support;
3. requires at least 85% empirical directional precision;
4. applies the selected threshold unchanged to the untouched test partition.

The live research decision can be LONG/SHORT only if that policy passes and the
current probabilities meet its thresholds. Live order execution remains absent.

## Migration behavior

Target definition version
`xrp-2pct-competing-risk-and-hourly-price-path-8h-v3` invalidates old anchors,
models, target tables, reports, and prediction ledgers. Raw Binance price
partitions are preserved and reused for the deterministic rebuild.

## Remaining work

Version 2 corrects the target and probability architecture. It does not claim
that the final trading edge has been proven. The following still require real
data and benchmark evidence:

- BTCUSDT and ETHUSDT point-in-time historical context;
- funding, open interest, mark/index basis, taker-flow, and liquidation history;
- restart-safe sequential order-book collection and reconstruction;
- regime-aware challenger comparison and rollback;
- net-of-fees/slippage paper trading;
- a clean five-year benchmark report produced on the user's stored dataset.
