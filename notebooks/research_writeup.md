# BTCUSDT L2 Market-Making Research Note

## Executive Summary

This project builds a deterministic L2 replay and execution simulator for BTCUSDT passive market-making research. The goal is not to present a strategy demo. The goal is to show a credible research workflow: reconstruct the book, model execution frictions, find and remove backtest artifacts, and explain why the tested baseline does or does not have edge.

What this work establishes: passive market-making on BTCUSDT under realistic execution does not show stable edge from microprice signal alone across the windows tested.

What this work does not establish: whether OFI, inventory-aware quoting, or different symbol/regime combinations would change that.

The current six-window result is a credible negative or conditional result, depending on which layer is being discussed. Full-strategy PnL is negative under both queue modes. Matched round-trip PnL is negative under the proportional cancellation-credit baseline, but positive under the conservative no-cancellation-credit mode, so the matched-lot conclusion is queue-model conditional.

## System

The replay system reconstructs BTCUSDT L2 books from recorded depth snapshots and diffs, merges them with aggregate trades by exchange timestamp, and drives a strategy through an event loop. Prices and quantities use `Decimal`, the order book is deterministic, and state hashing is available for replay checks.

The execution simulator models:

- post-only limit orders by default
- latency with deterministic seeded jitter
- maker and taker fees
- partial fills
- FIFO queue position using visible L2 quantity at the order price
- trade-driven queue drain
- configurable cancellation-driven queue credit

The main tested strategy is `MicropriceMM`, quoting around top-of-book microprice with `half_spread=2.00`, `requote_interval_ms=5000`, `order_qty=0.001`, `max_position=0.01`, and `maker_bps=2`.

The baseline queue mode is `queue_cancellation_mode=proportional`: book quantity decreases not explained by prior trades reduce `queue_ahead` proportionally. The conservative sensitivity mode is `queue_cancellation_mode=none`: only trades can drain queue ahead.

## Corrections

Two corrections changed the interpretation of the project.

First, early profitability contained accidental taker fills. Latency could leave intended passive limit orders crossing the spread at arrival, and the simulator filled those orders as takers. The simulator now defaults to post-only enforcement. Crossing limits are cancelled with `post_only_would_cross`, and current results supersede earlier pre-post-only results.

Second, a replay correctness issue existed around naive UTC snapshot timestamps and stale post-snapshot diffs. The depth parser now treats recorder `recv_time` values as UTC and drops stale diffs until the first Binance-valid bridge diff. The six-window baseline, bootstrap CI, microprice signal diagnostics, fill-toxicity diagnostics, and tail diagnostics were regenerated after this fix.

These corrections moved the project in the right direction: away from attractive contaminated results and toward a reviewable negative-result artifact.

## Baseline

The canonical baseline uses six 5-hour anchor windows:

- 2026-04-13 12:00-17:00 UTC
- 2026-04-14 12:00-17:00 UTC
- 2026-04-15 12:00-17:00 UTC
- 2026-04-16 12:00-17:00 UTC
- 2026-04-16 17:00-22:00 UTC
- 2026-04-17 12:00-17:00 UTC

Under proportional queue credit, the pooled baseline has 927 fills, 100% maker fills, and 2,276 post-only rejects. Window-level mean net PnL is -2.4388 with 95% bootstrap CI [-6.7121, +1.2887]. Window-level mean matched net PnL is -0.3599 with CI [-1.7966, +0.8134]. Matched-lot net PnL per BTC is -28.0814 with CI [-45.3967, -10.8951], but matched lots inside the same window are correlated, so this is a diagnostic unit rather than the headline inference unit.

The baseline conclusion is not "every window loses." Three windows have positive total and matched PnL. The point is stricter: across the six windows, the result is not stable enough to justify treating microprice-only passive quoting as a positive-edge strategy.

## Microprice

Microprice was tested two ways.

The unconditional signal test samples the book every second and regresses forward mid drift on microprice deviation. The pooled 1s result has beta * signal standard deviation of only +0.0168 bps, HAC t-stat +1.06, and R2 0.00031. Longer horizons are mixed and negative in the pooled result.

The conditional-on-fill test asks the strategy-relevant question: given a passive fill, does microprice skew at fill time identify favorable or toxic fills? At the 30s horizon, the dominant near-zero skew bucket has average side-normalized future movement of -2.0707 bps, and even the favorable-skew bucket has negative average movement (-1.2966 bps).

Together, these tests do not support adding complexity to the microprice-only strategy.

## Distribution

Tail diagnostics separate fill-level toxicity from matched-lot realization.

