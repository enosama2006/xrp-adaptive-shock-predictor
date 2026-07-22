# Prediction cadence decision

## Frozen operating policy

- Raw trades and book updates are ingested at source frequency.
- Online features refresh every 5 seconds when source health is valid.
- One immutable official prediction snapshot is written every 60 seconds.
- Every snapshot creates four rolling outcomes: 15, 30, 45, and 60 minutes.
- Windows are anchored to each minute, not to the top of the hour or quarter-hour.
- UI estimates may refresh between official snapshots, but they are observational and are not added to training or evaluation ledgers.
- Training labels mature independently at each horizon end.

## Example

An official snapshot at 10:07 creates outcomes ending at:

- 10:22 for 15 minutes
- 10:37 for 30 minutes
- 10:52 for 45 minutes
- 11:07 for 60 minutes

The next official snapshot at 10:08 creates a different set of rolling windows.

## Governance

Overlapping windows are allowed operationally but must not be treated as independent evidence. Evaluation uses purging, embargo, and event-cluster reporting. Missing or stale inputs force `WAIT`; no stale snapshot may be silently repeated as a new prediction.
