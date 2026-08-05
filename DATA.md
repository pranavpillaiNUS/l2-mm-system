# Data and reproducibility

This repository studies Binance spot BTCUSDT with locally recorded depth and
aggregate-trade streams. Raw captures are not committed. At the 4 August 2026
report snapshot, the complete local multi-dataset capture is approximately 37
GB, including recording-only perpetual-futures data. The two spot directories
used by the research occupy approximately 20.5 GB; the 240 raw files selected
for the 24-window development panel occupy approximately 0.82 GB. The repository
instead publishes selected derived artifacts and integrity metadata.

The central distinction is:

- **artifact-verifiable:** a clone can validate committed structures,
  identities, verdicts, and presentation inputs;
- **fully replayable:** requires the original raw files listed and hashed in the
  integrity manifest.

The repository claims the first, not the second.

## Recorded datasets

```text
data/raw/btcusdt/               spot depth diffs + REST snapshots
data/raw/btcusdt_trades/        spot aggregate trades
data/raw/btcusdt_perp/          USD-M perpetual depth capture
data/raw/btcusdt_perp_trades/   USD-M perpetual raw trade capture
```

Only the two spot datasets are used in the completed research. Perpetual data is
recording-only and is isolated because its depth sequencing and trade
granularity differ.

Files are hourly gzip-compressed JSON Lines:

```text
btcusdt_depth_YYYYMMDD_HH00.jsonl.gz
btcusdt_trades_YYYYMMDD_HH00.jsonl.gz
```

Depth records contain either a WebSocket diff or a tagged REST snapshot:

```json
{"recv_time":"...","data":{"E":0,"U":1,"u":2,"b":[["price","qty"]],"a":[]}}
{"request_time":"...","recv_time":"...","type":"snapshot","data":{"lastUpdateId":2,"bids":[],"asks":[]}}
```

Trade records wrap Binance aggregate-trade payloads:

```json
{"recv_time":"...","data":{"a":1,"p":"price","q":"qty","T":0,"m":true}}
```

For spot data, depth diffs expose Binance event time `E` and trades expose
matching time `T`. REST snapshots have no exchange event timestamp. The legacy
recorder populated snapshot `recv_time` with the request-start/rotation time
*before* its blocking REST fetch completed. Frozen V2 replay applied the returned
book at that early local tag, so it contains a snapshot look-ahead exposure.

The current recorder writes `request_time` before the request and `recv_time`
after response completion. Under `post_response_proxy_depth_boundary_v1`, the
parser emits a fail-closed resync barrier at the recorded local request time,
then establishes a valid bridge and requires a sequence-valid retained depth
receipt at or after recorded response completion. Legacy files lack that
completion time, so their first strictly later local depth receipt is used as a
post-response upper-bound proxy; the recorder's blocking design makes that
ordering defensible. Snapshot and buffered reconstruction events are released
at the selected record's `E`, and only the final reconstructed state reaches
strategy logic or book sampling.

This is a post-response-gated **exchange-clock proxy**, not a calibrated client
observation clock. The modeled axis uses recorded local request time for resync
barriers, depth `E` for recovery release and ordinary diffs, and trade `T` for
trades. Those fields have different semantics and uncalibrated latency; local
time is never substituted for a trade's `T`.

## Reconstruction rules

The parser follows the snapshot/diff bridge contract:

1. load a snapshot and its `lastUpdateId`;
2. discard stale diffs at or before that ID;
3. require the first retained update to bridge the snapshot;
4. require continuous update IDs thereafter; and
5. fail closed on a known gap until a later valid snapshot.

Quantity zero removes a level. A positive quantity replaces aggregate displayed
quantity at that price. Aggregate-trade IDs are checked independently for gaps.
The current replay uses snapshot and buffered diffs only to reconstruct state,
then exposes the final state at the policy boundary. It orders depth before
trades for unresolved equal-millisecond market-data ties, then processes
equal-time simulated private arrivals. It rejects a depth or trade stream whose
modeled timestamps decrease. This is a deterministic model policy, not a
reconstruction of the venue's true observation or within-millisecond matching
sequence.

