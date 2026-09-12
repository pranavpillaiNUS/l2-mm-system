# V3 development protocol

Protocol ID: `v3_development_protocol_v1`.
Status: completed prospective specification. The [original protocol](https://github.com/pranavpillaiNUS/l2-mm-system/blob/63f4f0b/notebooks/v3_development_protocol.md)
was committed before inspecting the full corrected development rerun.
This copy has subsequent punctuation edits. The decision artifact retains the
original protocol's SHA-256, verified at the recorded research revision along
with the run's source and input fingerprints. The experiment, thresholds, and
decision rule are unchanged. This is a repository specification, not an
external registration.

## Question and hypotheses

Does the previously observed population association between normalized OFI and
future mid-price drift survive corrected snapshot timing, and does its
conditional-on-passive-fill response clear the existing development screen?
The prospective hypotheses are:

1. The pooled one-second normalized OFI coefficient remains positive.
2. The defined 30-second fill-conditioned separation remains below `1.0 bps`
   at both queue-credit endpoints.

These hypotheses do not assert that every window has a positive coefficient,
that OFI is universally ineffective, or that an unrun candidate would lose.
Insufficient conditional counts produce an inconclusive screen. They do not
support the second hypothesis as a powered negative finding.

## Frozen experiment

Use all and only the same 24 non-overlapping five-hour development windows in
`results/panels/btcusdt_l2_panel_v2/development_windows.csv`, SHA-256
`c779138fdffb739715c53cfa27c75b3c8ca140bc4f5a6128f60f2251948bd362`.
The integrity inventory identity is
`a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61`.
The runner must verify the inventory file and each selected raw file before
replay. These are development observations, including the six old anchors.
Do not inspect strategy outcomes on the 12 strategy-sealed holdout windows.

| Setting | Locked value |
|---|---|
| Venue/instrument | Recorded Binance spot BTCUSDT |
| Strategy | Existing `MicropriceMM` baseline |
| Episodes | Five independently initialized one-hour episodes per window |
| Order quantity / maximum position | `0.001 BTC` / `0.01 BTC` |
| Half-spread / tick size | `2.00 USDT` / `0.01 USDT` |
| Requote interval | `5000 ms` |
| Entry / cancellation latency | `10 ms` / `10 ms` |
| Entry / cancellation jitter | `0 ms` / `0 ms`, simulator seed `42` |
| Maker / taker fees | `2 bps` / `5 bps`, post-only enabled |
| Queue cancellation credit | Both `0` and `1` |
| Execution model | `event_driven_v2` |
| Equal-time policy | `market_data_before_private_actions_v1` |
| Snapshot policy | `post_response_proxy_depth_boundary_v1` |
| Trade-gap policy | `pause_until_snapshot` |

Inventory is marked to session-end mid and reset. Liquidation cost is omitted.
The five-hour total is an aggregate of episodes, not a continuous portfolio.
No parameter search, threshold retuning, or window reselection belongs to this
rerun. Write all new execution-derived evidence outside the frozen V2 root.

## Measurements and fixed screen

Run phases A and B of `scripts/run_l2_panel.py`. Keep per-window and per-session
fills, maker/taker counts, P&L and residual exposure, matched-lot reconciliation,
markouts and hold times, same-millisecond attribution, tail and cluster
diagnostics, fee break-even estimates, and the full OFI regressions and buckets.
The authoritative baseline uncertainty uses the 24 five-hour window clusters,
10,000 bootstrap resamples, seed `7`, and a two-sided percentile 95% interval.
Session- and lot-level intervals remain diagnostics because their independence
assumptions are stronger.

For population OFI use the existing one-second interval and one-second sample
grid, with the one-second forecast horizon primary. Existing other horizons
remain descriptive. The support thresholds are unchanged: the dominant
coefficient sign occurs in at least `75%` of usable windows, pooled absolute
HAC `t >= 2`, absolute predicted drift per signal standard deviation
`>= 0.05 bps`. Sign stability can favor either sign under the historical
screen. Separately report the prospective positive pooled-coefficient
hypothesis, the number of usable windows, and positive/negative counts.

For fills use only maker fills and signal samples strictly before each fill.
Do not admit equal-millisecond information. The OFI interval, maximum anchor
staleness, and maximum future-sample lag are each `1000 ms`. The primary
conditional horizon is `30 s`. Use the existing side-aligned bucket edges
`[-1, -0.25, 0, 0.25, 1]` and pooled buy/sell rows (`side=all`). Choose the
most-populated negative and most-populated nonnegative buckets, breaking count
ties in the existing ascending bucket order. Separation is favorable-bucket
mean side-normalized forward mid move minus adverse-bucket mean, in bps.
These are the selected populated buckets, not necessarily extreme buckets.

At each endpoint:

- Population support fails if any of its three thresholds fails.
- Conditional support is `inconclusive_power` if either selected bucket is
  absent or has fewer than `30` observations. This name is retained for
  compatibility, 30 is a minimum-count rule, not a formal power calculation.
- With adequate counts, conditional support passes at separation `>= 1.0 bps`
  and fails below `1.0 bps`.
- An endpoint is `blocked` on population failure or an adequately counted
  conditional failure. It is `inconclusive` on population support with weak
  conditional counts. Otherwise it is `supported`.

The combined screen is supported only when **both** queue endpoints are
supported. Any blocked endpoint blocks the combined screen. Otherwise any
inconclusive endpoint makes it inconclusive. Preserve both endpoint findings
even when they disagree. The existing analyzer's
`supported_with_conditional_power_limit`/`all_pass` fields do not authorize
advancement. Its optional five-second fallback is exploratory and cannot
replace this primary screen or supply a positive decision.

## Diagnostic conditional uncertainty

In addition to the fixed screen, report a 95% percentile interval for the
30-second selected-bucket contrast. Fix the two labels chosen from the original
pooled sample, then resample all 24 five-hour windows with replacement 10,000
times using Python `Random(7)`. Each selected window brings all of its fills.
Within each resample compute pooled fill-weighted means for the fixed labels.
Include windows with no qualifying fills as zero-count clusters. Count and
report resamples missing either bucket. If any resample is undefined, withhold
the interval and label it inconclusive. Do not discard undefined resamples
silently. Report windows contributing to each bucket.

This interval conditions on the original bucket selection. It does not account
for selecting bucket labels or all cross-window temporal dependence. It is a
diagnostic addition, not a new gate, a formal power calculation, or a reason
to move the `1.0 bps` threshold after seeing results.

## Interpretation, artifacts, and next actions

Compare endpoint baseline means and confidence intervals descriptively with
the frozen `legacy_book_update_v1` results on the exact same windows. A
difference between means or marginal intervals is not a paired confidence
interval and does not by itself strengthen or overturn a strategy claim.
The previous queue/latency stress grid remains historical. Phases A/B repeat
the two queue endpoints at the locked 10 ms latency. A new claim about an
intermediate-credit or latency grid requires separately rerunning and
versioning that grid. Completing this baseline rerun does not require such a
claim. Do not label historical stress results as current-model evidence.

After the runner writes its manifest, verify it and summarize with:

```bash
env PYTHONPATH=. python scripts/verify_v3_artifacts.py
env PYTHONPATH=. python scripts/summarize_v3_development.py
```

The decision summary is written to
`results/panels/btcusdt_l2_panel_v3_development_summary/summary.json`, outside
the verified run tree so it does not invalidate the run manifest. It retains
both full endpoint CI and OFI diagnostics, input hashes, the descriptive
legacy comparison, and the fixed-screen findings. No script here runs a
candidate or opens the holdout. A supported screen only justifies separately
specifying a development candidate. Candidate economics must qualify at both
endpoints and a new holdout protocol must be locked before a one-shot holdout
evaluation. A blocked or inconclusive screen leaves the holdout sealed.

Freeze the corrected Python model, artifact schema and deterministic traces
after verification. The authorized C++ implementation follows that reference,
parity and measured benchmarks must precede performance claims. A negative
research screen is a completed research result, not an obligation to retune
until it passes.
