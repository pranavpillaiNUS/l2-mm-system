# L2 Market Microstructure System

A quantitative trading research project built in three phases: a bar-level backtesting framework (Phase 1, complete), a deterministic L2 orderbook replay engine with execution simulation (Phase 2, in progress), and a C++17 port of the hot path for performance (Phase 3, planned).

The goal is to build and validate market-making strategies on real recorded L2 orderbook data, with a realistic model of execution: queue position, latency, partial fills, and maker/taker fee assignment.

---

## Phase 1 — Bar-Level Backtester (complete)

Tests systematic directional strategies on BTCUSDT hourly OHLCV bars with realistic transaction cost modeling. Validated with 9-window walk-forward analysis.

### What it does

Four strategies (Momentum, Mean Reversion, SMA Crossover, Bollinger Bands) are swept across 60+ parameter combinations, then validated out-of-sample using anchored expanding windows. Training always starts from January and grows by one month per step. The test month is strictly OOS.

### Key findings

**Transaction costs are the dominant source of lost alpha.** Momentum at a 1% threshold paid 22% of capital in fees and lost money despite positive gross returns. The same strategy at a 3% threshold paid 5% in fees and returned 40.7%. Correlation between trade count and net return was -0.50 across all 18 configurations.

**Parameter stability predicts OOS performance better than in-sample Sharpe.** Walk-forward completely reshuffled the strategy ranking. SMA Crossover had the best in-sample Sharpe (tied at 0.39) but the worst OOS result — its optimal parameters shifted across 4 combinations in 9 windows. Mean Reversion selected identical parameters all 9 times and its OOS Sharpe (0.51) actually exceeded in-sample (0.39).

**Trend-following and mean-reversion fail in opposite regimes.** Momentum lost money in 4 of 9 OOS months (all choppy markets). Mean Reversion had zero trades in 2 months (both strongly trending). Neither detects which regime it's in.

Walk-forward OOS results (Apr–Dec 2024):

| Strategy | OOS Sharpe | Final Equity | Losing Months | Param Stability |
|---|---|---|---|---|
| Mean Reversion (12h, 3%) | 0.51 | $126k | 0/9 | 1 unique combo |
| Bollinger (24, 2.5σ) | 0.33 | $125k | 1/9 | 3 unique combos |
| Momentum (24h, 3%) | 0.24 | $128k | 4/9 | 2 unique combos |
| SMA (24/72) | 0.22 | $117k | 4/9 | 4 unique combos |
| Buy & Hold | — | ~$148k | — | — |

No strategy beat buy-and-hold in a +116% bull market year. Expected — they are designed for risk-adjusted returns, not maximum return in a trending market.

### Architecture

```
src/backtester/
├── config.py       # BacktestConfig, FeeConfig (maker/taker bps), SlippageConfig
├── data_loader.py  # loads OHLCV bars from parquet
├── strategy.py     # BaseStrategy ABC + 4 implementations
├── engine.py       # BacktestEngine — event loop over bars
├── portfolio.py    # cash, position, avg entry, equity snapshots
├── metrics.py      # Sharpe, Sortino, Calmar, drawdown, trade matching
└── plotting.py     # equity curves, drawdown, return distributions

scripts/
├── download_bars.py       # fetch OHLCV from Binance API
├── compare_strategies.py  # 18-combo sweep (Momentum vs Mean Reversion)
├── sweep_sma.py           # 11-combo SMA sweep
├── sweep_bollinger.py     # 20-combo Bollinger sweep
└── walk_forward.py        # anchored walk-forward plus OOS plots
```

---

## Phase 2 — L2 Replay Engine + Execution Simulator (in progress)

Replays recorded L2 orderbook and trade data deterministically and simulates order execution with realistic microstructure effects.

### Data collection

Two recorders run continuously in tmux, writing hourly gzipped JSONL files:

```
data/raw/btcusdt/               # depth diffs + snapshots (~140MB/day compressed)
data/raw/btcusdt_trades/        # aggTrade stream (~19MB/day compressed)
```

Depth files contain two record types. Diff records (no `"type"` field) carry bid/ask level updates — quantity `"0"` means remove that level. Snapshot records (`"type": "snapshot"`) are full 1000-level REST snapshots taken at file start and after reconnects, used as resync points. Snapshots are present from April 12 2026 onwards; earlier files are diffs only and reconnection gaps are unrecoverable.

Trade files carry aggTrade events with sequential IDs for gap detection. Field `m=true` means the buyer was the maker (seller was aggressor, a market sell). Field `T` is the trade timestamp — use this for event ordering, not `recv_time`.

### What's been built

