# Error Analysis

This note lists the project issues that can change conclusions or undermine
reviewer trust. It is intentionally stricter than a research log: resolved
items remain here because they explain why current results supersede earlier
ones.

## Resolved Issues

### Accidental Taker Fills

Early market-making runs allowed intended passive limits to become taker fills
after latency moved the book through the quote. Those fills made the first
profitable-looking results unusable as passive market-making evidence.

Current status:

- `SimConfig.post_only=True` is the default.
- Crossing limits are cancelled with reason `post_only_would_cross`.
- Current results assume post-only enforcement. Earlier pre-post-only results
  contained accidental taker fills and are superseded.

### Snapshot Timestamp And Stale Diff Replay

Recorder snapshot timestamps were naive UTC values. Treating them as local time
created timestamp inversions around hourly snapshots. Applying stale diffs after
a snapshot could also roll the reconstructed book backward.

Current status:

- Snapshot `recv_time` is interpreted as UTC.
- Stale diffs with `u <= lastUpdateId` are dropped after a snapshot.
- The first retained diff must bridge `lastUpdateId + 1`.
- The corrected six-window baseline still does not show a stable positive edge.

## Open Model Risks

### Queue Position From L2 Data

The simulator estimates queue position from aggregate L2 quantity. The current
baseline grants proportional queue improvement when displayed quantity falls
without matching trade volume. This is a reasonable approximation, but it is
load-bearing because the 5-second requote baseline benefits from orders resting
long enough to move forward in queue.

Required follow-up:

- Compare baseline results under `queue_cancellation_mode=proportional` and
  `queue_cancellation_mode=none`.
- State in the writeup whether the negative conclusion is stable across both
  queue modes.

### Same-Millisecond Depth/Trade Attribution

Depth events are processed before trades at the same millisecond. This is a
documented design decision: the depth diff is treated as the book state after
the matching-engine action that produced the trade.

Required follow-up:

- Quantify same-millisecond overlaps across the six anchor windows.
- Escalate only if overlap fills exceed 5% of total fills, or if wrong
  attribution is evidenced in more than 1% of overlapping cases.

## Interpretation Guardrails

- Do not treat total net PnL alone as edge when residual inventory PnL is large.
- Do not use matched-lot bootstrap as the headline inference unit; lots inside
  the same hour are correlated.
- Do not treat microprice beta per 1 bp as economically meaningful unless
  observed deviations are actually near that size.
- Do not build `InventorySkewMM`, `VolAdaptiveMM`, or C++ until fee break-even
  and queue sensitivity are documented.
