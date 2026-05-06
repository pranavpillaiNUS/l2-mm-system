# Project Brief — Current Stage

Last updated: 2026-05-06

## What This Project Is

This is a quantitative trading research system for studying market-making on real BTCUSDT order book data.

The project is being built in three phases:

1. **Phase 1: Bar-level backtester** — complete.
2. **Phase 2: L2 replay engine and execution simulator** — current focus.
3. **Phase 3: C++17 performance port** — planned after the Python research loop is credible.

The core idea is to replay recorded Binance L2 depth and trade data deterministically, simulate realistic order execution, and test whether market-making strategies can survive adverse selection, fees, latency, queue position, and inventory risk.

## What It Hopes To Accomplish

The project is meant to become a serious research artifact, not just a toy backtester.

It should show that you can:

- Build an end-to-end quantitative research system from raw data collection to strategy evaluation.
- Model execution realistically enough for market-making research: queue position, partial fills, latency, maker/taker fees, and gap handling.
- Explain where PnL comes from: spread capture, adverse selection, fees, and inventory movement.
- Validate strategies out-of-sample instead of relying on in-sample backtest wins.
- Eventually port the hot path to C++ while keeping Python as the reference implementation.

In practical terms, this project can become a strong portfolio/resume project because it demonstrates market microstructure knowledge, systems engineering, testing discipline, and quantitative research judgment.

## Current Status

The project is on the right track.

Phase 1 is complete and produced useful research conclusions: transaction costs dominated naive alpha, parameter stability mattered more than in-sample Sharpe, and strategies failed in different regimes.

Phase 2 now has a working research loop:

- L2 data is recorded locally.
- Depth and trade streams are parsed.
- Events are merged deterministically.
- The order book is reconstructed using snapshots and diffs.
- The execution simulator handles queue position, latency, partial fills, cancels, and fees.
- The replay engine connects data, order book, simulator, and strategy callbacks.
- Baseline market-making strategies exist.
- Strategies now support an optional requote interval to reduce cancel/replace churn.
- Markout analysis exists, with per-horizon p25/p50/p75 distribution.
- PnL decomposition exists, with adverse-selection diagnostics alongside the net-PnL identity.
- A replay script can run on real recorded data and print a full analytical summary.
- A multi-session comparison script can run both MM strategies across date ranges and write aggregate CSV summaries.
- A quote-mechanics mini-sweep script can compare half-spread and requote-interval combinations.

The analysis layer has been strengthened so that replay output now explains where PnL comes from, not just what it is. The project has now moved from "can one replay run work?" to "which quote mechanics survive repeated real sessions?"

## What Has Been Built

### Phase 1 — Bar Backtester

Built:

- OHLCV data loading from parquet.
- Bar-level event loop.
- Portfolio accounting.
- Fee and slippage modeling.
- Metrics: Sharpe, Sortino, Calmar, drawdown, trade stats.
- Strategies: Momentum, Mean Reversion, SMA Crossover, Bollinger Bands.
- Parameter sweeps and anchored walk-forward testing.

Main finding:

- The simple directional strategies did not beat buy-and-hold in the 2024 BTC bull market, but the project produced useful evidence about costs, stability, and regime dependence.

### Phase 2 — L2 Replay Core

Built:

- `src/replay/orderbook.py`
  - SortedDict-backed L2 book.
  - Decimal prices and quantities.
  - Best bid/ask, mid, spread, microprice.
  - Deterministic state hashes.

- `src/replay/depth_parser.py`
  - Parses gzipped depth files.
  - Handles snapshot and diff records.
  - Detects sequence gaps.

- `src/replay/trade_parser.py`
  - Parses aggTrade files.
  - Uses trade timestamp `T`.
  - Detects aggregate trade ID gaps.

- `src/replay/event_merger.py`
  - Merges depth and trade events by exchange timestamp.
  - Applies depth before trade on equal timestamps.

- `src/execution/order.py`
  - Defines `OrderRequest`, `Order`, `Fill`, and `OrderEvent`.

- `src/execution/simulator.py`
  - Applies latency with seeded randomness.
  - Tracks pending, active, partial, filled, and cancelled orders.
  - Models FIFO queue position from L2 quantities.
  - Drains queue from trade events.
  - Applies proportional queue improvement from cancellations.
  - Handles market orders and aggressive limits as takers.

- `src/replay/engine.py`
  - Main replay loop.
  - Handles snapshots, diffs, trades, fills, strategy callbacks, and gap recovery.
  - Cancels open orders during gaps, including pending orders.
  - Can record book samples for analysis.

### Phase 2 — Strategies

Built:

