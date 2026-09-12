# Event-Driven Execution Model V2

Status: Python reference implementation and deterministic acceptance complete
locally; the full development-panel V3 rerun completed on 2026-09-11. Its locked
primary OFI screen is blocked at both queue endpoints. This model supersedes the
execution timing and own-order queue semantics used by the frozen Phase 2 V2
research artifacts. Those artifacts remain historical evidence and must not be
silently overwritten.

Identifiers:

- Execution model: `event_driven_v2`
- Equal-timestamp policy: `market_data_before_private_actions_v1`
- Snapshot-time policy: `post_response_proxy_depth_boundary_v1`
- Historical artifact model: `legacy_book_update_v1`

## Why The Model Changed

The historical simulator activated an order only when a depth update arrived,
even if its modeled arrival time preceded an intervening trade. Cancellations
were effective immediately. This quantized entry latency to depth updates and
omitted cancel/fill races. A stale per-price queue cache could also grant a new
order cancellation credit for a depth decrease that happened before that order
arrived. Delayed cancels expose overlapping own quotes, for which the historical
simulator could reuse one recorded trade's full quantity for every own order at
the price.

The corrected Python research reference is frozen at commit `04df629`. The
implemented C++17 port reproduces the order-book contract; replay, execution,
and accounting continue to use Python.

The port is integrated as the selectable `cpp` book backend. Full-stream
comparisons pass across all 120 selected development hours at both queue
endpoints; [native design and measurements](../cpp/README.md) describe the
binding and [complete report](../report/l2_mm_system_complete_report.pdf) covers
the finished project. This integration does not replace `04df629` as the source
of the frozen V3 economics.

A later publication audit found a separate recorder issue: historical REST
snapshots were tagged at request start, before the blocking response completed.
The returned book could therefore enter replay before it was observable. New
captures persist request and response times separately. Current replay emits a
fail-closed barrier at snapshot request, establishes a valid bridge, and then
requires a sequence-valid retained depth receipt after response completion.
Legacy files use the first strictly later local receipt as a post-response
upper-bound proxy. Snapshot and buffered diffs reconstruct state at the selected
record's `E`, but only the final state reaches strategy logic. An unreleased or
unbridged snapshot never resumes replay.

## Clock Contract

The replay clock has two event classes:

1. Recorded market data: a snapshot resync barrier uses recorded local request
   time; recovery is gated by local receipt order and released on depth event
   time `E`; ordinary depth diffs use `E` and trades use matching time `T`.
   Depth precedes trade on equal milliseconds, preserving source order within
   each stream.
2. Simulated private arrivals: new-order and cancellation messages, preserving
   their stable insertion order.

At an identical millisecond, every recorded market-data event is processed
before private arrivals. Therefore:

- A new order arriving before a trade is eligible for that trade.
- A new order arriving at the same millisecond as a trade is not eligible.
- A cancellation arriving before a trade prevents the fill.
- A cancellation arriving at the same millisecond as a trade loses the race.
- A zero-latency action produced by a market-data callback cannot act on the
  event that caused it.

This is a conservative rule for unresolved millisecond ties. It is a modeling
policy, not a claim about Binance matching-engine sequence.

Each input stream is required to be monotone in its modeled ordering field;
replay fails rather than asking a sorted merge to conceal an inversion. Depth
cancellation attribution is deferred until all same-millisecond trades
have been observed. A displayed decrease explained by tied trade volume cannot
also receive cancellation-driven queue credit.

## Entry And Cancellation Lifecycle

`submit()` creates a `PENDING` order and schedules its exact modeled arrival.
The `arrived` and `queued` events use that scheduled timestamp, not the next
depth timestamp.

`cancel()` accepts a cancellation request against a pending or active order and
schedules the cancellation's modeled arrival. A pending order remains
nonfillable until its new-order arrival. Once active, it remains fillable while
the cancellation is in flight.
Lifecycle events distinguish:

- `cancel_requested`: client intent and scheduled arrival
- `cancelled`: cancellation became effective
- `cancel_too_late`: the order filled or otherwise terminated first

Entry and cancellation jitter use independent seeded PRNG streams. Cancellation
latency and jitter default to the entry values but are separately configurable.
Jitter may not exceed its base latency, preventing negative message delay.

