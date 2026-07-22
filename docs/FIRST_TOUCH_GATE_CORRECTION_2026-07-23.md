# First-Touch Gate Correction — 2026-07-23

## Trigger

The first live Model B predictions assigned nearly all probability to `NO_EVENT` while the runtime reported the model as available. This exposed a methodological defect in the original promotion gate.

## Defect

The previous gate measured accuracy among all predictions whose maximum class probability was at least 85%. Because ±10% moves inside 15–60 minutes are rare, high-confidence `NO_EVENT` predictions could dominate that sample and allow the model to pass without demonstrating useful precision for either directional event.

That behavior contradicted the project rule that overall accuracy and `NO_EVENT` dominance cannot qualify the ±10% First-Touch Model.

## Correction

Model B now uses gate methodology:

`first-touch-directional-event-gate-v2`

A champion is accepted only when untouched temporal test data contains sufficient support for both `UP_10` and `DOWN_10`, and the model produces enough high-confidence predictions for each direction and in total, with at least 85% empirical precision across those directional predictions.

`NO_EVENT` accuracy remains visible as a diagnostic, but it has zero authority to pass the directional gate.

## Legacy model handling

Any persisted first-touch bundle without the new gate methodology version is invalidated on startup and is not loaded as a champion. Historical data, anchors, features, Model A artifacts, and prediction ledgers are preserved. Model B is retrained against the stricter gate and may correctly remain `WAIT` when directional evidence is insufficient.

## Production reporting

Production reports now separate:

- overall accuracy, marked diagnostic-only;
- all-class high-confidence accuracy, marked diagnostic-only;
- high-confidence directional prediction count;
- high-confidence directional precision;
- directional results for `UP_10` and `DOWN_10` separately.

Trading readiness remains `WAIT` regardless of research-monitoring status.

## Interpretation

A probability near 100% for `NO_EVENT` may be statistically plausible for a rare ±10% target, but it is not evidence that the platform can identify impending +10% or -10% shocks. The corrected gate prevents that distinction from being hidden.