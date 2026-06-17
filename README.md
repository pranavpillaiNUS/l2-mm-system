# L2 Market Microstructure System

> Start here: [notebooks/research_note_public.md](notebooks/research_note_public.md) is a two-page summary of the research result. This README is the full technical reference.

**Headline result.** Order flow imbalance (OFI) strongly predicted one-second forward mid-price movement across the population of book states (pooled HAC `t = 64.6`, positive in 24 of 24 development windows, about `+0.123 bps` per signal standard deviation), but that predictive content did not survive conditioning on the passive fills the maker actually received (30s side-aligned separation `+0.127 bps` under proportional queue credit and `-0.376 bps` under no credit, both far below the pre-registered `1.0 bps` materiality bar). This is a population-strong signal that a passive maker cannot harvest: the fills it receives are not a representative draw from the predictive book states, a result consistent with adverse selection. Signal existence does not imply edge under passive execution.

A quantitative trading research project built in three phases: a bar-level backtesting framework (Phase 1, complete), a deterministic L2 orderbook replay engine with execution simulation and a completed spot-market Phase A/B/C research arc (Phase 2), and a C++17 hot-path port for parity-tested performance measurement (Phase 3, planned next).

The goal is to test market-making hypotheses on real recorded L2 orderbook data with a realistic model of execution: queue position, latency, partial fills, and maker/taker fee assignment. The current research result is deliberately conservative: no passive candidate advanced, and the sealed holdout remains untouched.

---

## Phase 1 - Bar-Level Backtester (complete)

Tests systematic directional strategies on BTCUSDT hourly OHLCV bars with realistic transaction cost modeling. Validated with 9-window walk-forward analysis.

### What it does

Four strategies (Momentum, Mean Reversion, SMA Crossover, Bollinger Bands) are swept across 60+ parameter combinations, then validated out-of-sample using anchored expanding windows. Training always starts from January and grows by one month per step. The test month is strictly OOS.

### Key findings

**Transaction costs are the dominant source of lost alpha.** Momentum at a 1% threshold paid 22% of capital in fees and lost money despite positive gross returns. The same strategy at a 3% threshold paid 5% in fees and returned 40.7%. Correlation between trade count and net return was -0.50 across all 18 configurations.

**Parameter stability predicts OOS performance better than in-sample Sharpe.** Walk-forward completely reshuffled the strategy ranking. SMA Crossover had the best in-sample Sharpe (tied at 0.39) but the worst OOS result - its optimal parameters shifted across 4 combinations in 9 windows. Mean Reversion selected identical parameters all 9 times and its OOS Sharpe (0.51) actually exceeded in-sample (0.39).

**Trend-following and mean-reversion fail in opposite regimes.** Momentum lost money in 4 of 9 OOS months (all choppy markets). Mean Reversion had zero trades in 2 months (both strongly trending). Neither detects which regime it's in.

Walk-forward OOS results (Apr-Dec 2024):

| Strategy | OOS Sharpe | Final Equity | Losing Months | Param Stability |
|---|---|---|---|---|
| Mean Reversion (12h, 3%) | 0.51 | $126k | 0/9 | 1 unique combo |
| Bollinger (24, 2.5 std) | 0.33 | $125k | 1/9 | 3 unique combos |
| Momentum (24h, 3%) | 0.24 | $128k | 4/9 | 2 unique combos |
| SMA (24/72) | 0.22 | $117k | 4/9 | 4 unique combos |
| Buy & Hold | - | ~$148k | - | - |

No strategy beat buy-and-hold in a +116% bull market year. Expected - they are designed for risk-adjusted returns, not maximum return in a trending market.

### Architecture

```
src/backtester/
|--- config.py       # BacktestConfig, FeeConfig (maker/taker bps), SlippageConfig
|--- data_loader.py  # loads OHLCV bars from parquet
|--- strategy.py     # BaseStrategy ABC + 4 implementations
|--- engine.py       # BacktestEngine - event loop over bars
|--- portfolio.py    # cash, position, avg entry, equity snapshots
|--- metrics.py      # Sharpe, Sortino, Calmar, drawdown, trade matching
`--- plotting.py     # equity curves, drawdown, return distributions