- `BaseMMStrategy`
  - Tracks bid/ask orders.
  - Handles cancel/replace logic.
  - Tracks inventory, realized PnL, fees, and fill count.
  - Enforces position limits.
  - Rounds bid prices down and ask prices up to tick size.
  - Supports optional `requote_interval_ms` so quotes can rest for a minimum interval before pure price refreshes.
  - Still cancels immediately when position limits suppress a side, so the throttle does not delay risk-reducing cancels.

- `SymmetricMM`
  - Quotes around arithmetic mid.
  - Baseline control strategy.

- `MicropriceMM`
  - Quotes around microprice.
  - First adverse-selection-aware strategy.

Current strategy contract:

- Strategy callbacks return actions:
  - `OrderRequest` to place an order.
  - `CancelRequest` to cancel an existing order.

### Phase 2 — Analysis And Scripts

Built:

- `src/analysis/markout.py`
  - Computes side-normalized markouts at 1s, 5s, 30s, 1m, and 5m.
  - Positive markout means favorable post-fill movement.
  - Negative markout means adverse selection.
  - Summary includes p25/p50/p75 distribution per horizon (in addition to mean).

- `src/analysis/pnl.py`
  - Decomposes net PnL into accounting components and reports adverse selection as a separate diagnostic:
    - **Spread capture**: edge captured vs mid at fill time.
    - **Fees**: total maker/taker fees paid.
    - **Inventory PnL**: residual position marked to session-end mid.
    - **Adverse selection**: proxy from markouts at a chosen horizon (default 30s).
  - Identity: `net_pnl ≈ spread_capture + inventory_pnl - fees`.
  - Adverse selection is a separate diagnostic, not a component of net_pnl.

- `scripts/run_replay.py`
  - Runs one replay session on local recorded data.
  - Supports `symmetric` and `microprice`.
  - Prints event counts, gap counts, order/fill counts, fees, position, PnL, markout distribution, and PnL decomposition.
  - Can write `summary.json`, `fills.csv`, and `markouts.csv` under `results/replay/`.
  - `summary.json` includes the full PnL decomposition.

- `scripts/compare_mm.py`
  - Runs `SymmetricMM` and `MicropriceMM` across multiple replay sessions.
  - Supports `--start`, `--end`, `--sessions`, `--session-hours`, strategy selection, latency, quote size, spread, and `--requote-interval-ms`.
  - Prints per-session comparison tables.
  - Writes `results/compare/comparison.csv`.
  - Writes aggregate-by-strategy output to `results/compare/aggregate.csv`.

- `scripts/sweep_mm_quote_mechanics.py`
  - Runs a focused mini-sweep over half-spread and requote interval.
  - Reuses the same replay path as `compare_mm.py`.
  - Writes detailed rows to `quote_mechanics_detail.csv`.
  - Writes grouped aggregate rows to `quote_mechanics_aggregate.csv`.
  - Prints a ranked table by aggregate net PnL.

Smoke-tested on real local data:

```bash
env PYTHONPATH=. python scripts/run_replay.py \
  --strategy symmetric \
  --date 2026-04-16 \
  --hour 12 \
  --hours 1 \
  --half-spread 0.50 \
  --order-qty 0.001 \
  --max-position 0.01 \
  --latency-ms 10 \
  --jitter-ms 0
```

The same run also works with `--strategy microprice`.

Multi-session comparison smoke-tested on real local data:

```bash
env PYTHONPATH=. python scripts/compare_mm.py \
  --start 2026-04-16T12 \
  --end 2026-04-16T17 \
  --half-spread 0.50 \
  --order-qty 0.001 \
  --max-position 0.01 \
  --latency-ms 10 \
  --jitter-ms 0
```

This writes:

- `results/compare/comparison.csv`
- `results/compare/aggregate.csv`

### First Comparison Evidence

Baseline 5-hour comparison window:

- Window: 2026-04-16 12:00 through 17:00 exclusive.
- Strategy pair: `symmetric` vs `microprice`.
- Parameters: half-spread `0.50`, order quantity `0.001`, max position `0.01`, latency `10ms`, jitter `0ms`.
- Gaps detected: `0` for both strategies.

Aggregate results:

| Strategy | Sessions | Fills | Maker % | Net PnL | Avg session PnL | Fees | Avg 30s markout bps | Adverse selection bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| microprice | 5 | 3480 | 2.07% | -158.7581 | -31.7516 | 126.4826 | -0.9706 | 0.9281 |
| symmetric | 5 | 3503 | 2.03% | -159.9407 | -31.9881 | 127.3239 | -0.9801 | 0.9397 |