Official field and synchronization definitions are in the
[Binance spot WebSocket documentation](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
and [market-data REST documentation](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints).

## Frozen integrity inventory

The canonical inventory is
[`results/panels/btcusdt_l2_panel_v2/integrity_manifest.json`](results/panels/btcusdt_l2_panel_v2/integrity_manifest.json).
It covers hourly slots before `2026-06-01T00:00:00`:

| Inventory status | Hours |
|---|---:|
| Total | 1,193 |
| Valid | 775 |
| Invalid | 418 |

An hour is valid only if required files exist and pass gzip, JSON, snapshot
bridge, depth sequence, cross-hour sequence, and trade-ID checks. Each manifest
row records file paths, SHA-256 values, row counts, first/last IDs, errors, and
rejection reasons.

The manifest's logical identity is
`a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61`.
This is distinct from the SHA-256 of the JSON file itself.

Missingness is not assumed to be random. Recorder or feed failures may be more
likely during operationally difficult markets, so filtering to valid hours can
select the research sample.

The separate
[`snapshot timing audit`](results/replay_correctness/snapshot_timing_panel24.json)
hash-checks all 120 selected development depth files. Every snapshot had a valid
bridge. The first later local depth receipt followed the legacy pre-fetch tag by
a median 250.5 ms, 90th percentile 672.9 ms, and maximum 2,335 ms. This is an
upper-bound response-completion proxy, not a measured REST round trip. All 120
snapshots reached a sequence-valid policy boundary; on these files that boundary
was the first valid bridge. Two no-credit and four proportional-credit fills
came from orders placed before the selected exchange-clock boundary. Those are
proxy counts, not bounds on all pre-observation effects, and they cannot exclude
changes to later queue age. V3 must therefore rerun the full development panel
under the new policy.

## Development and holdout panels

The selected-panel identity is
`760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122`.

| Panel | Windows | Use |
|---|---:|---|
| Development | 24 × 5 hours | Baseline, premise tests, and model stress |
| Holdout | 12 × 5 hours | Reserved for one locked candidate strategy evaluation |

Six development windows were exploratory baseline anchors. The other 18 were
selected later from manifest-valid, non-overlapping candidates while balancing
UTC buckets and realized-volatility terciles. The panel is therefore expanded
development evidence, not a random market sample or untouched confirmation set.

The holdout is **strategy-sealed, not unseen**. Its identities and pre-strategy
regime descriptors were examined. Those descriptors label it `regime-shifted`,
with lower volatility and fewer jumps than development. No candidate strategy
has been evaluated on it, and no holdout strategy artifact is committed.

Canonical selections:

- [`development_windows.csv`](results/panels/btcusdt_l2_panel_v2/development_windows.csv)
- [`holdout_windows.csv`](results/panels/btcusdt_l2_panel_v2/holdout_windows.csv)
- [`window_selection_summary.json`](results/panels/btcusdt_l2_panel_v2/window_selection_summary.json)
- [`holdout_protocol.md`](notebooks/holdout_protocol.md)

For the frozen economics, each five-hour development window is an inference
cluster made from five separate one-hour replay episodes. Strategy, execution,
and inventory state restart each hour; residual inventory is marked to that
session's final mid and then discarded without modeled liquidation. The
five-hour statistic is therefore a sum of hourly marked episodes, not one
continuous portfolio path.

## What a fresh clone can reproduce

| Level | Raw data required | Command | Meaning |
|---|---|---|---|
| Deterministic mechanics | No | `pytest -q` | Unit, invariant, integration, and artifact-contract tests |
| Synthetic lifecycle | No | `python scripts/demo_event_driven_execution.py` | One fill/cancel race in `ExecutionSimulator` |
| Frozen artifact checks | No | `python scripts/verify_v2_artifacts.py` | Expected identities, shapes, verdict fields, model boundary, and strategy-sealed holdout |
| Report regeneration | No | `make report` | Figures and PDF from pinned committed summaries |
| V2 empirical replay | Yes | Historical scripts and raw hashes | Reconstruct the legacy experiment |
| V3 development replay | Yes | `python scripts/run_l2_panel.py --phase all` | Produce current-model development artifacts |

The synthetic lifecycle is not evidence of empirical realism or profitability.
The V2 verifier is a lightweight semantic guard, not a cryptographic
recalculation of every historical output. A V3 verifier cannot succeed until a
complete V3 run writes its artifact manifest.

The live recorder check is a manual network operation, not a unit test:

```bash
env PYTHONPATH=. python scripts/smoke_test_recorder.py
```

## Full-replay prerequisites

Reconstructing the frozen V2 experiment requires a detached checkout or
worktree at the full research commit
`106695026ca64ae232f07103ae380b49c2a4d49f`, plus the raw files at the manifest
paths with matching SHA-256 values. The historical commands and parameters are
recorded in [`research_writeup_v2.md`](notebooks/research_writeup_v2.md) and the
[`Phase 2 artifact index`](notebooks/phase2_artifact_index.md). Current `main`
code must not be used to write into the immutable V2 result root, and the
repository does not claim a one-command V2 rebuild from a fresh clone.

To run the current V3 development workflow, restore raw files at their manifest
paths, install the pinned Python requirements, and first verify that selected
file hashes match the inventory. The runner is pinned to the frozen development
CSV and rejects the holdout file:

```bash
env PYTHONPATH=. python scripts/run_l2_panel.py --phase all
```

Outputs belong only under:

```text
results/panels/btcusdt_l2_panel_v3_event_driven/
```

Current code guards the frozen V2 root against event-driven writes.

## Distribution and scope

The captures came from public Binance market-data interfaces. They are excluded
from Git because of size and because this repository should not imply rights to
redistribute venue data. Anyone assembling an independent dataset is responsible
for the venue's current terms, rate limits, and applicable data rules.

No API keys, account data, private orders, or live fills are part of the
published research dataset.
