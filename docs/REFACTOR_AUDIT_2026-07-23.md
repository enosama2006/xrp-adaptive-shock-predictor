# XASP Refactor Audit — 2026-07-23

## Scope

This audit reviewed the current repository against the agreed dual-model scientific contract:

- Model A: XRP Adaptive Shock Predictor / future-excursion magnitude model.
- Model B: ±10% First-Touch Predictor.
- At least one year of real minute history, then continuous minute enrichment.
- Causal feature engineering and training-only scaling.
- Near-price order-book supply/demand features that cannot be dominated by far-away walls.
- Independent model states, artifacts, reports, predictions, and production evaluation.

This report distinguishes **code presence** from **validated evidence**.

---

## Executive verdict

The repository is beyond a static prototype, but it is not yet a scientifically complete production research system.

Strengths:

- real Binance minute kline ingestion;
- restart-safe missing-tail collection;
- causal price/volume transformations;
- independent Model A and Model B artifacts and APIs;
- delayed first-touch prediction ledger;
- dual-model dashboard;
- fail-closed startup tests.

Critical weaknesses:

- Model B historical labels and production maturation are close-only;
- real Binance minute timestamps may be misaligned by one millisecond;
- purge/embargo code is not used by the real trainers;
- feature selection is implicit;
- first bootstrap is opaque and materializes a full year before final persistence;
- order-book logic is not connected to live collection or training;
- total/far-book context is not protected by an explicit model-feature registry;
- real one-year benchmark and model evidence are absent.

---

## Findings

### A-01 — Product identity was inconsistent

**Severity:** Critical documentation/product risk.

The repository historically described only first-touch ±10% as the project objective, while the current implementation also contains an independent future-excursion model. This caused the dashboard and discussions to confuse one model with the other.

**Action:** README, master plan, status, TODO, APIs, and dashboard must use one frozen naming contract.

**Status:** documentation corrected; runtime isolation still requires deeper work.

---

### A-02 — Model B misses intraminute touches

**Severity:** P0 correctness.

The anchor builder and prediction ledger convert each minute to a close-only `PricePoint`. A candle may trade above +10% or below -10% and close back inside the barrier. Such a real touch would be missed.

**Required correction:**

- use candle `high` for upper-barrier detection;
- use candle `low` for lower-barrier detection;
- mark a candle touching both as `AMBIGUOUS` unless finer data proves sequence;
- use OHLC-aware logic both for historical target building and live prediction maturation.

---

### A-03 — Internal data gaps can be mistaken for complete paths

**Severity:** P0 correctness.

A path can reach the final horizon timestamp while missing intermediate minute candles. The current close-point labeler only checks whether the last point reaches the horizon.

**Required correction:** require the full expected minute sequence or record `INCOMPLETE`/`EXCLUDED`.

---

### A-04 — Binance candle timestamp semantics are unsafe

**Severity:** P0 correctness.

Binance one-minute kline `closeTime` normally ends in `...59,999`, while anchor creation uses exact minute boundaries. The ingestion path stores `event_time_ms=closeTime`, which can misalign feature/anchor joins and horizon completeness.

**Required correction:**

- normalize a completed candle to `closeTime + 1` or equivalent exact boundary;
- record raw open/close timestamps separately if needed;
- migrate existing local 59,999-ms rows idempotently;
- test joins and maturation on real Binance timestamp shapes.

---

### A-05 — Purge and embargo are not used by training

**Severity:** P0 validation.

A `build_purged_split` helper exists, but Model A and Model B trainers still use simple chronological fractions. Adjacent minute anchors have overlapping future label windows, so contamination remains possible.

**Required correction:** actual train/calibration/validation/test rows must be purged around boundaries and embargoed after evaluation windows. Reports must state purged row counts and boundaries.

---

### A-06 — Feature selection is implicit and unsafe

**Severity:** P0 governance.

The runtime selects nearly every numeric column except timestamp, price, and feature-availability time. When new data families are joined, raw price-like fields, context-only order-book totals, diagnostics, or accidental numeric columns can silently enter training.

**Required correction:** explicit versioned feature registry. Unknown numeric columns are excluded by default and reported.

