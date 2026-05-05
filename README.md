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
├── walk_forward.py        # 9-window anchored walk-forward, all 4 strategies
└── plot_walk_forward.py   # OOS equity curves and monthly return charts
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

### What's left to build

**`src/replay/engine.py`** — the main replay loop. Takes a list of depth and trade files, creates the event merger, drives the orderbook forward event by event, calls `simulator.on_book_update` and `simulator.on_trade`, dispatches to the strategy, and handles gap/resync (pauses strategy callbacks when a gap is detected, resumes after the next snapshot). Returns a `ReplayResult` with all fills and events.

**`src/strategies/`** — four market-making strategy implementations, in order of complexity:

| Strategy | Quoting reference | What it adds |
|---|---|---|
| `SymmetricMM` | arithmetic mid | baseline — symmetric bid/ask around mid |
| `MicropriceMM` | microprice | reduces adverse selection by quoting a better mid |
| `InventorySkewMM` | microprice + skew | shifts quotes toward flat as inventory grows |
| `VolAdaptiveMM` | microprice + skew + vol | widens spreads in high-volatility periods |

All strategies inherit from `BaseMMStrategy` with callbacks `on_book_update`, `on_trade`, `on_fill`, each returning a list of `OrderRequest` objects.

**`src/analysis/`** — post-replay analysis:
- `markout.py` — adverse selection measurement: mid price change at 1s, 5s, 30s, 1min, 5min after each fill
- `pnl.py` — P&L decomposition into spread capture, adverse selection cost, fees, and inventory P&L
- `fill_rate.py` — fill probability by distance from mid, time of day, volatility regime

**`scripts/`**:
- `run_replay.py` — run a single replay session, print summary stats
- `sweep_mm_params.py` — parameter sweep over spread width, inventory limits, etc.
- `walk_forward_mm.py` — same anchored-window framework as Phase 1, adapted for L2 time periods
- `benchmark.py` — throughput, latency, and memory benchmarks (events/sec, ms/fill)

### Architecture (full picture)

```
src/replay/
├── orderbook.py      # SortedDict L2 book, Decimal prices, microprice, state hash  ✓
├── depth_parser.py   # parse depth .jsonl.gz, gap detection                        ✓
├── trade_parser.py   # parse trade .jsonl.gz, gap detection                        ✓
├── event_merger.py   # time-sorted merge of both streams                           ✓
└── engine.py         # main replay loop, gap/resync handling                       [ ]

src/execution/
├── order.py          # OrderRequest, Order, Fill, OrderEvent types                 ✓
└── simulator.py      # FIFO queue, latency, partial fills, fees                    ✓

src/strategies/
├── base_mm.py        # BaseMMStrategy ABC                                           [ ]
├── symmetric_mm.py   # quote symmetrically around mid                              [ ]
├── microprice_mm.py  # quote around microprice                                     [ ]
├── inventory_skew.py # shift quotes toward flat                                    [ ]
└── vol_adaptive.py   # widen in high vol, tighten in low vol                       [ ]

src/analysis/
├── markout.py        # adverse selection at multiple horizons                      [ ]
├── pnl.py            # P&L decomposition                                           [ ]
└── fill_rate.py      # fill probability analysis                                   [ ]

scripts/
├── run_replay.py          # [ ]
├── sweep_mm_params.py     # [ ]
├── walk_forward_mm.py     # [ ]
└── benchmark.py           # [ ]
```

---

## Phase 3 — C++17 Port (planned)

Port the hot path of the replay loop to C++17 via pybind11 for roughly a 10x speedup on parameter sweeps. The Python implementation is the reference; the C++ port must produce bit-identical results on the same input.

```
cpp/
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
│   ├── replay/        # Phase 2 data pipeline (partially complete)
│   ├── execution/     # Phase 2 execution simulator (complete)
│   ├── strategies/    # Phase 2 MM strategies (not started)
│   └── analysis/      # Phase 2 post-replay analysis (not started)
├── cpp/               # Phase 3 C++17 port (not started)
├── tests/             # one file per module, standalone (no pytest)
├── scripts/           # runnable analysis scripts
├── notebooks/         # research_log.md, error_analysis.md, lessons_learned.md
├── data/
│   ├── bars/          # OHLCV parquet files
│   └── raw/           # recorded L2 data (gitignored, local only)
└── results/           # output CSVs and plots (gitignored, force-add selectively)
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

**Run tests:**
```bash
python tests/test_orderbook.py
python tests/test_depth_parser.py
python tests/test_trade_parser.py
python tests/test_event_merger.py
python tests/test_execution_simulator.py
```

Note: `test_depth_parser.py` and `test_trade_parser.py` include a smoke test against a real recorded file. These require data in `data/raw/` and are skipped automatically if the files are not present.

---

## Key design decisions

**Determinism is non-negotiable.** Same input + same parameters must produce identical output every time. This means: `Decimal` for all prices and quantities (no float), seeded PRNG for latency jitter, no wall-clock dependency anywhere in the replay path. State hashing at checkpoints verifies this.

**Depth before trade on equal timestamps.** When a depth diff and a trade share the same millisecond, the depth event is processed first. The book update reflects the state after the matching engine executed that trade — applying it first gives the strategy the correct view of the book before dispatching the trade.

**L2 queue position is an approximation.** L2 data shows aggregated volume per price level, not individual orders. The model places our order at the back of the queue at arrival, drains the front using trade events, and applies proportional shrinkage for cancellations. This is the standard industry approximation.

**Strategies submit `OrderRequest` objects, not `Order` objects.** The strategy expresses intent (side, type, price, qty). The simulator owns the lifecycle — it applies latency, assigns queue position, and manages fills. This keeps strategy logic clean and independent of execution mechanics.

**`FeeConfig` from Phase 1 uses float; `SimConfig` in Phase 2 uses `Decimal`.** They are intentionally separate. The bar backtester operates at a coarseness where float precision is irrelevant. The L2 simulator accumulates fees across thousands of fills where it is not.
