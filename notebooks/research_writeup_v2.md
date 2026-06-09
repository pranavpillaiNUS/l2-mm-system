# BTCUSDT L2 Market-Making Research Note V2

This V2 writeup supersedes the V1 six-window result in `notebooks/research_writeup.md`. The 24-window Phase A result artifacts were generated on 2026-06-09. V1 is retained as a frozen reference showing the original corrected six-window conclusion and the reasoning that led to the expanded panel.

## Executive Summary

Status: Phase A complete (2026-06-09). The integrity manifest and selected
panels are frozen, and the 24-window development baseline has been remeasured at
queue-credit endpoints `{0.0, 1.0}`. Headline: `Conditional V2:
queue-model-dependent result`; conservative passive-edge verdict `Strengthens
V1`. Phase B OFI diagnostics are the next step. The holdout remains sealed.

The V2 panel keeps the same canonical passive microprice baseline and expands the evidence from 6 to 24 deterministic 5-hour BTCUSDT development windows. The selected size was derived from strict manifest-clean capacity. The purpose is to test whether the V1 conclusion survives broader data before adding any new strategy variant.

The short answer: it does. Across 24 windows the passive microprice baseline is net-negative in 18 of 24 windows at each queue endpoint, and under the realistic proportional queue model the window-level mean net PnL CI now excludes zero (it crossed zero in V1). The negative conclusion strengthened on more, cleaner, pre-selected data. This is a robustness result, not a profitability result.

## Replay Correctness Protocol

New research defaults to `trade_gap_policy="pause_until_snapshot"`. A trade gap cancels open orders, pauses unreliable events, and resumes after the next valid depth snapshot. V1 robustness is published separately in `results/replay_correctness/trade_gap_anchor6_delta.json` by replaying all six anchors under both `ignore` and `pause_until_snapshot`.

The six anchors were directly parser-audited as trade-gap-clean. The published delta artifact reports exact zero deltas for fills, matched PnL, residual PnL, total PnL, and gap counts in every anchor.

> V1 was not a trade-gap artifact.

## Integrity And Panels

The manifest cutoff is `2026-06-01T00:00` UTC, end-exclusive. Any five-hour block containing an invalid hour is excluded. Development retains the six V1 anchors and uses `min(24, clean non-overlapping capacity)`. Holdout begins at `2026-05-20T00:00` and uses `min(12, clean non-overlapping capacity)`. Never relax integrity rules to force panel size.

Frozen manifest SHA-256: `a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61`.

| Range | Valid Hours | Candidate Five-Hour Starts | Strict Non-Overlapping Capacity |
|---|---:|---:|---:|
| Development before `2026-05-20T00:00` | 512 | 377 | 89 |
| Holdout from `2026-05-20T00:00` to `2026-06-01T00:00` | 263 | 185 | 43 |

The requested `24 + 12` panel fits the actual strict inventory.

Frozen panel SHA-256: `760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122`.
The selected panel contains `24` development windows and `12` sealed holdout
windows.

Before candidate holdout evaluation, compare drift, realized volatility, jump count, and UTC distribution across panels. These are correlated context descriptors, not independent confirmations and not strategy-tuning inputs.

The pre-strategy screen labels the holdout `regime-shifted`. Drift medians are
inside the development percentile bands, but late-May volatility and jump
descriptors have absolute standardized mean differences above `0.5` (roughly
`0.75` to `0.92`). Report this regime label alongside any later holdout verdict.
A `pass` is encouraging but may reflect easier conditions. A `fail` or `mixed`
result is consistent with an untested mechanism rather than a broken one. Do
not over-update in either direction.

## V1 To V2 Headline Comparison

Generated from the 2026-06-09 Phase A run. V1 numbers are the corrected
proportional six-window baseline. PnL figures are in account currency over a
5-hour window; CIs are 95% bootstrap (10,000 iterations, seed 7).

| Metric | V1 Six Windows | V2 24 Windows | What Changed |
|---|---|---|---|
| Window-level mean net PnL plus CI | `-2.4388`, `[-6.7121, +1.2887]` (proportional) | qc1 proportional: `-1.0431`, `[-2.3316, -0.0323]`; qc0 none: `-0.6858`, `[-1.8833, +0.2896]` | CI now excludes zero at the proportional endpoint. Mean less negative, variance much tighter. Negative result strengthened. |
| Window-level matched net PnL plus CI | `-0.3599`, `[-1.7966, +0.8134]` | qc1: `-0.3577`, `[-0.7990, +0.0602]`; qc0: `-0.1074`, `[-0.4503, +0.2526]` | Much tighter, still crosses zero at both endpoints. Completed round trips are near-flat; the firmer net loss is residual-inventory driven. |
| Pooled microprice `beta * signal_std` (1s) | `+0.0168 bps`, HAC `t +1.06` | `+0.0190 bps`, HAC `t +1.74`; positive 1s beta in 19 of 24 windows | Directionally stable (79% same-sign) but still fails the `|t| >= 2` and `0.05 bps` economic bars. Premise not rescued. |
| Fill-toxicity worst-5% tail share (30s) | `39.6%` of total adverse move | qc1 `40.6%`, qc0 `42.8%` (share of total); `25.9%` / `26.5%` (share of negative-only sum) | Stable on V1's denominator. Toxicity is broad, not a single-tail artifact. |
| Fee break-even by queue credit (full strategy) | proportional needs `2.4618 bps` rebate; none needs `1.1572 bps` | qc1 needs `1.3515 bps` rebate; qc0 needs `0.7693 bps`. Tail-excluded matched lots flip positive at both endpoints. | Still requires a maker rebate at both endpoints. Body is approximately break-even; the loss is tail plus residual inventory. |