---

### A-07 — Historical Binance trade-flow fields are discarded

**Severity:** P1 data value.

Binance klines provide quote volume, trade count, taker-buy base volume, and taker-buy quote volume. The current price table retains only OHLC and base volume.

**Required correction:** preserve these real fields and derive causal taker-buy ratio, signed-volume proxy, trade intensity, average trade size, and robust normalized variants.

---

### A-08 — Order-book module is scientifically promising but not integrated

**Severity:** P1 implementation.

The repository has near-price bands and distance-weighted depth, but no restart-safe depth collector, sequence validation, availability join, or training integration.

The module also emits raw total bid/ask quantities. Those values must be context-only or removed from model eligibility because huge far-away orders can distort learning.

**Required correction:**

- explicit model/context separation;
- best-level imbalance and microprice;
- live sequential depth updates;
- OFI, cancellation, replenishment, depletion, and persistence;
- far-wall non-influence tests;
- availability masks for periods without order-book history.

---

### A-09 — Historical order-book backfill must not be invented

**Severity:** P0 data integrity.

Standard public Binance REST endpoints do not provide a one-year archive of reconstructed order-book snapshots. The platform must not fill historical rows with synthetic or inferred books.

**Policy:** train the initial one-year baseline on real historical sources that exist; begin live microstructure collection from deployment time; add that feature family only to models/folds where availability is real and explicitly masked.

---

### A-10 — First bootstrap is opaque and memory-heavy

**Severity:** P1 operations.

The pipeline converts the entire iterator to a list before merging and writing. A one-year minute backfill is manageable but unnecessarily opaque and fragile.

**Required correction:** page/chunk checkpointing, progress state, retry policy, and resumable manifests. The UI must show `BOOTSTRAP_HISTORY` rather than generic `WAIT`.

---

### A-11 — Independent model readiness is incomplete

**Severity:** P1 product/runtime.

APIs are separated, but some overall readiness fields still require both models. Each model needs its own lifecycle, failure reason, report freshness, data eligibility, and last prediction time.

---

### A-12 — Model A is a baseline, not yet the complete adaptive shock system

**Severity:** P1 model scope.

Current Model A is horizon-specific future high/low quantile regression. It is a valid starting baseline, but it does not yet use multi-source trade flow, cross-market, derivatives, and microstructure features or regime-adaptive ensembles.

The UI and documentation must not imply more than is implemented.

---

### A-13 — Model B rare-event evidence may be insufficient

**Severity:** P1 statistical risk.

A ±10% move within 15–60 minutes may be rare. One year of minute anchors creates many overlapping rows but not necessarily many independent `UP_10` or `DOWN_10` events.

**Required correction:** report class support and independent event clusters. Never treat large overlapping row counts as independent evidence. Keep the gate closed when support is insufficient.

---

### A-14 — Production reporting needs model-specific maturity

**Severity:** P1 monitoring.

Model B has a matured ledger. Model A stores predictions, but its future high/low outcomes and production coverage/error need an equally explicit maturation process and independent report.

---

### A-15 — No reproducible real-data evidence bundle yet

**Severity:** P0 readiness.

The repository has tests and code but no committed benchmark evidence proving:

- successful 365-day real backfill;
- coverage/gap statistics;
- model training on that dataset;
- untouched-test metrics;
- runtime duration and memory;
- model-specific production report;
- successful CI workflow.

No trading-readiness statement is valid until this bundle exists.

---

## Refactor plan initiated

1. Documentation contract frozen.
2. Feature registry and fail-closed selection.
3. Order-book primary/context separation and far-wall tests.
4. Kline timestamp and richer trade-flow schema migration.
5. OHLC/gap-aware Model B labels and ledger maturation.
6. Purged training integration for both models.
7. Bootstrap lifecycle/progress and chunk checkpointing.
8. Independent model state/report/ledger APIs.
9. Live depth collection with availability masks.
10. Champion/challenger, drift, rollback, and real one-year evidence run.

## Official conclusion

The current repository is a serious research scaffold, not a validated production predictor. The correct engineering response is refactor-and-prove, not rename-and-assume. Official state remains `WAIT`.