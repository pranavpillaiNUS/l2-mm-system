# BTCUSDT L2 Market-Making Research Note V2

This V2 writeup supersedes the V1 six-window result in `notebooks/research_writeup.md` once the 24-window panel artifacts are generated. V1 is retained verbatim as a frozen reference showing the original corrected six-window conclusion and the reasoning that led to the expanded panel.

## Executive Summary

Status: V2 protocol implemented, pending frozen manifest and Phase A panel generation.

The V2 panel keeps the same canonical passive microprice baseline and expands the evidence from 6 to up to 24 deterministic 5-hour BTCUSDT development windows. Final panel sizes are manifest-derived. The purpose is to test whether the V1 conclusion survives broader data before adding any new strategy variant.

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
`0.75` to `0.92`). A later candidate holdout failure is therefore ambiguous
between overfitting and regime change.

## V1 To V2 Headline Comparison

Fill this table after Phase A regeneration.

| Metric | V1 Six Windows | V2 24 Windows | What Changed |
|---|---:|---:|---|
| Window-level mean net PnL plus CI | TBD | TBD | TBD |
| Window-level matched net PnL plus CI | TBD | TBD | TBD |
| Pooled microprice `beta * signal_std` | TBD | TBD | TBD |
| Fill-toxicity worst-5% tail share | TBD | TBD | TBD |
| Fee break-even by queue credit | TBD | TBD | TBD |

## Phase A Decision

Classify each queue-credit endpoint in order:

- `Overturns V1`: CI lower bound is above zero.
- `Strengthens V1`: CI upper bound is below zero.
- `Weakens V1`: CI crosses zero and the mean improves toward positive by at least `25%` of the absolute frozen V1 endpoint mean.
- `Confirms V1`: CI crosses zero without that material improvement.

If endpoints disagree, emit `Conditional V2: queue-model-dependent result`, report both endpoint verdicts, and use the less favorable verdict for passive edge as the headline.

Do not proceed from this table directly to a strategy. Use it only to frame Phase B OFI diagnostics.

## Phase B OFI

Status: pending.

OFI must clear `75%` same-sign 1s beta stability across development windows, pooled HAC `|t| >= 2`, and pooled `|beta * signal_std| >= 0.05 bps`. Conditional 30s toxicity is `pass`, `fail_signal`, or `inconclusive_power`; thin buckets are not mislabeled as signal failure.

## Phase C Queue And Regime Diagnostics

Status: pending.

The regime table is descriptive and for writeup context only. Do not select strategy filters from a 24-row table with many columns. Apparent patterns require holdout windows because spurious correlations are expected by chance.

Queue stress reports credits `{0.0, 0.25, 0.5, 0.75, 1.0}` at `10ms` and endpoint latencies `{0, 10, 50}ms`. Candidate advancement uses paired five-hour quantity-weighted matched net PnL per BTC, not total matched PnL.

The holdout remains sealed until `notebooks/holdout_protocol.md` is filled and committed with the lock timestamp, commit hash, manifest hash, candidate definition, development CI bounds, regime label, and no-retuning rule.
