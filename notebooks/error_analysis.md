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

### Trade-Gap Replay Policy

The V1 replay engine detected aggTrade gaps but did not act on them. New
research defaults to `trade_gap_policy="pause_until_snapshot"`: cancel orders,
pause unreliable events, and resume only after the next valid depth snapshot.

Current status:

- `trade_gaps_detected` is reported separately while aggregate gap counters are
  preserved for compatibility.
- The six V1 anchors were replayed under both `ignore` and
  `pause_until_snapshot`.
- `results/replay_correctness/trade_gap_anchor6_delta.json` reports zero trade
  gaps and exact zero old/new deltas in every anchor.
- V1 was not a trade-gap artifact.

## Open Model Risks

### Queue Position From L2 Data

The simulator estimates queue position from aggregate L2 quantity. The V1
baseline granted proportional queue improvement when displayed quantity fell
without matching trade volume. This is a reasonable approximation, but it is
load-bearing because the 5-second requote baseline benefits from orders resting
long enough to move forward in queue.

Completed V1 sensitivity:

- Compared baseline results under `queue_cancellation_mode=proportional` and
  `queue_cancellation_mode=none`.
- Full-strategy PnL stayed negative under both modes, while matched-lot PnL
  changed sign. The matched-lot conclusion is queue-model conditional.

Required V2 follow-up:

- Remeasure the development panel at queue-credit endpoints `{0.0, 1.0}`.
- Run Phase C queue credits `{0.25, 0.5, 0.75}` at `10ms` and endpoint
  latencies `{0, 10, 50}ms`.

### Same-Millisecond Depth/Trade Attribution

Depth events are processed before trades at the same millisecond. This is a
documented design decision: the depth diff is treated as the book state after
the matching-engine action that produced the trade.

Completed V1 audit:

- The six-anchor audit found `6,762` same-ms overlap timestamps and `5` overlap
  fills, `0.54%` of all fills.
- It found `1` attribution-risk candidate and `0` artifact-evidenced
  wrong-attribution cases.
- Neither escalation threshold was met.

Required V2 follow-up:

- Compute data-level overlap timestamps once and recompute only endpoint-specific
  fill joins.

### Holdout Regime Shift

The frozen late-May holdout has lower realized volatility and fewer jumps than
the development panel. It is labeled `regime-shifted`, not
`regime-comparable`.

Required interpretation:

- Report the regime label alongside the holdout verdict.
- Treat a `pass` as encouraging but potentially helped by easier conditions.
- Treat a `fail` or `mixed` result as consistent with an untested mechanism,
  not automatically a broken one.
- Do not over-update in either direction.

## Interpretation Guardrails

- Do not treat total net PnL alone as edge when residual inventory PnL is large.
- Do not use matched-lot bootstrap as the headline inference unit; lots inside
  the same hour are correlated.
- Do not treat microprice beta per 1 bp as economically meaningful unless
  observed deviations are actually near that size.
- Do not run candidate holdout replays before the holdout protocol is filled,
  locked, and committed.
- Do not build `InventorySkewMM`, `VolAdaptiveMM`, or C++ before the V2
  evidence-expansion path is complete.