**`src/replay/orderbook.py`**
SortedDict-backed L2 orderbook using `Decimal` throughout (no float). Handles `apply_snapshot` and `apply_diff`. Exposes best bid/ask, mid, spread, and microprice. Microprice is volume-weighted mid — it skews toward whichever side has less resting size, making it a better short-term price predictor than arithmetic mid. SHA-256 state hashing for determinism verification.

**`src/replay/depth_parser.py`**
Reads hourly depth `.jsonl.gz` files and yields `DepthEvent` objects. Distinguishes snapshots from diffs by checking for the presence of the `"type"` key. Detects sequence gaps (`U != prev_u + 1`) and flags affected events. Resets gap tracking after each snapshot. Gap state carries across file boundaries.

**`src/replay/trade_parser.py`**
Reads hourly trade `.jsonl.gz` files and yields `TradeEvent` objects with `Decimal` prices and quantities. Detects gaps via `agg_trade_id`. Uses field `T` for `exchange_time_ms`.

**`src/replay/event_merger.py`**
2-way sorted merge of depth and trade streams using `heapq.merge`. Sort key is `exchange_time_ms`. Tiebreaker: depth before trade — the book update reflects the state after the matching engine processed that order, so it must be applied before the trade is dispatched to the strategy.

**`src/execution/order.py`**
Data types for the execution layer: `OrderRequest` (strategy intent — side, type, price, qty), `Order` (simulator-tracked lifecycle with mutable status and queue state), `Fill` (one per partial or full fill, with maker/taker flag and fee), `OrderEvent` (one per state transition — placed, arrived, queued, partial_fill, filled, cancelled).

**`src/execution/simulator.py`**
The execution simulator. Takes `OrderRequest` objects from strategies, applies latency (`base + uniform(±jitter)` with seeded PRNG for determinism), and tracks each order through its full lifecycle.

FIFO queue model: when a limit order arrives, `queue_ahead` is set to the book quantity at that price level. Trade events at the limit price drain `queue_ahead` from the front; once it reaches zero, the order starts filling. When book quantity at an active order's price decreases without a corresponding trade, the decrease is treated as cancellations and `queue_ahead` shrinks proportionally.

Market orders walk available book levels greedily; unfilled remainder is cancelled. Aggressive limit orders (price crosses the spread) execute immediately as takers. Fees are assigned per fill: maker rate for resting limit fills, taker rate for everything else.

**`src/replay/engine.py`** — the main replay loop. Takes a list of depth and trade files, creates the event merger, drives the orderbook forward event by event, calls `simulator.on_book_update` and `simulator.on_trade`, dispatches to the strategy, and handles gap/resync (pauses strategy callbacks when a gap is detected, resumes after the next snapshot). Returns a `ReplayResult` with all fills and events.

**`src/strategies/`**
Base market-making strategy plumbing plus two quoting strategies. The base class
owns order tracking, inventory accounting, position limits, tick rounding, and
optional quote-throttling via `requote_interval_ms`.

| Strategy | Quoting reference | What it adds |
|---|---|---|
| `SymmetricMM` | arithmetic mid | baseline — symmetric bid/ask around mid |
| `MicropriceMM` | microprice | tests whether top-of-book imbalance improves fill quality |
| `InventorySkewMM` | planned | shift quotes toward flat as inventory grows |
| `VolAdaptiveMM` | planned | widen spreads in high-volatility periods |

Strategies inherit from `BaseMMStrategy` with callbacks `on_book_update`, `on_trade`, `on_fill`, each returning a list of actions: `OrderRequest` to place orders or `CancelRequest` to cancel existing orders.

**`src/analysis/markout.py`**
Computes side-normalized fill markouts at 1s, 5s, 30s, 1m, and 5m horizons using recorded book samples from the replay engine. Positive markout means favorable post-fill movement; negative markout indicates adverse selection.

**`src/analysis/pnl.py`**
Decomposes replay P&L into spread capture, residual inventory P&L, and fees. Adverse selection is reported separately as a markout-based diagnostic rather than treated as part of the accounting identity.

**`src/analysis/tail_diagnostics.py`**
Summarizes whether losses are broad-based or concentrated in adverse tails. It reports pooled and per-window tail contribution, clusters worst-5% fill and matched-lot outcomes across 60s/120s/300s gaps, tags clusters by UTC session-boundary proximity, and separates fill-level toxicity from matched-lot realization episodes.

**`scripts/run_replay.py`**
Runs a single L2 replay on local recorded data, prints event/execution/P&L/markout summaries, and can write `summary.json`, `fills.csv`, and `markouts.csv` under `results/replay/`.

