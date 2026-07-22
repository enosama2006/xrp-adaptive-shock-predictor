# Data Contracts

## Universal event envelope

Every raw event must include:

- `source`
- `market`
- `symbol`
- `event_type`
- `exchange_event_time_ms`
- `received_time_ms`
- `sequence_id` when provided
- `ingestion_version`
- `raw_payload_hash`

## Spot trade

Required fields:

- trade id
- price
- quantity
- buyer-maker flag or aggressor side
- exchange event time

Validation:

- price and quantity > 0;
- trade id unique per symbol;
- event time cannot exceed receive time by more than configured clock tolerance;
- duplicates preserved in quarantine log but excluded from clean data.

## Top-of-book

Required fields:

- best bid price and quantity
- best ask price and quantity
- exchange event time

Validation:

- bid > 0 and ask > 0;
- ask >= bid;
- quantities >= 0;
- stale state flagged after configured age.

## Multi-level order book

Required fields:

- snapshot sequence id;
- delta first and last sequence ids;
- bid and ask price levels;
- quantities;
- exchange event time.

Validation:

- snapshot applied before deltas;
- no unhandled sequence gap;
- zero quantity removes a level;
- crossed books are quarantined;
- reconstruction checksum and depth summary emitted periodically.

## Derivatives observations

Supported observations include funding, open interest, mark price, index price, basis, taker flow, and liquidations.

Each observation must declare:

- semantic definition from the source;
- point-in-time versus interval meaning;
- publication delay;
- update cadence;
- unit and currency;
- missing-value behavior.

## Clean minute bar

Each minute bucket must include:

- open, high, low, close;
- base and quote volume;
- trade count;
- aggressive-buy and aggressive-sell volume;
- spread statistics;
- data coverage and quality flags.

A minute is not considered valid merely because OHLC exists. Required source coverage must meet the configured completeness threshold.

## Prediction anchor

A prediction anchor must include:

- immutable forecast id;
- anchor timestamp and reference price;
- horizon;
- upper and lower barriers;
- feature schema version;
- model version;
- probability vector;
- data watermark;
- governance state;
- issue time and source clock state.

## Matured outcome

A matured outcome must include:

- forecast id;
- first-touch label;
- exact touch timestamp when known;
- horizon end;
- maximum favorable and adverse excursion;
- path completeness;
- ambiguity reason;
- eligibility for training and evaluation;
- exclusion reason when ineligible.

## Storage rules

- Raw data is append-only.
- Clean and feature datasets are versioned derivatives.
- No record is overwritten to improve a result.
- Corrections create a new version with lineage to the superseded artifact.
- Manifests include row counts, time bounds, schema version, and SHA-256 hashes.
