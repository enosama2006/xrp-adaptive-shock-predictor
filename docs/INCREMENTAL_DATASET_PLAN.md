# Incremental Dataset & Continuous Training Plan

## Current truth

The repository has not yet produced a measured maximum tested dataset size. No claim may be made about millions of rows, multi-day endurance, or browser memory limits until benchmark artifacts exist.

## Core design decision

The system must never rebuild the dataset from zero on every page open.

It uses two layers:

1. **Immutable raw event store**
   - append-only Parquet partitions
   - partitioned by source / symbol / event type / UTC date
   - deduplicated by deterministic event key
   - each partition has row count, min/max timestamps, checksum, and gap report

2. **Derived training dataset**
   - one row per prediction anchor timestamp
   - features computed only from data available at the anchor
   - labels finalized only after the 60-minute maximum horizon expires
   - incrementally extended from the last durable watermark

## User-proposed label design — accepted and refined

For every anchor price `p0` at time `t0`, persist:

- `reference_price`
- `max_price_15m`, `min_price_15m`
- `max_price_30m`, `min_price_30m`
- `max_price_45m`, `min_price_45m`
- `max_price_60m`, `min_price_60m`
- `upper_barrier = p0 * 1.10`
- `lower_barrier = p0 * 0.90`
- `upper_first_hit_ts`
- `lower_first_hit_ts`
- `first_touch_side`
- `first_touch_ts`
- `time_to_touch_seconds`
- `mfe`
- `mae`
- `path_complete`
- `label_status = PENDING | FINAL | AMBIGUOUS | INCOMPLETE`

The maximum and minimum prices are useful diagnostics, but the supervised target is **which 10% barrier was touched first**, not merely which extreme was larger.

## Incremental lifecycle

### On first run

1. Read the dataset state file.
2. If no state exists, start historical backfill from the configured start date.
3. Download in bounded chunks.
4. Commit each completed partition immediately.
5. Update the raw watermark after every verified partition.
6. Build anchor rows up to `now - 60 minutes`.
7. Finalize labels only where the full future path exists.
8. Train a baseline model from finalized rows only.

### On every later run

1. Load the last verified raw watermark.
2. Backfill only the missing interval from the watermark to now.
3. Merge live events using deterministic deduplication.
4. Finalize previously pending labels whose 60-minute window has now completed.
5. Generate new anchor rows after the last feature watermark.
6. Update evaluation metrics.
7. Train a challenger on a schedule or when enough new finalized events accumulate.
8. Keep the existing champion active until the challenger passes promotion gates.

## Browser behavior

Opening the site should:

- display the last persisted model and dataset state immediately;
- start a background synchronization job;
- show progress and data freshness;
- never block the interface while rebuilding history;
- never erase existing data on reconnect;
- never train directly in the browser on an unbounded dataset.

Large-scale historical ingestion and model training belong in a Python service or scheduled job. The browser consumes persisted model artifacts and records live observations.

## Watermarks

Persist separate watermarks for:

- raw spot trades
- raw spot klines
- raw book ticker
- raw order-book deltas
- funding
- open interest
- liquidations
- feature rows
- finalized labels
- last training cutoff

A watermark advances only after the partition passes schema, ordering, deduplication, and checksum checks.

## Storage policy

- Raw data: append-only and immutable.
- Corrected data: new version, never silent overwrite.
- Derived features: versioned by `feature_schema_version`.
- Labels: versioned by `label_schema_version`.
- Models: versioned by dataset hash, feature version, config hash, and commit SHA.

## Training policy

The model does not retrain after every tick.

- Probabilities and live features update continuously.
- Labels mature after the maximum 60-minute horizon.
- Lightweight calibration may update after a minimum batch of new finalized outcomes.
- Full challenger retraining occurs after a configurable count of new finalized independent event clusters or on a scheduled cadence.
- Promotion requires walk-forward validation and no degradation in calibration, precision, and risk metrics.

## Minimum viable dataset

The system may start collecting and displaying data immediately, but must remain `WAIT`.

Initial research thresholds:

- baseline training: at least 100,000 anchor rows and sufficient class representation;
- any directional research claim: at least 100 finalized `UP_10` and 100 finalized `DOWN_10` events across multiple periods;
- user-facing trade signal consideration: at least 500 evaluated independent event clusters plus passed paper-trading gates.

These are governance thresholds, not guarantees of statistical adequacy. The final sample requirement must be derived from observed event prevalence and confidence intervals.

## Benchmark plan

Before claiming scale, run and publish benchmarks at:

- 100,000 rows
- 1,000,000 rows
- 10,000,000 rows
- 24-hour live recording
- 7-day live recording

Measure:

- ingestion rows/second
- memory peak
- disk size
- deduplication rate
- gap rate
- label throughput
- feature throughput
- restart/resume duration
- checksum reproducibility

## Completion criteria

This plan is complete only when:

- restart resumes from the exact last verified watermark;
- a forced interruption during backfill does not corrupt completed partitions;
- rerunning the same interval creates zero duplicate rows;
- pending labels become final without rebuilding old anchors;
- the same raw partitions produce byte-identical labels;
- the page opens immediately using persisted state while sync continues;
- benchmark artifacts document the largest tested scale;
- all failure modes degrade to `WAIT`.