**`scripts/compare_mm.py`**
Runs `SymmetricMM` and `MicropriceMM` across multiple L2 sessions, prints per-session and aggregate comparison tables, and writes CSV summaries under `results/compare/`.

**`scripts/sweep_mm_quote_mechanics.py`**
Runs a focused mini-sweep over half-spread and requote interval. This is deliberately narrower than a full parameter sweep: its job is to calibrate a credible passive baseline before adding more strategy complexity.

### Current research status

The first naive market-making setup produced misleading profitability because latency could turn intended passive limits into taker fills. The simulator now defaults to post-only behavior, and crossing limits are cancelled rather than filled as takers.

A later audit found a replay correctness issue around naive UTC snapshot timestamps and stale post-snapshot diffs. The depth parser now treats recorder timestamps as UTC and drops stale diffs until the first Binance-valid bridge diff. The six-window baseline, bootstrap CI, unconditional microprice signal test, and conditional-on-fill microprice toxicity test have all been regenerated after this fix. The negative conclusion survived the replay correction, which is a robustness result rather than a footnote.

The corrected passive microprice baseline (`half_spread=2.00`, `requote_interval_ms=5000`, `maker_bps=2`) does not show a stable positive edge across six 5-hour anchor windows. Unconditional microprice drift is weak and regime-dependent, and conditional-on-fill microprice skew does not rescue the strategy.

Tail Diagnostics V1 adds the sharper distributional picture:

- Fill-level 30s toxicity is broad and somewhat tail-heavy: worst 5% of fills explain about 39.6% of total adverse 30s movement, and worst 10% explain about 66.8%.
- Matched-lot losses are much more tail-dominated: worst 5% explain about 111.5% of total matched-lot loss, because the rest of the distribution offsets part of the damage.
- The matched-lot body is heterogeneous across windows. Apr 13 and Apr 14 lose even after removing their worst 5%; the other four windows have positive body economics.
- The largest matched-lot 300s cluster is an Apr 14 realization episode from 13:55:18 to 13:58:19 UTC: 8 matched lots from 4 unique closing fills and 5 unique opening fills, within 30 minutes after US cash open. This is descriptive only, not proof of a session-boundary effect.

The current research task is not to add `InventorySkewMM`, `VolAdaptiveMM`, or OFI yet. The next step is per-window and pooled maker-fee break-even analysis: full strategy, full matched lots, tail-excluded matched lots, and body-only matched lots. Quantity-weighted net per BTC is the load-bearing economic measure; unweighted per-lot metrics are distribution diagnostics.

### What's left to build

**`scripts/`**:
- `analyze_fee_break_even.py` — per-window/pool maker-fee break-even for full strategy, full matched lots, tail-excluded matched lots, and body-only matched lots
- `analyze_queue_sensitivity.py` — rerun the anchor windows under conservative queue-credit assumptions
- `sweep_mm_params.py` — broader parameter sweep after quote mechanics are stable
- `walk_forward_mm.py` — same anchored-window framework as Phase 1, adapted for L2 time periods
- `benchmark.py` — throughput, latency, and memory benchmarks (events/sec, ms/fill)

### Architecture (full picture)

```
src/replay/
├── orderbook.py      # SortedDict L2 book, Decimal prices, microprice, state hash  ✓
├── depth_parser.py   # parse depth .jsonl.gz, gap detection                        ✓
├── trade_parser.py   # parse trade .jsonl.gz, gap detection                        ✓
├── event_merger.py   # time-sorted merge of both streams                           ✓
└── engine.py         # main replay loop, gap/resync handling                       ✓

src/execution/
├── order.py          # OrderRequest, Order, Fill, OrderEvent types                 ✓
└── simulator.py      # FIFO queue, latency, partial fills, fees                    ✓

src/strategies/
├── base_mm.py        # BaseMMStrategy ABC                                           ✓
├── symmetric_mm.py   # quote symmetrically around mid                              ✓
├── microprice_mm.py  # quote around microprice                                     ✓
├── inventory_skew.py # shift quotes toward flat                                    [ ]
└── vol_adaptive.py   # widen in high vol, tighten in low vol                       [ ]

src/analysis/
├── markout.py        # adverse selection at multiple horizons                      ✓
├── pnl.py            # P&L decomposition                                           ✓
├── fill_rate.py      # fill probability analysis                                   ✓
├── hold_time.py      # FIFO matched-lot / hold-time reconciliation                 ✓
├── bootstrap.py      # confidence intervals over chosen sampling units             ✓
├── microprice_signal.py          # unconditional microprice drift tests             ✓
├── microprice_fill_toxicity.py   # conditional-on-fill microprice toxicity          ✓
└── tail_diagnostics.py           # tail concentration and cluster diagnostics        ✓

scripts/
├── run_replay.py                    # single replay session                         ✓
├── compare_mm.py                    # multi-session strategy comparison              ✓
├── sweep_mm_quote_mechanics.py      # focused spread/requote mini-sweep              ✓
├── analyze_microprice_signal.py     # unconditional microprice drift diagnostics      ✓
├── analyze_microprice_fill_toxicity.py # conditional-on-fill microprice diagnostics  ✓
├── analyze_mm_tail_diagnostics.py   # adverse tail and cluster diagnostics           ✓
├── sweep_mm_params.py               # broader MM parameter sweep                     [ ]
├── walk_forward_mm.py               # L2 walk-forward validation                     [ ]
└── benchmark.py                     # performance benchmark                          [ ]
```