The strategy exposure envelope counts the remaining quantity of every working
same-side order. Pending entries may become fillable; active orders remain
fillable while cancels are in flight. A replacement is submitted only if the
worst case in which all working quantity fills remains within `max_position`.
Any invariant breach aborts the replay rather than silently reporting an
over-limit result.

## Own-Order FIFO And Volume Conservation

For each resting order, total `queue_ahead` is decomposed into:

- externally displayed quantity ahead
- remaining quantity from earlier simulated orders at the same side and price

Public cancellation credit applies only to the external component. Cancelling
an earlier simulated order releases its remaining quantity from later own-order
queues. A recorded trade is applied against these non-overlapping FIFO
positions, and the simulator asserts that aggregate maker fills cannot exceed
the recorded trade quantity.

The complete post-trade FIFO transition is planned and validated before order
or fill state is mutated. Aggregate fills are structurally capped by the
recorded quantity. FIFO comparisons allow only a scale-aware `1e-24` relative
tolerance for `Decimal` division round-off; the tolerance does not create
additional fill budget.

## Gaps And Replay End

A data gap immediately invalidates every local open order and pending private
action. This is fail-closed research state invalidation, not a claim of
zero-latency exchange cancellation.

Under the strict gap policy, a gap marker taints its entire millisecond group.
The engine invalidates before applying any depth/trade event or strategy
callback in that group, so a known event earlier in source order at the same
millisecond cannot create a fill from an incomplete group. This deliberately
chooses timestamp-group censoring over prefix preservation.

Scheduled private events after the last recorded market-data timestamp are not
processed. Executing them would require assuming an unobserved future book.
Every still-open order is explicitly terminalized as `EXPIRED`, and pending
private/cancel action counts are reported separately. The
`pending_actions_at_end` and `pending_cancels_at_end` statistics count scheduled
actions immediately before terminalization; they can be nonzero even though
the corresponding orders are then expired.

## Known Limits

- Binance L2 data exposes aggregate price levels, not market-by-order queue
  events. External queue position and cancellation placement remain modeled.
- Exchange and feed timestamps have insufficient information to resolve true
  within-millisecond ordering; the policy above must be sensitivity-tested if
  tied events prove material.
- The modeled market-data clock combines recorded local request time for resync
  barriers, depth event time `E` for release and ordinary diffs, and trade
  matching time `T`. Local response/receipt order gates recovery. These fields
  have different semantics and feed latency is uncalibrated. The gate removes
  request-start strategy access, but release remains on exchange `E`; this is
  not a calibrated client-observation or exchange-native snapshot timestamp.
- Simulated taker orders use observed book liquidity counterfactually. A shared
  shadow ledger prevents multiple own takers from consuming the same displayed
  quantity between depth updates, but it is reset on the next observed depth
  event; taker-heavy strategies still require stronger market-impact and book
  reconciliation assumptions before they are credible.
- Market-data latency is not modeled separately from order-entry latency.

## Artifact Provenance

The committed `results/panels/btcusdt_l2_panel_v2` execution-derived outputs
were generated under `legacy_book_update_v1`. The verifier continues to verify
those immutable historical artifacts. Raw-file integrity and selected
development/holdout identities are unaffected. The private-timing and
snapshot-time corrections required regenerated book-state signals, fills, PnL,
conditional studies, and queue diagnostics. The completed V3 study supplies
these at both queue endpoints with the locked 10 ms latency. The old broader
queue/latency stress grid remains historical and requires a separate rerun
before any current-model grid claim. All new execution-derived outputs belong
under `event_driven_v2` in a new result root.

Standalone current-engine scripts default beneath `results/event_driven_v2`.
The development-panel runner uses
`results/panels/btcusdt_l2_panel_v3_event_driven`. Current writers resolve
symlinks and refuse any target beneath the frozen V2 panel; current cache and
aggregation paths require explicit, compatible execution provenance.

The strategy-sealed holdout remains unavailable for model development. Only
frozen development windows may be used while validating and rerunning the new
execution model.

The V3 runner accepts only the frozen `development_windows.csv` content
(SHA-256
`c779138fdffb739715c53cfa27c75b3c8ca140bc4f5a6128f60f2251948bd362`).
Before a completed step can be resumed, it verifies the frozen integrity
manifest file and identity plus the SHA-256 of every selected depth and trade
file. Resume markers bind the command, source tree, inputs, complete expected
output list, and output contents.