Initial read:

- `MicropriceMM` was slightly better than `SymmetricMM` on net PnL, average markout, and adverse-selection bps.
- The difference is small; this is not yet strong evidence that microprice has robust edge.
- Maker fill rate was only about 2%, which is too low for the intended passive market-making interpretation.
- The low maker rate suggests the current quote behavior is too tight and/or too reactive before any strategy conclusion can be trusted.

Sanity checks and quote-mechanics mini-sweep:

- Wider quotes alone (`half_spread=5.00`, no requote interval) reduced fills and losses but maker rate was still only about 10% in the one-hour test.
- Zero latency did not materially change the one-hour baseline result.
- Adding a 1-second requote interval improved one-hour net PnL modestly but only nudged maker rate.
- Adding a 5-second requote interval cut losses further but made symmetric and microprice nearly identical on the tested hour.
- Combining wider quotes with a 5-second requote interval (`half_spread=5.00`, `requote_interval_ms=5000`) produced the cleanest sanity result on the tested hour:
  - Maker rate improved to 29.17%.
  - Net PnL moved close to flat.
  - Microprice had better adverse-selection bps than symmetric: `0.7207` vs `0.9446`.
- A one-hour quote-mechanics grid over half-spreads `0.50`, `1.00`, `2.00`, `5.00` and requote intervals `0`, `1000`, `5000` ranked the `5.00 / 5000ms` combination first for both strategies.
- Validating `half_spread=5.00` and `requote_interval_ms=5000` over the same five-hour window changed the baseline materially:

| Strategy | Sessions | Fills | Maker % | Net PnL | Avg session PnL | Fees | Avg 30s markout bps | Adverse selection bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| symmetric | 5 | 229 | 34.06% | 1.5643 | 0.3129 | 6.0781 | -1.4396 | 1.4019 |
| microprice | 5 | 232 | 34.05% | 1.3426 | 0.2685 | 6.1584 | -1.5209 | 1.4838 |

- Testing the same candidate baseline on the adjacent five-hour window, `2026-04-16T17` to `2026-04-16T22`, did not confirm robust profitability:

| Strategy | Sessions | Fills | Maker % | Net PnL | Avg session PnL | Fees | Avg 30s markout bps | Adverse selection bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| symmetric | 5 | 163 | 35.58% | -13.1352 | -2.6270 | 4.1993 | -1.4764 | 1.4390 |
| microprice | 5 | 163 | 35.58% | -13.1352 | -2.6270 | 4.1993 | -1.4764 | 1.4390 |

Research conclusion so far:

- Do not implement `InventorySkewMM` yet.
- The project has found a much more credible passive baseline candidate: `half_spread=5.00`, `requote_interval_ms=5000`.
- The candidate improved maker rate and reduced fee bleed, but it did not survive the adjacent out-of-sample block as profitable.
- `MicropriceMM` has not yet shown robust advantage; in the adjacent block it behaved identically to `SymmetricMM`.
- The next research question is whether nearby quote widths/requote cadences improve robustness, not whether to add a more complex strategy immediately.

## Current Test Coverage

Important tests:

```bash
env PYTHONPATH=. python tests/test_orderbook.py
env PYTHONPATH=. python tests/test_depth_parser.py
env PYTHONPATH=. python tests/test_trade_parser.py
env PYTHONPATH=. python tests/test_event_merger.py
env PYTHONPATH=. python tests/test_execution_simulator.py
env PYTHONPATH=. python tests/test_engine.py
env PYTHONPATH=. python tests/test_mm_strategies.py
env PYTHONPATH=. python tests/test_markout.py
env PYTHONPATH=. python tests/test_pnl.py
```

Known test note:

- `tests/test_recorder.py` is a live connection test and depends on environment/network setup. It should not be treated as part of the normal deterministic test suite.

## Important Design Decisions

- **Determinism matters most.** Same input and parameters should produce identical output.
- **Decimal is used for L2 prices and quantities.** Avoid float drift in execution and fee accumulation.
- **Depth before trade on timestamp ties.** This matches the assumption that the book update reflects the matching engine state after the trade.
- **L2 queue position is approximate.** The simulator models queue ahead from aggregate size because individual order IDs are not visible.
- **Snapshots are required for reliable replay.** Pre-April-12 data without snapshots may be unrecoverable after reconnect gaps.
- **Strategies express intent only.** The simulator owns order lifecycle and execution mechanics.
- **Quote churn is now a first-class parameter.** `requote_interval_ms` controls how frequently baseline MM strategies refresh stale prices.
- **Risk-reducing cancels must bypass quote throttles.** If position limits block a side, stale orders on that side are cancelled immediately.
- **Analysis should guide strategy development.** Do not build many strategy variants before understanding markouts, fees, and inventory effects.