---

## Phase 3 — C++17 Port (planned)

Port the hot path of the replay loop to C++17 via pybind11 for roughly a 10x speedup on parameter sweeps. The Python implementation is the reference; the C++ port must produce bit-identical results on the same input.

```
cpp/  # planned
├── CMakeLists.txt
├── orderbook.hpp / .cpp    # SortedMap with int64 prices (tick units, no Decimal overhead)
├── replay_engine.hpp / .cpp  # core event processing loop
└── bindings.cpp            # pybind11 — exposes C++ classes to Python
```

The parity test runs both implementations on the same input data and compares state hashes at every 1000th event. The benchmark measures events/sec and ms-per-parameter-sweep run, producing the concrete numbers for the resume.

---

## Repository structure

```
l2-mm-system/
├── src/
│   ├── backtester/    # Phase 1 (complete)
│   ├── recorder/      # live data recorders (run 24/7)
│   ├── replay/        # Phase 2 deterministic L2 replay engine
│   ├── execution/     # Phase 2 execution simulator (complete)
│   ├── strategies/    # Phase 2 baseline MM strategies
│   └── analysis/      # Phase 2 markout and P&L analysis
├── tests/             # deterministic test suite
├── scripts/           # runnable analysis scripts
├── notebooks/         # research_log.md, error_analysis.md, lessons_learned.md
├── data/
│   ├── bars/          # OHLCV parquet files
│   └── raw/           # recorded L2 data (gitignored, local only)
└── results/           # selected research artifacts; generated outputs are gitignored
```

---

## Setup

```bash
git clone https://github.com/pranavpillaiNUS/l2-mm-system.git
cd l2-mm-system

conda create -n l2mm python=3.11
conda activate l2mm
pip install -r requirements.txt
```

**Phase 1 — run a backtest:**
```bash
python scripts/download_bars.py
python scripts/compare_strategies.py
python scripts/walk_forward.py
```

**Run deterministic tests:**
```bash
env PYTHONPATH=. pytest -q tests --ignore=tests/test_recorder.py
```

Individual test files can also be run directly:
```bash
python tests/test_orderbook.py
python tests/test_depth_parser.py
python tests/test_trade_parser.py
python tests/test_event_merger.py
python tests/test_execution_simulator.py
python tests/test_engine.py
python tests/test_mm_strategies.py
python tests/test_markout.py
python tests/test_pnl.py
```

Note: `tests/test_recorder.py` is a live recorder/network test and is intentionally excluded from the deterministic suite. Some parser tests include smoke checks against local recorded files and skip those checks automatically when `data/raw/` is not present.

---

## Key design decisions

**Determinism is non-negotiable.** Same input + same parameters must produce identical output every time. This means: `Decimal` for all prices and quantities (no float), seeded PRNG for latency jitter, no wall-clock dependency anywhere in the replay path. State hashing at checkpoints verifies this.

**Depth before trade on equal timestamps.** When a depth diff and a trade share the same millisecond, the depth event is processed first. The book update reflects the state after the matching engine executed that trade — applying it first gives the strategy the correct view of the book before dispatching the trade.

**L2 queue position is an approximation.** L2 data shows aggregated volume per price level, not individual orders. The model places our order at the back of the queue at arrival, drains the front using trade events, and applies proportional shrinkage for cancellations. This is the standard industry approximation.

**Strategies submit `OrderRequest` objects, not `Order` objects.** The strategy expresses intent (side, type, price, qty). The simulator owns the lifecycle — it applies latency, assigns queue position, and manages fills. This keeps strategy logic clean and independent of execution mechanics.

**`FeeConfig` from Phase 1 uses float; `SimConfig` in Phase 2 uses `Decimal`.** They are intentionally separate. The bar backtester operates at a coarseness where float precision is irrelevant. The L2 simulator accumulates fees across thousands of fills where it is not.