scripts/
|--- download_bars.py       # fetch OHLCV from Binance API
|--- compare_strategies.py  # 18-combo sweep (Momentum vs Mean Reversion)
|--- sweep_sma.py           # 11-combo SMA sweep
|--- sweep_bollinger.py     # 20-combo Bollinger sweep
`--- walk_forward.py        # anchored walk-forward plus OOS plots
```

---

## Phase 2 - L2 Replay Engine + Execution Simulator (spot research arc complete)

Replays recorded L2 orderbook and trade data deterministically and simulates order execution with realistic microstructure effects.

### Data collection

Recorders run continuously in tmux, writing hourly gzipped JSONL files. Spot is the primary dataset; a long-lead perp (USD-M futures) capture runs in parallel for a later cross-venue study (recording and reconnect only, no perp analysis yet):

```
data/raw/btcusdt/               # spot depth diffs + snapshots (~140MB/day compressed)
data/raw/btcusdt_trades/        # spot aggTrade stream (~19MB/day compressed)
data/raw/btcusdt_perp/          # perp depth diffs + snapshots (@depth@100ms, U/u/pu)
data/raw/btcusdt_perp_trades/   # perp raw @trade stream (this futures feed has no aggTrade)
```

The recorders take `--market {spot,perp}`; spot is the default and its paths are unchanged. Perp is written to its own dataset so the two raw trees can never collide. Perp trades use the raw `@trade` stream because this environment's futures feed does not populate `@aggTrade`; raw trades are one record per fill, more granular than spot aggTrades, which the future perp parser and any spot-vs-perp comparison must account for.

Depth files contain two record types. Diff records (no `"type"` field) carry bid/ask level updates - quantity `"0"` means remove that level. Snapshot records (`"type": "snapshot"`) are full 1000-level REST snapshots taken at file start and after reconnects, used as resync points. Snapshots are present from April 12 2026 onwards; earlier files are diffs only and reconnection gaps are unrecoverable.

Trade files carry aggTrade events with sequential IDs for gap detection. Field `m=true` means the buyer was the maker (seller was aggressor, a market sell). Field `T` is the trade timestamp - use this for event ordering, not `recv_time`.

### What's been built

**`src/replay/orderbook.py`**
SortedDict-backed L2 orderbook using `Decimal` throughout (no float). Handles `apply_snapshot` and `apply_diff`. Exposes best bid/ask, mid, spread, and microprice. Microprice is volume-weighted mid - it skews toward whichever side has less resting size, making it a better short-term price predictor than arithmetic mid. SHA-256 state hashing for determinism verification.

**`src/replay/depth_parser.py`**
Reads hourly depth `.jsonl.gz` files and yields `DepthEvent` objects. Distinguishes snapshots from diffs by checking for the presence of the `"type"` key. Detects sequence gaps (`U != prev_u + 1`) and flags affected events. Resets gap tracking after each snapshot. Gap state carries across file boundaries.

**`src/replay/trade_parser.py`**
Reads hourly trade `.jsonl.gz` files and yields `TradeEvent` objects with `Decimal` prices and quantities. Detects gaps via `agg_trade_id`. Uses field `T` for `exchange_time_ms`.

**`src/replay/event_merger.py`**
2-way sorted merge of depth and trade streams using `heapq.merge`. Sort key is `exchange_time_ms`. Tiebreaker: depth before trade - the book update reflects the state after the matching engine processed that order, so it must be applied before the trade is dispatched to the strategy.

**`src/execution/order.py`**
Data types for the execution layer: `OrderRequest` (strategy intent - side, type, price, qty), `Order` (simulator-tracked lifecycle with mutable status and queue state), `Fill` (one per partial or full fill, with maker/taker flag and fee), `OrderEvent` (one per state transition - placed, arrived, queued, partial_fill, filled, cancelled).

**`src/execution/simulator.py`**
The execution simulator. Takes `OrderRequest` objects from strategies, applies latency (`base + uniform(+/-jitter)` with seeded PRNG for determinism), and tracks each order through its full lifecycle.

