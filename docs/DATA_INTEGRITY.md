# XASP Observed-Data Integrity Contract

Version: 1.4.0

## Scope

The integrity audit covers locally persisted, completed one-minute XRPUSDT candles. It reads UTC-month Parquet partitions when present and falls back to the preserved legacy `data/prices.parquet` file only when no monthly partitions exist.

## Evidence produced

For every file the audit records:

- SHA-256 content hash and file size;
- total and unique timestamp counts;
- expected and missing minute counts;
- timestamp duplication, order, and minute-boundary alignment;
- OHLC invariants and non-negative finite volume;
- observed coverage ratio and time range.

The dataset fingerprint is computed from the ordered partition hashes, row counts, and time ranges. Any observed content change produces a different fingerprint.

## Status semantics

- `PASS`: structural checks pass and observed coverage is at least 99.5%.
- `WARN`: structure is valid but observed coverage is below the configured threshold.
- `FAIL`: duplicate, unordered, misaligned, malformed OHLC, or invalid-volume rows exist.
- `WAIT`: no local observed price file exists yet.

## Fail-closed rules

Startup stops on `FAIL`. A coverage warning remains visible but does not fabricate candles or silently convert gaps to `NO_EVENT`. Gap-aware label builders remain responsible for marking affected horizons incomplete.

The report is written atomically to `reports/data_integrity.json` before the server starts. The audit never edits market data, repairs prices, fills gaps, or changes model readiness by itself.
