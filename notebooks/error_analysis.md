# Error Analysis

This note lists the project issues that can change conclusions or undermine
reviewer trust. It is intentionally stricter than a research log: resolved
items remain here because they explain why current results supersede earlier
ones.

The committed V2 result set is historical: it was generated at research commit
`4650f4c` under `legacy_book_update_v1`. The Phase 2.5 `event_driven_v2`
Python implementation has passed local acceptance; see
[`notebooks/execution_model_v2.md`](execution_model_v2.md). Until the frozen
development panel is rerun under the V3 event-driven namespace, the newer model
has no validated execution-derived research result.

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

### Private-Action Timing And Own-Order FIFO

Frozen V2 activated a modeled order arrival on the next depth update and made a
cancel request effective immediately. That timing omitted intervening-trade
eligibility and cancel/fill races. It also did not fully conserve one recorded
trade across overlapping own orders at the same price.

Current status:

- The frozen artifacts remain under
  `results/panels/btcusdt_l2_panel_v2` with provenance marker
  `EXECUTION_MODEL.json`; they are not silently regenerated.
- `event_driven_v2` schedules exact order and cancel arrivals, uses an explicit
  market-data-before-private equal-time rule, and models own-order FIFO with
  trade-volume conservation.
- Local implementation and deterministic acceptance testing pass. Fill sets,
  PnL, conditional-on-fill analyses, queue diagnostics, and latency conclusions
  still require the V3 development rerun before they are treated as current
  evidence.
- The holdout remains sealed.

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

Completed V2 stress:

- The 24-window development panel was remeasured at queue-credit endpoints
  `{0.0, 1.0}` in Phase A.
- Phase C ran queue credits `{0.0, 0.25, 0.5, 0.75, 1.0}` at `10ms`, plus
  endpoint latencies `{0, 10, 50}ms`.
- Matched net per BTC worsened monotonically from about `-9.75` at credit
  `0.0` to about `-23.65` at credit `1.0`.
- Matched and full-strategy PnL were invariant to latency in `[0, 50]ms` at the
  tested spread.
- The V1 matched-lot positive under no cancellation credit did not generalize
  to the 24-window panel.

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

Completed V2 panel audit:

- The 24-window data-level overlap cache is shared across endpoint audits.
- Proportional queue credit: `10` same-ms overlap fills out of `2041` fills
  (`0.49%`) and `0` artifact-evidenced wrong-attribution cases.
- No queue credit: `7` same-ms overlap fills out of `1595` fills (`0.44%`) and
  `0` artifact-evidenced wrong-attribution cases.
- Both endpoints remain below the predefined escalation thresholds.

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
- Do not build `InventorySkewMM` or `VolAdaptiveMM` without a new
  pre-registered mechanism. The V2 evidence-expansion path is complete.
- The C++ performance port is intentionally paused until the project owner has
  built a fundamental understanding of modern C++. When it resumes, parity
  against the frozen Python reference must precede benchmark claims.