FIFO queue model: when a limit order arrives, `queue_ahead` is set to the book quantity at that price level. Trade events at the limit price drain `queue_ahead` from the front; once it reaches zero, the order starts filling. V2 uses `queue_cancellation_credit` in `[0.0, 1.0]`. Credit `1.0` preserves proportional shrinkage when displayed quantity falls without a matching trade. Credit `0.0` allows only trade-driven queue drain. Intermediate values are Phase C stress cases.

Market orders walk available book levels greedily; unfilled remainder is cancelled. Limit orders default to post-only behavior: if latency leaves a submitted limit crossing the spread at arrival, the simulator cancels it with `post_only_would_cross` rather than filling it as a taker. Fees are assigned per fill: maker rate for resting limit fills, taker rate for market orders and explicitly non-post-only aggressive limits.

**`src/replay/engine.py`** - the main replay loop. Takes a list of depth and trade files, creates the event merger, drives the orderbook forward event by event, calls `simulator.on_book_update` and `simulator.on_trade`, dispatches to the strategy, and handles gap/resync (pauses strategy callbacks when a gap is detected, resumes after the next snapshot). Returns a `ReplayResult` with all fills and events.

New research replays default to `trade_gap_policy="pause_until_snapshot"`. An aggTrade gap now cancels open orders, pauses unreliable events, and resumes only after the next valid depth snapshot. The legacy `ignore` policy remains available only for explicit before/after robustness audits.

The published six-anchor before/after audit is `results/replay_correctness/trade_gap_anchor6_delta.json`. All six anchors are trade-gap-clean and every reported old/new delta is exactly zero, so V1 was not a trade-gap artifact.

**`src/strategies/`**
Base market-making strategy plumbing plus two quoting strategies. The base class
owns order tracking, inventory accounting, position limits, tick rounding, and
optional quote-throttling via `requote_interval_ms`.

| Strategy | Quoting reference | What it adds |
|---|---|---|
| `SymmetricMM` | arithmetic mid | baseline - symmetric bid/ask around mid |
| `MicropriceMM` | microprice | tests whether top-of-book imbalance improves fill quality |
| `OFIGatedMM` | microprice plus recent normalized OFI | implemented as a gated candidate, but blocked because Phase B failed conditional-on-fill |
| `InventorySkewMM` | parked | not justified by the current evidence |
| `VolAdaptiveMM` | parked | not justified by the current evidence |

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

Current results assume post-only enforcement. Earlier pre-post-only results contained accidental taker fills and are superseded.

A later audit found a replay correctness issue around naive UTC snapshot timestamps and stale post-snapshot diffs. The depth parser now treats recorder timestamps as UTC and drops stale diffs until the first Binance-valid bridge diff. The six-window baseline, bootstrap CI, unconditional microprice signal test, and conditional-on-fill microprice toxicity test have all been regenerated after this fix. The negative conclusion survived the replay correction, which is a robustness result rather than a footnote.

The corrected passive microprice baseline (`half_spread=2.00`, `requote_interval_ms=5000`, `maker_bps=2`, `queue_cancellation_mode=proportional`) does not show a stable positive edge across six 5-hour anchor windows. Unconditional microprice drift is weak and regime-dependent, and conditional-on-fill microprice skew does not rescue the strategy.

Tail Diagnostics V1 adds the sharper distributional picture:

- Fill-level 30s toxicity is broad and somewhat tail-heavy: worst 5% of fills explain about 39.6% of total adverse 30s movement, and worst 10% explain about 66.8%.
- Matched-lot losses are much more tail-dominated: worst 5% explain about 111.5% of total matched-lot loss, because the rest of the distribution offsets part of the damage.
- The matched-lot body is heterogeneous across windows. Apr 13 and Apr 14 lose even after removing their worst 5%; the other four windows have positive body economics.
- The largest matched-lot 300s cluster is an Apr 14 realization episode from 13:55:18 to 13:58:19 UTC: 8 matched lots from 4 unique closing fills and 5 unique opening fills, within 30 minutes after US cash open. This is descriptive only, not proof of a session-boundary effect.

Fee break-even and queue-sensitivity diagnostics are generated. The full-strategy result is negative under both queue modes and would require a rebate in pooled results. On the six-anchor artifact matched-lot PnL was queue-mode conditional (negative under proportional cancellation credit, positive under `none`), but the Phase C 24-window queue-credit stress superseded this: matched net per BTC is negative across the entire credit grid (`-9.75` at credit `0.0` worsening to `-23.65` at credit `1.0`), so the matched-lot sign flip does not generalize. The matched-lot sign is robust; throughput, average fill quality, and loss magnitude are the queue-model-sensitive quantities. Queue sensitivity therefore belongs in the headline interpretation, not a footnote.

