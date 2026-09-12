# Corrected V3 development study

Completed: 2026-09-11. Protocol committed before rerun: `63f4f0b`.
Python research reference: `04df629`.

The corrected study preserves the central result: normalized OFI predicts
population mid-price drift, but its defined passive-fill screen fails at both
queue endpoints. The baseline has negative sample mean net P&L at both
endpoints. No candidate advances and the strategy-sealed holdout remains sealed.

This completes the corrected baseline development study. The accompanying
[C++17 order-book port](../cpp/README.md) is a separate engineering milestone.
It reproduces the Python book's supported input domain. Execution and accounting
continue to use the Python reference.

## Purpose and experiment

The project tests whether a price-predictive signal remains useful when passive
execution determines which market states become fills. It demonstrates raw-data
reconstruction, deterministic replay, execution modeling, research discipline,
and measured native implementation. Finding a profitable strategy is not a
completion requirement.

The rerun uses the same 24 non-overlapping five-hour development windows and the
same 240 hash-verified BTCUSDT spot depth/trade files as V2. Each window aggregates
five independently initialized one-hour episodes. Inventory is marked at each
hour end and reset. Liquidation costs are omitted. All 24 windows are development
evidence. The 12 holdout windows were not used for strategy evaluation.

The locked baseline is `MicropriceMM`, with `0.001 BTC` orders, `0.01 BTC`
maximum position, a `2.00 USDT` half-spread, a `5000 ms` requote interval,
`10 ms` entry and cancellation latency, zero jitter, and modeled maker/taker
fees of `2/5 bps`. Queue-cancellation credit is tested at `0` and `1`.

V3 uses `event_driven_v2`, `market_data_before_private_actions_v1`, and
`post_response_proxy_depth_boundary_v1`. Entries and cancellations run on the
model clock, cancel/fill races and own-order FIFO volume conservation are
explicit. Recovery hides snapshot state until its selected post-response proxy
boundary. These corrections can alter which orders fill, so V2 execution
results were retained as historical evidence and V3 was rerun in a new namespace.

## Results

| Measurement | No queue credit | Proportional queue credit |
|---|---:|---:|
| Windows / hourly episodes | 24 / 120 | 24 / 120 |
| Maker fills / taker fills | 1,609 / 0 | 2,030 / 0 |
| Mean net P&L, USDT/window | -0.773882 | -0.978202 |
| 95% window-bootstrap net P&L interval | [-1.954489, +0.181666] | [-2.204134, -0.081496] |
| 30s conditional OFI separation, bps | -0.455884 | -0.064348 |
| Diagnostic clustered 95% separation interval, bps | [-1.496504, +0.517766] | [-0.903883, +0.691652] |
| Selected negative / nonnegative bucket counts | 685 / 157 | 817 / 196 |
| Defined conditional screen | Blocked | Blocked |

Population normalized OFI has a positive coefficient in all 24 windows. The
pooled one-second regression has 430,259 observations, predicted drift of
`+0.123285 bps` per signal standard deviation, HAC `t = 64.6214`, and
`R² = 0.0365623`. This passes the existing population support thresholds.

The conditional screen compares the most-populated negative and nonnegative
side-aligned buckets at 30 seconds. Both endpoints select `<-1.0` and
`[0.0,0.25)`. Separation is the nonnegative-bucket response minus the
negative-bucket response. Both counts exceed the minimum of 30. Both contrasts
fall below the pre-specified `1.0 bps` threshold. Neither endpoint triggers the
exploratory five-second fallback. The combined decision is **blocked**.

The new diagnostic interval resamples the 24 whole windows 10,000 times with
seed 7, keeping the two originally selected bucket labels fixed. All 24 windows
contribute observations to each selected bucket and no draw has an undefined
contrast. Both intervals cross zero. The intervals condition on bucket selection
and assume independent window clusters. They are not selection-adjusted and do
not alter the fixed gate. Counts above 30 are not a formal power calculation.

Both prospective hypotheses hold under the defined measurements: the pooled
population coefficient remains positive and the conditional separation remains
below the threshold at both endpoints. This does not establish that OFI is
universally ineffective or that an unrun strategy would lose.

## Comparison with the historical study

The descriptive change in mean net P&L from V2 is `-0.088129 USDT/window` with
no credit and `+0.064866 USDT/window` with proportional credit. The no-credit
interval still crosses zero. The proportional interval remains entirely
negative. These differences are descriptive model sensitivity, not paired
confidence intervals or an automatic strategy-advancement verdict.

The old intermediate-credit and latency sweep remains historical. V3 establishes
the two endpoint results at the locked 10 ms latency. It does not establish
current-model invariance over the old grid. A separate versioned sweep would be
needed before making that claim.

## Reproducibility and source freeze

The run manifest records all 61 completed workflow steps, 330 durable artifacts,
the selected raw hashes, experiment identity, and Python source fingerprint:

```text
45141ba610bb070217e6d08bbb5fef9ca1a725f5c7d5e40b6e14881450a4cb4d
```

The frozen research commit is `04df629`. Later benchmark and verification-tool
changes do not replace that reference. With the selected raw captures restored
and that commit available in Git history, verify and rebuild the decision using:

```bash
env PYTHONPATH=. python scripts/verify_v3_artifacts.py --source-revision recorded
env PYTHONPATH=. python scripts/summarize_v3_development.py --source-revision recorded
```

Without `--source-revision`, verification requires the current working source
to match the run. Recorded-revision verification requires the exact manifest
commit and reproduces the source hash from its Git objects. Raw files and durable
outputs are still checked against the unchanged manifest.

To rerun the original experiment, use an isolated checkout of `04df629`, restore
the selected raw files, and run `scripts/run_l2_panel.py --phase all`. The normal
entry point is serial and resumable. This completed run scheduled independent
commands concurrently through the same runner checks. Shared-cache OFI steps
remained sequential. No replay parameters or analysis algorithms were changed
by scheduling.

The machine-readable [decision summary](../results/panels/btcusdt_l2_panel_v3_development_summary/summary.json)
retains both full endpoint diagnostics, the fixed gate, clustered intervals,
protocol hash, and descriptive legacy comparison. The
[artifact manifest](../results/panels/btcusdt_l2_panel_v3_event_driven/ARTIFACT_MANIFEST.json)
identifies the durable evidence. Raw data and ephemeral caches remain excluded
from Git. Git attributes preserve the frozen output bytes, including generated
CSV line endings, so checkout conversion cannot invalidate their hashes.
The [PDF report](../report/l2_mm_research_report.pdf) documents the
historical V2 study. This note is the current V3 addendum.