## Phase A Decision

Classify each queue-credit endpoint in order:

- `Overturns V1`: CI lower bound is above zero.
- `Strengthens V1`: CI upper bound is below zero.
- `Weakens V1`: CI crosses zero and the mean improves toward positive by at least `25%` of the absolute frozen V1 endpoint mean.
- `Confirms V1`: CI crosses zero without that material improvement.

If endpoints disagree, emit `Conditional V2: queue-model-dependent result`, report both endpoint verdicts, and use the less favorable verdict for passive edge as the headline.

### Result (2026-06-09)

Headline: `Conditional V2: queue-model-dependent result`. Conservative
passive-edge verdict: `Strengthens V1`.

Both queue-credit endpoints have negative mean window-level net PnL across the 24
development windows, and 18 of 24 windows are net-negative at each endpoint. The
endpoints classify differently, so the headline is conditional:

| Endpoint | Mean net PnL | 95% CI | Endpoint verdict |
|---|---:|---|---|
| `1.0` proportional credit | `-1.0431` | `[-2.3316, -0.0323]` | `Strengthens V1` (CI upper bound below zero) |
| `0.0` no credit | `-0.6858` | `[-1.8833, +0.2896]` | `Weakens V1` (CI crosses zero, mean improves > 25% of frozen V1 mean `-1.4413`) |

The proportional endpoint produced a less negative point estimate than the
frozen V1 proportional mean (`-2.4388`, CI `[-6.7121, +1.2887]`) but a much
tighter CI that now excludes zero. More data did not make the baseline
profitable; it made the small loss statistically reliable. Per the disagreement
rule, the conservative verdict `Strengthens V1` is the headline.

Supporting evidence on the expanded panel:

- Matched round-trip PnL crosses zero at both endpoints (`-0.3577`,
  `[-0.7990, +0.0602]` proportional; `-0.1074`, `[-0.4503, +0.2526]` none).
  Completed cycles are near-flat, so the firmer full-strategy loss is
  residual-inventory driven, not round-trip driven.
- Microprice predictiveness is not rescued: pooled 1s `beta * signal_std`
  `+0.0190 bps`, HAC `t +1.74`, positive 1s beta in 19 of 24 windows. The sign
  is directionally stable but the signal fails both the `|t| >= 2` and the
  `0.05 bps` economic-size bars.
- Fill toxicity remains broad (pooled 30s median `-1.50 bps` proportional);
  worst-5% fill share is `40.6%` of the total adverse move, comparable to V1.
- Fee break-even: the full strategy needs a maker rebate at both endpoints
  (`1.3515 bps` proportional, `0.7693 bps` none). Tail-excluded matched lots
  flip positive at both, so the body is approximately break-even and the loss
  concentrates in the adverse tail plus residual inventory.

The two worst V1 anchor windows (Apr 13 and Apr 17) remain the two worst windows
on the expanded panel, so they are genuinely toxic windows rather than
small-sample artifacts. The queue-model dependence that V1 flagged is now the
formal headline rather than a footnote, and microprice-only passive quoting
still shows no demonstrated standalone edge.

Do not proceed from this table directly to a strategy. Use it only to frame Phase B OFI diagnostics.

## Phase B OFI

Status: pending.

OFI must clear `75%` same-sign 1s beta stability across development windows, pooled HAC `|t| >= 2`, and pooled `|beta * signal_std| >= 0.05 bps`. Conditional 30s toxicity is `pass`, `fail_signal`, or `inconclusive_power`; thin buckets are not mislabeled as signal failure.

## Phase C Queue And Regime Diagnostics

Status: pending.

The regime table is descriptive and for writeup context only. Do not select strategy filters from a 24-row table with many columns. Apparent patterns require holdout windows because spurious correlations are expected by chance.

Queue stress reports credits `{0.0, 0.25, 0.5, 0.75, 1.0}` at `10ms` and endpoint latencies `{0, 10, 50}ms`. Candidate advancement uses paired five-hour quantity-weighted matched net PnL per BTC, not total matched PnL.

The holdout remains sealed until `notebooks/holdout_protocol.md` is filled and committed with the lock timestamp, commit hash, candidate definition, development CI bounds, and no-retuning rule. The template already records the frozen manifest hash, selected-panel hash, `regime-shifted` label, and the pre-committed interpretation constraint.
