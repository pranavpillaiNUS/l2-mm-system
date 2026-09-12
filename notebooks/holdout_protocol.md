# V2 Holdout Protocol

Status: **HISTORICAL UNLOCKED TEMPLATE. Do not use this file to authorize a
future holdout run.**

This template was written for the blocked OFI candidate and was never locked.
It remains as protocol history. Any different future candidate requires a new,
generic holdout protocol committed before evaluation. This file must not be
retrofitted after development results are known.

Current Phase 2 status: no active candidate qualified for holdout. OFI was
blocked by the Phase B conditional-on-fill gate, so `OFIGatedMM` did not advance.

The holdout is strategy-sealed, not unseen: its window identities and
pre-strategy regime descriptors were inspected during panel selection. No
candidate strategy has been evaluated on those windows.

## Lock Record

- Lock timestamp (UTC): `TBD`
- Lock commit hash: `TBD`
- Integrity manifest SHA-256: `a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61`
- Frozen selected-panel SHA-256: `760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122`
- Regime-comparability label: `regime-shifted`

## Candidate Strategy

- Strategy: `TBD`
- OFI interval: `TBD`
- OFI threshold: `TBD`
- Quote behavior when gated: threshold-crossing positive normalized OFI
  suppresses the ask, threshold-crossing negative normalized OFI suppresses
  the bid. Otherwise quote both eligible sides. This remains a historical
  template only unless a future candidate clears development.
- Queue-credit endpoints: `{0.0, 1.0}`
- Retuning after lock: prohibited

## Locked Development Result

Record the paired five-hour development CI for:

```text
quantity_weighted_matched_net_pnl_per_btc =
    sum(matched_lot_net_pnl) / sum(matched_lot_quantity)
```

| Queue credit | Mean paired improvement | Lower 95% CI | Upper 95% CI |
|---|---:|---:|---:|
| `0.0` | `TBD` | `TBD` | `TBD` |
| `1.0` | `TBD` | `TBD` | `TBD` |

## Holdout Rule

The one-shot holdout passes only if, at both queue-credit endpoints:

- Mean paired matched-net-PnL-per-BTC improvement is positive.
- Mean improvement is at least the locked development lower CI bound.
- Positive matched quantity remains in every compared window.
- Candidate fills are at least `50%` of baseline fills.
- Maker fills remain `100%`.
- Full-strategy pooled PnL is no worse than baseline.
- Average absolute residual inventory is at most `110%` of baseline.

Emit `mixed` if economics improve but any guardrail or locked-bound check fails.
Emit `fail` if either endpoint has non-positive per-BTC improvement.

## Interpretation

The holdout is `regime-shifted`: late-May volatility is lower than development.
Report this regime label alongside the holdout verdict. A `pass` is encouraging
but may reflect easier conditions. A `fail` or `mixed` result is consistent with
an untested mechanism rather than a broken one. Do not over-update in either
direction.