The same-millisecond depth/trade attribution audit is bounded and quantified. Across the six anchor windows, only 5 of 927 fills occurred at same-ms depth/trade overlaps (0.54%), with zero artifact-evidenced wrong-attribution cases. That is below the predefined escalation thresholds, so the depth-before-trade rule remains a documented design assumption.

V2 was run as evidence expansion, not strategy proliferation:

- `scripts/build_l2_integrity_manifest.py` freezes hourly file integrity before `2026-06-01T00:00` UTC with checksums, gzip/JSON validation, snapshot bridging, and depth/trade gap reasons.
- `scripts/select_l2_windows.py` caches one-hour depth descriptors, selects manifest-clean non-overlapping development and holdout panels, and computes one correlated regime-comparability screen before strategy holdout evaluation.
- `scripts/run_l2_panel.py` remeasures Phase A at queue-credit endpoints `{0.0, 1.0}` and runs the Phase B OFI diagnostics.
- `scripts/sweep_queue_credit.py` and `scripts/summarize_queue_credit_sweep.py` report queue-credit and latency stress using quantity-weighted matched net PnL per BTC.
- `notebooks/holdout_protocol.md` is the required pre-commit lock record before any one-shot candidate holdout run.

Phase A baseline remeasurement is complete (2026-06-09): the 24-window result is `Conditional V2: queue-model-dependent`, conservative verdict `Strengthens V1`. The passive microprice baseline still shows no stable edge, and under the proportional queue model the window-level net-PnL CI now excludes zero (it crossed zero in the V1 six-window result).

Phase B OFI diagnostics are complete (2026-06-14): verdict `blocked`. Unconditional OFI is a strong, queue-independent predictor of forward mid drift (pooled 1s beta `+0.119`, HAC `t 64.6`, positive 1s beta in 24 of 24 windows, clean monotone bucket dose-response from `-0.276 bps` to `+0.284 bps`). But conditional on receiving a passive fill, prior OFI gives no usable separation between toxic and benign fills (30s side-aligned separation `+0.13 bps` proportional, `-0.38 bps` none, both far below the `1.0 bps` bar). This is the project centerpiece: a strong population-level signal that a passive maker cannot harvest, because the fills it receives are the adversely-selected subsample. The conditional test was hardened to strictly-pre-fill samples with a leakage tripwire, and the result was essentially unchanged. `OFIGatedMM` does not advance.

Phase C queue-credit and latency stress is complete (2026-06-14): the baseline negative is robust to both model levers. Matched and full-strategy PnL worsen monotonically with queue-cancellation credit (matched net per BTC `-9.75` at credit `0.0` to `-23.65` at credit `1.0`) and are invariant to latency in `[0, 50]ms` at this spread, so the V1 "positive matched under no credit" does not generalize. Phases A, B, and C are complete; no candidate advances, the holdout stays sealed, and the disciplined outcome is to publish the expanded negative. Do not add `InventorySkewMM` or `VolAdaptiveMM` yet.

The compact artifact map is `notebooks/phase2_artifact_index.md`. The lightweight
verification command is:

```bash
env PYTHONPATH=. python scripts/verify_v2_artifacts.py
```

Frozen manifest SHA-256: `a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61`.
The strict inventory contains `89` clean non-overlapping development windows and
`43` clean non-overlapping late-May holdout windows, so the planned `24 + 12`
panel is available without relaxing eligibility rules.

Frozen panel SHA-256: `760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122`.
The selected panel contains `24` development and `12` holdout windows. The
pre-strategy holdout screen is `regime-shifted`: drift medians remain inside
development bands, but late-May volatility and jump descriptors exceed the
absolute standardized mean-difference limit of `0.5`. A later holdout failure
must therefore be framed as ambiguous between overfitting and regime change.
Report the `regime-shifted` label alongside any holdout verdict. A `pass` is
encouraging but may reflect easier conditions. A `fail` or `mixed` result is
consistent with an untested mechanism rather than a broken one. Do not
over-update in either direction.