At the 30s fill level, toxicity is broad and somewhat tail-heavy: the median fill is negative, and the worst 5% of fills explain about 39.6% of total adverse 30s movement.

At the matched-lot level, losses are more episodic. The worst 5% of matched lots explain about 111.5% of total matched-lot loss because the rest of the distribution offsets part of the damage. This means the project has both a broad adverse-selection layer and a clustered realization layer.

The largest matched-lot cluster is descriptive, not causal evidence: an Apr 14 realization episode near US cash open. The anchor windows are not uniformly distributed across the day, so session-boundary interpretation needs a proper time-of-day baseline before it can carry weight.

## Fee Economics

The fee break-even analysis is queue-mode-specific and uses quantity-weighted net per BTC as the main economic measure.

Pooled proportional results:

| Row | Net PnL | Break-even maker fee bps | Required rebate bps | Net per BTC |
|---|---:|---:|---:|---:|
| Full strategy | -14.6329 | -2.4618 | 2.4618 | -33.2290 |
| Full matched lots | -2.1594 | 1.1395 | 0.0000 | -12.8078 |
| Tail-excluded matched | +2.3439 | 2.9767 | 0.0000 | +14.5359 |
| Body-only matched | -2.7361 | 0.7746 | 0.0000 | -18.2197 |

Pooled no-cancellation results:

| Row | Net PnL | Break-even maker fee bps | Required rebate bps | Net per BTC |
|---|---:|---:|---:|---:|
| Full strategy | -8.6480 | -1.1572 | 1.1572 | -23.5334 |
| Full matched lots | +1.6205 | 2.8529 | 0.0000 | +12.7049 |
| Tail-excluded matched | +4.8635 | 4.6838 | 0.0000 | +39.9596 |
| Body-only matched | +0.1990 | 2.1183 | 0.0000 | +1.7599 |

Full-strategy rows are endpoint-sensitive because residual inventory is marked at the window-close mid. They are economically complete, but they can be dominated by the last mark. Matched-lot rows isolate completed round trips, but exclude residual inventory tails.

## Queue Sensitivity

Queue sensitivity is the main model-risk result.

No-cancellation mode reduces pooled fills from 927 to 733, about 79.1% of the proportional fill count. Orders per fill worsen from 44.0248 to 55.5102, a 26.1% increase. Maker percentage remains 100% in both modes, so this is a queue-throughput effect, not a taker-fill artifact.

The conclusion is mixed by layer:

- Full-strategy net PnL is negative under both modes: -14.6329 proportional, -8.6480 none.
- Matched net PnL is not stable across modes: -2.1594 proportional, +1.6205 none.
- Residual inventory remains large and negative in pooled results under both modes.

That means the full-strategy negative result is robust to this queue sensitivity check, but the matched-lot negative result is conditional on queue-mode assumptions. This is not a failure of the project. It is the model-risk result the project needed to expose.

## Same-Millisecond Audit

The event merger processes depth before trade when both share the same millisecond. The design assumption is that the book update reflects state after the matching engine processed the trade.

The bounded audit found:

- 1,079,840 depth diff events
- 2,130,136 trade events
- 6,762 same-ms overlap timestamps
- 927 total fills
- 5 same-ms overlap fills
- 0.54% of fills at same-ms overlaps
- 1 attribution-risk candidate
- 0 artifact-evidenced wrong-attribution cases

The thresholds were 5% for overlap fills and 1% for wrong-attribution cases. Neither threshold was met. The same-ms rule is therefore documented as a quantified design assumption, not a blocker.

## Conclusion

The project is on the right path because it is now disciplined about negative evidence. The research loop found a contaminated profitability artifact, fixed it, found a replay correctness issue, fixed it, regenerated the artifacts, and then tested whether the remaining result depends on fees and queue assumptions.

The current conclusion is:

- Microprice-only passive market making does not show stable full-strategy edge across the six tested BTCUSDT windows.
- Unconditional microprice predictiveness is weak and economically small.
- Conditional-on-fill microprice skew does not rescue the strategy.
- Full-strategy PnL remains negative under both queue modes.
- Completed matched-lot PnL is queue-mode conditional, so it should not be oversold as a queue-robust negative result.

## Next Steps

1. Add more windows before drawing regime conclusions.
2. Validate or stress-test the queue model further, ideally against exchange-level fill evidence or stricter queue assumptions.
3. Test OFI as a separate signal family, both unconditionally and conditional on maker fills.
4. Only after the signal layer improves, test inventory-aware quoting and risk-control variants.

Do not start with `InventorySkewMM` or `VolAdaptiveMM`. The current evidence says the signal and execution assumptions need more validation first.
