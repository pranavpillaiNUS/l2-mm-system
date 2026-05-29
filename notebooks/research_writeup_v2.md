# BTCUSDT L2 Market-Making Research Note V2

This V2 writeup supersedes the V1 six-window result in `notebooks/research_writeup.md` once the 24-window panel artifacts are generated. V1 is retained verbatim as a frozen reference showing the original corrected six-window conclusion and the reasoning that led to the expanded panel.

## Executive Summary

Status: pending Phase A panel generation.

The V2 panel keeps the same canonical passive microprice baseline and expands the evidence from 6 to 24 deterministic 5-hour BTCUSDT windows. The purpose is to test whether the V1 conclusion survives four times the data before adding OFI diagnostics, queue-credit sweeps, or any new strategy variants.

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

Classify the V2 result as one of:

- Confirms V1: headline direction and uncertainty are broadly similar.
- Strengthens V1: economics are materially worse or uncertainty narrows against the baseline.
- Weakens V1: economics are less negative but still not a stable positive edge.
- Overturns V1: V2 shows a stable positive baseline that V1 missed.

Do not proceed from this table directly to a strategy. Use it only to frame Phase B OFI diagnostics.

## Phase B OFI

Status: pending.

OFI gates are intentionally stricter than what microprice achieved in V1. OFI must clear a higher bar because the question is whether it is strong enough to justify strategy complexity, not whether it is marginally better than a weak prior signal.

## Phase C Queue And Regime Diagnostics

Status: pending.

The regime table is descriptive and for writeup context only. Do not select strategy filters from a 24-row table with many columns. Apparent patterns require holdout windows because spurious correlations are expected by chance.