## What Needs To Happen Next

### 1. Refine The Quote-Mechanics Search Around The Candidate

Use `scripts/sweep_mm_quote_mechanics.py` to search near the current candidate baseline across multiple windows.

Purpose:

- Keep the improved maker-fill behavior from slower, wider quoting.
- Find whether nearby quote widths and refresh cadences reduce adverse selection in the losing adjacent block.
- Avoid overfitting to one profitable five-hour window.

Suggested focused grid:

- Half-spread: `3.00`, `4.00`, `5.00`, `6.00`, `8.00`.
- Requote interval: `3000`, `5000`, `10000`.
- Windows:
  - `2026-04-16T12` to `2026-04-16T17`
  - `2026-04-16T17` to `2026-04-16T22`

Look for configurations that do not simply win one block while failing the next.

### 2. Expand The Quote-Mechanics Mini-Sweep Carefully

Goal:

- Run `scripts/sweep_mm_quote_mechanics.py` on more than one window.
- Add nearby values around the current winner, not a huge grid.
- Candidate refinements: half-spread `3.00`, `4.00`, `5.00`, `6.00`, `8.00`; requote interval `3000`, `5000`, `10000`.

Required metrics:

- fills
- maker%
- net PnL
- fees
- spread capture bps
- average and median 30s markout bps
- adverse-selection bps
- ending inventory
- gap count

### 3. Add Fill Rate And Quote Quality Analysis

Build `src/analysis/fill_rate.py`.

Useful breakdowns:

- Fill probability by distance from mid.
- Fill probability by quote age.
- Fill probability by volatility regime.
- Fill probability by time of day.
- Maker/taker split and cancel/replace rate.
- Average quote lifetime.
- Orders submitted per fill.

### 4. Implement InventorySkewMM

Only after quote mechanics produce a credible passive baseline, add inventory skew.

Purpose:

- Reduce inventory accumulation.
- Shift quotes toward flat as position grows.
- Measure whether lower inventory risk offsets any lost spread capture.

### 5. Implement VolAdaptiveMM

After inventory skew is working, add volatility-adaptive spread width.

Purpose:

- Widen quotes when short-term volatility is high.
- Avoid quoting too tightly during toxic or jumpy periods.

### 6. Add Parameter Sweeps

Build `scripts/sweep_mm_params.py`.

Initial sweep dimensions:

- Half-spread.
- Requote interval.
- Order quantity.
- Max position.
- Latency.
- Strategy type.

Output:

- CSV summary per run.
- Top configurations by net PnL, average markout, fill count, and drawdown/inventory risk.

### 7. Add Walk-Forward L2 Validation

Build `scripts/walk_forward_mm.py` only after sweeps work.

Goal:

- Recreate the Phase 1 discipline at L2 scale.
- Choose parameters on earlier windows and validate on later unseen windows.

### 8. Benchmark Before C++

Build `scripts/benchmark.py`.

Measure:

- Events per second.
- Runtime per hour of data.
- Memory usage.
- Runtime per parameter sweep.

Only start Phase 3 C++ after these numbers show where Python is actually too slow.

## What Not To Do Yet

Do not start the C++ port yet.

Do not build every strategy before analyzing the first two.

Do not implement `InventorySkewMM` until quote width and requote cadence have been calibrated.

Do not optimize performance before confirming the Python research outputs are meaningful.

Do not rely on net PnL alone. For market making, markouts, fees, fill quality, and inventory behavior matter just as much.

## Suggested Immediate Next Session

Recommended next session:

1. Run a focused quote-mechanics sweep around `half_spread=5.00`, `requote_interval_ms=5000`.
2. Include both five-hour blocks from April 16, not just the profitable first block.
3. Compare aggregate and per-block results so unstable winners are obvious.
4. Only after a stable baseline emerges, decide whether to refine quote mechanics further or move toward inventory skew.

The comparison and mini-sweep tools are now in place. The next job is to search for robustness, not just best single-window PnL.

## Current Project Health

Overall health: strong.

Why:

- The architecture is modular.
- Determinism is treated seriously.
- The execution model includes realistic microstructure effects.
- Tests cover the important Phase 2 components.
- The project has moved from backtesting into replay-based market microstructure research.
- The first multi-session comparison has already produced useful evidence and a better next question.

Main risk:

- Drawing strategy conclusions before quote mechanics are calibrated.

Best next principle:

- Calibrate the baseline first, then let the data decide what strategy complexity is justified.