Deterministic suite checkpoints:

- Pre-V2 fixture-fix checkpoint: `178 passed in 9.57s`.
- Initial local V2 scaffold: `215 passed in 9.49s`.
- Current deterministic suite after synthetic bar-fixture hardening:
  `216 passed in 5.14s`.
- After perp-recorder market split and OFI strictly-pre-fill leakage hardening:
  `223 passed in 5.08s`.
- Latest local hygiene verification on 2026-06-16:
  `223 passed in 5.37s`.
- Remote CI run ID: pending authenticated verification. This private repository
  returns `404` from the unauthenticated GitHub Actions API in the current
  environment, so no green remote run is claimed here.

### Next build scope

The next implementation phase is the C++17 hot-path port. Parity against the Python reference comes first; benchmark numbers are reported only after parity tests pass. Broader MM sweeps, L2 walk-forward, and perp analysis are parked until the port exists and the spot negative result is written up cleanly.

### Architecture (full picture)

```
src/replay/
|--- orderbook.py      # SortedDict L2 book, Decimal prices, microprice, state hash
|--- depth_parser.py   # parse depth .jsonl.gz, gap detection
|--- trade_parser.py   # parse trade .jsonl.gz, gap detection
|--- event_merger.py   # time-sorted merge of both streams
`--- engine.py         # main replay loop, gap/resync handling

src/execution/
|--- order.py          # OrderRequest, Order, Fill, OrderEvent types
`--- simulator.py      # FIFO queue, latency, partial fills, fees

src/strategies/
|--- base_mm.py        # BaseMMStrategy ABC
|--- symmetric_mm.py   # quote symmetrically around mid
|--- microprice_mm.py  # quote around microprice
|--- ofi_gated_mm.py   # implemented, blocked by Phase B conditional-on-fill result
|--- inventory_skew.py # parked, not justified by current evidence                   [ ]
`--- vol_adaptive.py   # parked, not justified by current evidence                   [ ]

src/analysis/
|--- markout.py        # adverse selection at multiple horizons
|--- pnl.py            # P&L decomposition
|--- fill_rate.py      # fill probability analysis
|--- hold_time.py      # FIFO matched-lot / hold-time reconciliation
|--- bootstrap.py      # confidence intervals over chosen sampling units
|--- microprice_signal.py          # unconditional microprice drift tests
|--- microprice_fill_toxicity.py   # conditional-on-fill microprice toxicity
`--- tail_diagnostics.py           # tail concentration and cluster diagnostics

scripts/
|--- run_replay.py                    # single replay session
|--- compare_mm.py                    # multi-session strategy comparison
|--- sweep_mm_quote_mechanics.py      # focused spread/requote mini-sweep
|--- analyze_microprice_signal.py     # unconditional microprice drift diagnostics
|--- analyze_microprice_fill_toxicity.py # conditional-on-fill microprice diagnostics
|--- analyze_mm_tail_diagnostics.py   # adverse tail and cluster diagnostics
|--- analyze_fee_break_even.py        # maker-fee break-even by queue credit
|--- analyze_queue_sensitivity.py     # legacy V1 endpoint comparison artifact
|--- audit_same_ms_attribution.py     # bounded same-ms depth/trade tie diagnostic
|--- audit_trade_gap_replay_delta.py  # old/new trade-gap replay robustness check
|--- build_l2_integrity_manifest.py   # frozen hourly integrity inventory
|--- select_l2_windows.py             # cached development and holdout panel selection
|--- run_l2_panel.py                  # resumable V2 endpoint panel runner
|--- sweep_queue_credit.py            # queue-credit and latency stress replays
|--- summarize_queue_credit_sweep.py  # V2 queue-credit stress summary
|--- run_ofigated_panel.py             # OFI-supported candidate replay only
|--- build_strategy_gate_metrics.py    # per-window baseline/candidate gate adapter
|--- sweep_mm_params.py               # parked until C++ throughput exists             [ ]
|--- walk_forward_mm.py               # parked until C++ throughput exists             [ ]
`--- benchmark.py                     # C++/Python performance benchmark               [ ]
```

---

## Phase 3 - C++17 Port (planned next)

Port the hot path of the replay loop to C++17 via pybind11 and measure the actual speedup on replay and sweep workloads. The Python implementation is the reference; the C++ port must produce bit-identical state hashes on the same input before any benchmark claim is made.

```
cpp/  # planned
|--- CMakeLists.txt
|--- orderbook.hpp / .cpp    # SortedMap with int64 prices (tick units, no Decimal overhead)
|--- replay_engine.hpp / .cpp  # core event processing loop
`--- bindings.cpp            # pybind11 - exposes C++ classes to Python
```

