# V2 Holdout Protocol

Status: **UNLOCKED TEMPLATE. Do not run candidate strategy holdout results until
this file is filled, committed, and changed to `LOCKED`.**

## Lock Record

- Lock timestamp (UTC): `TBD`
- Lock commit hash: `TBD`
- Integrity manifest SHA-256: `TBD`
- Development panel SHA-256: `TBD`
- Regime-comparability label: `TBD`

## Candidate Strategy

- Strategy: `OFIGatedMM`
- OFI interval: `TBD`
- OFI threshold: `TBD`
- Quote behavior when gated: `TBD`
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
