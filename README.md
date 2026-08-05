# L2 Market Microstructure System

[![Tests](https://github.com/pranavpillaiNUS/l2-mm-system/actions/workflows/tests.yml/badge.svg)](https://github.com/pranavpillaiNUS/l2-mm-system/actions/workflows/tests.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB.svg)](https://www.python.org/downloads/release/python-3110/)
[![Technical report](https://img.shields.io/badge/report-PDF-B30B00.svg)](report/l2_mm_research_report.pdf)

A Python research system for deterministic Binance spot L2 reconstruction,
event-driven execution simulation, and market-making hypothesis testing. It was
built to answer a specific microstructure question: does a signal that predicts
the next mid-price move remain useful after passive execution selects which
states become fills?

The project currently ends at a tested Python behavioral reference. The next
milestone is a 24-window development rerun under the event-driven model and its
post-response-gated snapshot policy. The C++ performance port is intentionally
deferred until that result is frozen and a separate modern C++ learning phase
is complete.

**Start here:** [technical report](report/l2_mm_research_report.pdf) ·
[data and reproducibility](DATA.md) · [result map](results/README.md) ·
[execution-model contract](notebooks/execution_model_v2.md)

## Current result

All economic numbers below are frozen historical evidence from
`legacy_book_update_v1`, not results from the current simulator.

Normalized one-second order-flow imbalance (OFI) had a strong population-level
association with forward mid drift: HAC `t = 64.63`, positive coefficients in
24/24 selected development windows, and `+0.1233 bps` per signal standard
deviation. The relationship was monotone across unconditional OFI buckets.

The pre-specified fill-conditioned screen nevertheless failed at both queue
endpoints. Its 30-second side-aligned separation was only `+0.127 bps` with
proportional cancellation credit and `-0.376 bps` with no credit, versus a
heuristic `1.0 bps` threshold. The complete conditional bucket pattern was
non-monotone. The defensible conclusion is therefore narrow: the defined gate
did not provide robust enough evidence to run the OFI-gated candidate. It does
not show that OFI is universally useless after fills, or that the unrun strategy
would necessarily lose.

![Population and fill-conditioned OFI evidence](report/figures/ofi_population_vs_fills.png)

The candidate did not advance, and no candidate strategy was evaluated on the
12-window holdout. Holdout identities and pre-strategy regime descriptors were
examined, so it is **strategy-sealed**, not unseen.

## Project status

| Layer | Status | What can be claimed |
|---|---|---|
| Bar backtester | Educational precursor | Reusable interfaces and lessons; historical performance ratios are not treated as rigorous evidence |
| Frozen L2 study (V2 artifacts) | Complete | Auditable negative/conditional development result under `legacy_book_update_v1` |
| Event-driven Python model | Mechanics accepted | Exact private scheduling on the model clock, post-response-gated snapshot recovery, delayed cancels, own FIFO, volume conservation, gap handling, and risk invariants |
| V3 development economics | Pending | No current-model fill, P&L, markout, queue, or conditional-signal result exists yet |
| Holdout strategy evaluation | Not run | No candidate cleared development |
| C++ performance port | Paused | Begins after Python V3 is frozen and modern C++ fundamentals are in place |

This version boundary is deliberate. The historical simulator activated an
order on the next depth update after modeled arrival and cancelled immediately
on request. The current `event_driven_v2` model schedules entries and cancels on
one millisecond clock, gates snapshot recovery on a post-response depth-receipt
boundary, models cancel/fill races, and writes only to a separate V3
namespace. Historical and current-model economics are never combined.

## What is implemented

The core flow is:

```text
depth snapshots/diffs + aggregate trades
                    |
           gap-aware parsers
                    |
     deterministic timestamp merger
                    |
          Decimal L2 order book
                    |
   replay engine <-> strategy intent
                    |
 event-driven execution + private scheduler
                    |
 P&L / markout / OFI / queue / tail analysis
                    |
       versioned, hashed artifacts
```

Engineering highlights:

- Snapshot bridging, stale-diff rejection, cross-file depth continuity, and
  aggregate-trade gap detection. Snapshot reconstruction is released only at a
  sequence-valid depth record known to follow response completion, or a
  defensible upper-bound proxy for legacy captures; only the final recovered
  state reaches strategy logic.
- Explicit equal-time rule: all recorded market data at a millisecond is
  processed before equal-time private arrivals.
- Post-only arrival checks, maker/taker fee assignment, partial fills, separate
  entry/cancel latency, and explicit late-cancel outcomes.
- Public queue plus non-overlapping own-order FIFO; aggregate simulated maker
  fills cannot exceed recorded aggressor volume.
- Fail-closed gap handling and snapshot resynchronization.
- Hard worst-case working-exposure limit across active orders, pending entries,
  and cancels in flight.
- `Decimal` accounting, seeded randomness, canonical state hashes, provenance
  guards, and separate frozen/current result roots.
- Window-level bootstrap inference, Newey-West standard errors, strict
  pre-fill signal anchoring, decision gates, and a lightweight artifact verifier.

The implementation is a research approximation over aggregate L2, not an
exchange matching-engine replica or a production trading system.

## Frozen evidence at a glance

The V2 development panel contains 24 non-overlapping five-hour windows from
April–May 2026. Six were exploratory anchors; the other 18 were selected later
from a frozen integrity inventory. This is an expanded development study, not a
pristine confirmation sample.

| Finding | Frozen legacy result | Interpretation |
|---|---:|---|
| Baseline mean net P&L, no queue credit | `-0.686 USDT/window`, 95% CI `[-1.883, +0.290]` | Interval crosses zero |
| Baseline mean net P&L, proportional credit | `-1.043 USDT/window`, 95% CI `[-2.332, -0.032]` | Negative under this endpoint |
| Microprice, 1s effect | `+0.0190 bps/s.d.`, HAC `t = 1.74` | Missed significance and size screens |
| Normalized OFI, 1s effect | `+0.1233 bps/s.d.`, HAC `t = 64.63` | Strong population association; low `R² = 0.0366` |
| OFI fill-conditioned separation | `+0.127 / -0.376 bps` | Defined gate failed; response non-monotone |
| Queue-credit stress, matched net/BTC | `-9.75` to `-23.65 USDT/BTC` | More modeled queue credit increased fills but worsened economics |

The baseline uses 0.001 BTC quotes, a 0.01 BTC maximum position, a 2 USDT
half-spread, a five-second requote interval, 10 ms modeled entry latency, and a
2 bps modeled maker fee. These are experimental assumptions, not measured
account parameters.

At a representative 71,500 USDT price, that half-spread is about 0.28 bps per
side, materially below the 2 bps fee per maker fill. The pooled completed-lot
break-even maker fee was 1.351 bps with no queue credit and 0.426 bps with
proportional credit. Fees are therefore a structural hurdle, not a footnote.

A publication audit found one additional historical timing defect: the legacy
recorder tagged each REST snapshot before the blocking request completed, and
V2 replay applied that state at the early tag. Across the 120 selected
development hours, the first later local depth receipt followed the tag by a
median 250.5 ms (90th percentile 672.9 ms; maximum 2,335 ms). Two no-credit
and four proportional-credit frozen fills came from orders placed before the
selected policy boundary. This is a proxy diagnostic, not an upper bound or a
robustness result; later queue age can still be affected. Future captures record
request and response times separately. V3 will use the versioned
`post_response_proxy_depth_boundary_v1` policy and remeasure the entire
development panel.
The audit is committed at
[`snapshot_timing_panel24.json`](results/replay_correctness/snapshot_timing_panel24.json).

## Quick verification

Python 3.11 is the supported environment. These commands do not require raw
market data:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

env PYTHONPATH=. pytest -q
env PYTHONPATH=. python scripts/demo_event_driven_execution.py
env PYTHONPATH=. python scripts/verify_v2_artifacts.py
```

A fresh clone runs 376 deterministic tests and explicitly skips three optional
raw-data smoke checks. With the selected captures restored, all 379 pass. The
synthetic demo shows an order arriving, a cancellation being requested, a trade
filling the order, and the delayed cancellation losing the race:

```text
0ms:placed -> 5ms:arrived -> 5ms:queued
6ms:cancel_requested -> 10ms:filled -> 11ms:cancel_too_late
```

The demo checks lifecycle mechanics only. The V2 verifier checks committed
identities, artifact shapes, verdict fields, model separation, and the absence
of candidate holdout outputs. Neither command recomputes empirical results.

## Reproducibility boundary

There are three levels of reproduction:

1. **Clone-level:** run deterministic tests and the synthetic execution demo.
2. **Artifact-level:** validate the selected committed V2 summaries and rebuild
   the report figures and PDF.
3. **Full replay:** restore the selected raw files whose individual hashes are
   recorded in the integrity manifest. The 24-window development subset is 240
   spot files (approximately 0.82 GB locally); the complete local capture is
   approximately 37 GB and also includes unused perpetual-futures recordings.

Raw market data is intentionally excluded from Git. See [DATA.md](DATA.md) for
schemas, directory layout, integrity rules, missingness, and exact boundaries.

To build the report from committed artifacts:

```bash
make report
make report-check
```

The report build is pinned in CI to Tectonic 0.17.0. `report-check` also uses
Poppler (`pdfinfo`, `pdffonts`, and `pdftotext`) and uses `qpdf` when available.

## Repository map

| Path | Purpose |
|---|---|
| [`src/replay/`](src/replay/) | L2 book, depth/trade parsers, event merge, replay clock |
| [`src/execution/`](src/execution/) | Orders, private scheduler, queue/FIFO model, fills and fees |
| [`src/strategies/`](src/strategies/) | Symmetric, microprice, and blocked OFI-gated quoting logic |
| [`src/analysis/`](src/analysis/) | P&L, markouts, signals, uncertainty, gates and diagnostics |
| [`scripts/`](scripts/) | Recording, replay, panel orchestration, analysis and verification |
| [`tests/`](tests/) | Deterministic unit, invariant, integration and artifact-contract tests |
| [`results/panels/`](results/panels/) | Canonical selected derived artifacts by model namespace |
| [`notebooks/`](notebooks/) | Frozen protocols, historical writeups, research log and model note |
| [`report/`](report/) | LaTeX source, generated figures and compiled technical report |

The root `results/` tree also contains exploratory and superseded outputs. Use
the [result map](results/README.md) before citing a file.

## Known limits

- One venue, one instrument, and a short, clustered research period.
- Each nominal five-hour result sums five independently initialized one-hour
  episodes. Inventory is marked to each session-end mid and then reset without
  modeled liquidation cost; it is not continuous five-hour portfolio P&L.
- Aggregate L2 rather than market-by-order data; true queue rank is unobserved.
- Uncalibrated queue-cancellation credit and unresolved within-millisecond order.
- Frozen V2 snapshots used a pre-fetch local tag. Current replay emits a
  fail-closed resync barrier at recorded local request time, gates recovery on
  post-response receipt order, releases recovered depth on `E`, and keeps trades
  on `T`. This mixed modeled clock is not a calibrated client-observation clock;
  the audit cannot rule out changed queue age or execution eligibility.
- No separate market-data latency, live acknowledgement/fill calibration,
  endogenous impact, hidden liquidity, or account-specific fee tier.
- Recording availability may not be random: 775 of 1,193 inventoried hours met
  all eligibility rules.
- No clustered confidence interval yet for the fill-conditioned OFI contrast.
- No order gateway, kill switch, monitoring, or other production controls.

The full limitations and failure analysis are in the
[technical report](report/l2_mm_research_report.pdf).

## Next milestone

1. Run the fixed baseline on the same 24 development windows under
   `event_driven_v2` and `post_response_proxy_depth_boundary_v1` at both queue
   endpoints.
2. Regenerate conditional OFI and queue/latency diagnostics under that model.
3. Decide from a newly committed development rule whether any candidate earns a
   one-shot holdout evaluation.
4. Freeze the Python reference, golden traces, artifact schema, and parity
   tolerances.
5. Freeze the scope at the Python reference, then complete a modern C++
   fundamentals phase before starting any performance port.

The project makes no claim of live profitability, production readiness, or
exchange-validated execution.