The runner supplies an exact one-to-one reconciliation run/start allowlist to
the bootstrap step. Allowlisted artifacts must be regular, non-symlinked files
contained directly in their named run directory. This prevents compatible
stale or redirected runs from silently entering a confidence interval. Each
endpoint must carry complete `event_driven_v2` provenance, match the requested
queue credit and canonical five-hour sizing experiment, and cover the same run
set as its frozen legacy reference. The production comparator also pins the
two frozen legacy endpoint file hashes rather than inferring "legacy" from
missing metadata alone. The resulting comparison records source and panel
hashes, reports each current CI's relation to zero, and reports
descriptive mean deltas only. It does not reuse the historical V2/V1
advancement ladder, and a cross-model mean delta is not presented as a paired
confidence interval.

A completed full-panel run writes `ARTIFACT_MANIFEST.json`, which embeds the
Python source-tree fingerprint, git base commit, canonical experiment and
panel identity, every selected raw-file hash, completed-step derivations, and
every durable non-status artifact hash. Ephemeral caches are excluded and V3
CSV artifacts are explicitly trackable despite the repository-wide raw-CSV
ignore rule. Unlike resumability markers, this manifest is not ignored by Git.
`scripts/verify_v3_artifacts.py` checks source identity, raw inputs, the frozen
panel, all required derivation steps, and the complete durable output tree.

The completed run records 61 steps and 330 durable artifacts against research
commit `04df6294872a256374f910d12d5e5e3e08e8757a`, with source fingerprint
`45141ba610bb070217e6d08bbb5fef9ca1a725f5c7d5e40b6e14881450a4cb4d`.
Later benchmark and verification-tool changes are outside that frozen research
reference. Verify it explicitly from its recorded Git objects using:

```bash
env PYTHONPATH=. python scripts/verify_v3_artifacts.py --source-revision recorded
env PYTHONPATH=. python scripts/summarize_v3_development.py --source-revision recorded
```

Recorded mode requires the exact manifest commit and reproduces its original
Python source fingerprint. Raw files and durable artifacts remain subject to
the same checks. Default verification without `--source-revision` still requires
the current working source to match. The decision summary records the verified
revision and is written outside the run tree, preserving the run manifest.

## Minimum Acceptance Cases

The deterministic suite must cover:

- arrival before a trade without an intervening depth update
- exact-time arrival/trade and cancel/trade races
- cancel-before-fill and fill-before-cancel outcomes
- price re-entry without stale cancellation credit
- overlapping same-price own orders with conserved trade volume
- own-order cancellation releasing later FIFO queue
- same-millisecond depth/trade attribution
- data-gap invalidation of pending and active orders
- repeated-process deterministic fills and lifecycle events
- snapshot requests pause replay; recovery requires a post-response proxy
  boundary, hides intermediate reconstruction states, withholds unbridged
  snapshots, and rejects nonmonotone input streams
- delayed cancel/replace overlap cannot breach the hard working-exposure limit
- exact development-window/run allowlisting for V3 bootstrap inputs
- rejection of legacy, mixed, incomplete, or credit-mismatched provenance in
  V3-derived comparisons
- descriptive-only cross-model comparison with source and panel hashes

Local acceptance checkpoint (2026-07-14):

- `369 passed in 5.56s` for the deterministic suite, excluding the live
  recorder/network test
- `126 passed in 2.26s` for the execution simulator, replay engine, event
  merger, and market-making strategy subset
- all frozen V2 artifact checks passed, including the strategy-sealed holdout check
- a real one-hour BTCUSDT replay completed with exact private scheduling,
  delayed cancellations, eight maker fills, zero taker fills, two explicit
  replay-end expirations, and no pending orders at termination

These historical checks accepted the Python implementation boundary before the
V3 study. The completed [V3 writeup](research_writeup_v3.md) and
[decision summary](../results/panels/btcusdt_l2_panel_v3_development_summary/summary.json)
now report the corrected development evidence. Both endpoint conditional OFI
screens failed the pre-specified `1.0 bps` threshold; the strategy-sealed holdout
remains unused for candidate evaluation.
