# BTCUSDT L2 Market-Making Research Note V2

This V2 writeup supersedes the V1 six-window result in `notebooks/research_writeup.md`. The 24-window Phase A result artifacts were generated on 2026-06-09. V1 is retained as a frozen reference showing the original corrected six-window conclusion and the reasoning that led to the expanded panel.

## Executive Summary

Status: Phase C complete (2026-06-14); the Phase A/B/C development arc is
finished. The integrity manifest and selected panels are frozen. Phase A remeasured the 24-window baseline at queue-credit
endpoints `{0.0, 1.0}` (`Conditional V2: queue-model-dependent result`,
conservative passive-edge verdict `Strengthens V1`). Phase B then tested OFI as a
premise: the unconditional signal is strong (pooled 1s HAC `t=64.6`, 24 of 24
windows same-sign, clean bucket dose-response), but the conditional-on-fill test
fails (separation `+0.13 bps` proportional, `-0.38 bps` none, both far below the
`1.0 bps` bar). OFI is therefore `blocked` as a passive maker edge, and
`OFIGatedMM` does not advance. Phase C queue-credit and latency stress
(2026-06-14) confirms the baseline negative is robust to both model levers: it
worsens monotonically with queue-cancellation credit and is invariant to latency
in `[0, 50]ms`. No candidate advances and the holdout remains sealed.

Closure note: the compact artifact map is
`notebooks/phase2_artifact_index.md`. The lightweight reproducibility guard is
`scripts/verify_v2_artifacts.py`, which checks the frozen hashes, Phase A/B/C
verdict shape, and sealed-holdout boundary without rerunning the expensive replay
panel.

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

Status: complete (2026-06-14). Verdict: `blocked`.

### Pre-registered gate

OFI had to clear `75%` same-sign 1s beta stability across development windows,
pooled HAC `|t| >= 2`, and pooled `|beta * signal_std| >= 0.05 bps`. Conditional
30s toxicity is `pass`, `fail_signal`, or `inconclusive_power`; thin buckets
(below 30 samples) are `inconclusive_power`, not signal failure. Conditional
`pass` requires a side-aligned 30s separation of at least `1.0 bps`. That `1.0
bps` value is a pre-registered materiality threshold: it was fixed before the run
and was not derived from baseline execution economics or adjusted after seeing the
conditional results. The whole gate was fixed before the run.

### Leakage hardening and robustness

Before the locked run, the conditional fill-toxicity computation was tightened to
strictly-pre-fill samples. The reference book and the OFI window now use the most
recent sample STRICTLY before the fill, so a book sample stamped at the fill
millisecond (which can encode the fill-causing move) cannot leak into the OFI or
the reference mid. A leakage tripwire in `tests/test_ofi_signal.py` requires a
near-zero t-stat when the OFI-to-drift pairing is destroyed (shuffled), which a
window-overlap bug would not satisfy. The unconditional path was unchanged.

The hardening moved the conditional separation only marginally (`qc1 +0.1264 ->
+0.1272 bps`; `qc0 -0.3788 -> -0.3756 bps`) and changed no status. Same-ms
leakage is therefore empirically negligible in this dense top-of-book data, so
the conditional-fail result is not a same-ms artifact.

### Unconditional result (passes, strongly)

The unconditional OFI signal is queue-independent (`qc0 == qc1`). Pooled across
the 24 windows:

| Horizon | n | beta | HAC t | R2 | beta * signal_std |
|---|---:|---:|---:|---:|---:|
| 1s | 430,278 | +0.1192 | +64.63 | 0.0366 | +0.1233 bps |
| 10s | 430,062 | +0.2424 | +35.83 | 0.0128 | +0.2508 bps |
| 1m | 428,862 | +0.3157 | +18.06 | 0.0032 | +0.3268 bps |
| 5m | 423,102 | +0.2958 | +7.48 | 0.0006 | +0.3069 bps |

Per-window 1s beta is positive in all 24 of 24 windows (100 percent same-sign),
with `|t|` from 4.48 to 26.90, so every window is individually significant. The
1s bucket dose-response is clean and monotone: average forward drift rises from
`-0.276 bps` at OFI `< -1.0` through zero to `+0.284 bps` at OFI `>= 1.0`. The
unconditional gate passes on all three bars (same-sign 100 vs 75, `|t| 64.63` vs
2, effect `0.1233 bps` vs 0.05).

### Conditional-on-fill result (fails)

The strategy-relevant test is conditional on receiving a passive fill. The 30s
side-aligned separation (favorable-OFI largest bucket minus adverse-OFI largest
bucket) is small and sign-inconsistent across queue models:

| Queue credit | Separation | Min bucket n | Status |
|---|---:|---:|---|
| 1.0 proportional | +0.1272 bps | 201 | fail_signal |
| 0.0 none | -0.3756 bps | 154 | fail_signal |

Both are far below the `1.0 bps` separation bar and well powered (`n >= 30`), so
this is `fail_signal`, not `inconclusive_power`. Combined with the strong
unconditional signal, the overall verdict is `blocked`.

### Interpretation (project centerpiece)

OFI is a real, strong, monotone predictor of forward mid drift at the population
level. But its predictive content did not survive conditioning on the passive
fills the maker received: favorable-OFI fills moved about as adversely as
adverse-OFI fills, and the separation stayed far below the materiality bar. The
fill sample is therefore not a representative draw from the book states where OFI
is predictive. This is consistent with adverse selection of the fills, since the
events that reach a resting quote are selected against the signal; the experiment
identifies the conditional-sample failure rather than every causal mechanism
behind it (hidden liquidity, participant heterogeneity, queue dynamics, and
event-order effects could all contribute). This is the sharpest single result in
the project: signal existence does not imply edge once execution conditioning is
applied honestly. It also explains why the microprice baseline does not improve
with a microprice-derived gate.

This blocks `OFIGatedMM`: it must not run unless Phase B is `supported` or
`supported_with_conditional_power_limit`, and it is neither. The holdout stays
sealed. The result does not speak to faster or taker-capable participants, to
inventory-aware or vol-adaptive quoting, or to other venues.

## Phase C Queue And Regime Diagnostics

Status: complete (2026-06-14). The negative baseline is robust to the
queue-credit and latency model levers.

Queue stress ran credits `{0.0, 0.25, 0.5, 0.75, 1.0}` at `10ms` plus endpoint
credits `{0.0, 1.0}` at latencies `{0, 50}ms` (the `10ms` endpoints reuse the
Phase A reconciliation runs). Pooled over the 24 development windows:

| Credit | Latency | Fills | Orders/fill | Matched net/BTC | Matched net | Full net | Matched break-even fee |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.0 | 0/10/50 | 1595 | 70.8 | -9.75 | -2.58 | -16.46 | +1.35 |
| 0.25 | 10 | 1796 | 62.9 | -15.08 | -4.38 | -17.64 | +1.00 |
| 0.5 | 10 | 1802 | 62.6 | -16.08 | -4.97 | -20.88 | +0.93 |
| 0.75 | 10 | 1831 | 61.6 | -17.51 | -5.69 | -21.33 | +0.84 |
| 1.0 | 0/10/50 | 2041 | 55.4 | -23.65 | -8.59 | -25.03 | +0.43 |

Three findings close the model-risk question:

- Latency-invariant under this configuration. For each credit, latencies
  `{0, 10, 50}ms` give identical matched and full-strategy PnL (only
  orders-per-fill changes marginally). Changing latency from 0 to 50ms did not
  change the simulated fill set. This is specific to the quote distance (a `$2`
  half-spread, about `0.28 bps` from the reference at a representative `$71.5k`
  price level) and the event path used here, not a general claim about latency
  sensitivity in market making.
- Monotonic in queue credit. More cancellation credit yields more fills
  (1595 to 2041) of worse quality: matched net per BTC degrades from `-9.75` to
  `-23.65` and full net from `-16.46` to `-25.03` as credit rises `0.0 -> 1.0`.
- The matched-lot sign flip does not persist. Matched net is negative across the
  entire grid, most favorable at credit `0.0` (`-0.107` per window, the near-flat
  Phase A no-credit value) and most negative at credit `1.0` (`-0.358` per
  window, the Phase A proportional value). The V1 six-anchor "positive matched
  under no credit" does not generalize. The proportional endpoint that Phase A
  headlined is the least favorable, so the Phase A negative is conservative.

The development regime table is descriptive and for writeup context only. Do not
select strategy filters from a 24-row table with many columns. Apparent patterns
require holdout windows because spurious correlations are expected by chance.
Candidate advancement, if any candidate had qualified, would use paired five-hour
quantity-weighted matched net PnL per BTC, not total matched PnL. No candidate
qualified: OFI is `blocked` and the baseline negative is robust, so no strategy
advances and the holdout stays sealed.

The holdout remains sealed until `notebooks/holdout_protocol.md` is filled and committed with the lock timestamp, commit hash, candidate definition, development CI bounds, and no-retuning rule. The template already records the frozen manifest hash, selected-panel hash, `regime-shifted` label, and the pre-committed interpretation constraint.