The parity test runs both implementations on the same input data and compares state hashes at every 1000th event. The benchmark measures events/sec and ms-per-parameter-sweep run, producing the concrete numbers for the resume.

---

## Repository structure

```
l2-mm-system/
|--- src/
|   |--- backtester/    # Phase 1 (complete)
|   |--- recorder/      # live data recorders (run 24/7)
|   |--- replay/        # Phase 2 deterministic L2 replay engine
|   |--- execution/     # Phase 2 execution simulator (complete)
|   |--- strategies/    # Phase 2 baseline MM strategies
|   `--- analysis/      # Phase 2 markout and P&L analysis
|--- tests/             # deterministic test suite
|--- scripts/           # runnable analysis scripts
|--- notebooks/         # research_log.md, error_analysis.md, lessons_learned.md
|--- data/
|   |--- bars/          # OHLCV parquet files
|   `--- raw/           # recorded L2 data (gitignored, local only)
`--- results/           # selected research artifacts; generated outputs are gitignored
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

**Phase 1 - run a backtest:**
```bash
python scripts/download_bars.py
python scripts/compare_strategies.py
python scripts/walk_forward.py
```

**Run deterministic tests:**
```bash
env PYTHONPATH=. pytest -q tests --ignore=tests/test_recorder.py
```

### Reproducibility checklist

Use the `l2mm` conda environment from the repository root:

```bash
conda activate l2mm
cd l2-mm-system
```

Deterministic suite:

```bash
env PYTHONPATH=. pytest -q tests --ignore=tests/test_recorder.py
```

V2 artifact verifier:

```bash
env PYTHONPATH=. python scripts/verify_v2_artifacts.py
```

`tests/test_recorder.py` is a live network/recorder test and is intentionally excluded from the deterministic suite. Remote CI remains pending authenticated verification for this private repository; no green remote run ID is claimed here.

Reproduce the V2 development research path:

```bash
env PYTHONPATH=. python scripts/run_l2_panel.py --phase a
env PYTHONPATH=. python scripts/run_l2_panel.py --phase b
env PYTHONPATH=. python scripts/sweep_queue_credit.py
env PYTHONPATH=. python scripts/summarize_queue_credit_sweep.py \
  --runs-csv results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/queue_credit_sweep_runs.csv \
  --output-root results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary
```

Primary writeup and artifacts:

- `notebooks/research_writeup_v2.md`
- `notebooks/phase2_artifact_index.md`
- `notebooks/research_log.md`
- `results/panels/btcusdt_l2_panel_v2/phase_a_verdict.json`
- `results/panels/btcusdt_l2_panel_v2/ofi_signal/`
- `results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary/`

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

**Depth before trade on equal timestamps.** When a depth diff and a trade share the same millisecond, the depth event is processed first. The book update reflects the state after the matching engine executed that trade - applying it first gives the strategy the correct view of the book before dispatching the trade.

**L2 queue position is an approximation.** L2 data shows aggregated volume per price level, not individual orders. The model places our order at the back of the queue at arrival, drains the front using trade events, and applies proportional shrinkage for cancellations. This is the standard industry approximation.

**Strategies submit `OrderRequest` objects, not `Order` objects.** The strategy expresses intent (side, type, price, qty). The simulator owns the lifecycle - it applies latency, assigns queue position, and manages fills. This keeps strategy logic clean and independent of execution mechanics.

**`FeeConfig` from Phase 1 uses float; `SimConfig` in Phase 2 uses `Decimal`.** They are intentionally separate. The bar backtester operates at a coarseness where float precision is irrelevant. The L2 simulator accumulates fees across thousands of fills where it is not.
