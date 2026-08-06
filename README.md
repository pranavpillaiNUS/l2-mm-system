# L2 Market Microstructure System

[![Tests](https://github.com/pranavpillaiNUS/l2-mm-system/actions/workflows/tests.yml/badge.svg)](https://github.com/pranavpillaiNUS/l2-mm-system/actions/workflows/tests.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB.svg)](https://www.python.org/downloads/release/python-3110/)
[![Technical report](https://img.shields.io/badge/report-PDF-B30B00.svg)](report/l2_mm_research_report.pdf)

A deterministic Python research system for reconstructing Binance BTCUSDT spot
L2 order books, replaying market data, and simulating passive execution.

> **Research question:** Does a signal that predicts the next mid-price move
> remain useful after passive execution selects which states become fills?

**Author:** [Pranav Pillai](https://github.com/pranavpillaiNUS) · National
University of Singapore

## At a glance

- **System:** gap-aware L2 reconstruction, deterministic event merging, an
  event-driven replay clock, passive-order execution, and versioned research
  artifacts.
- **Execution model:** explicit entry and cancel latency, post-only arrival
  checks, partial fills, own-order FIFO, cancel/fill races, fee accounting, and
  worst-case working-exposure limits.
- **Research result:** strong population-level one-second OFI association with
  forward mid-price drift, but a failed pre-specified passive-fill screen for
  the tested market-making baseline.
- **Verification:** `376 passed, 3 skipped` from a public clone; `379 passed`
  with the selected raw captures restored; report generation is checked in CI.
- **Current status:** Python mechanics are implemented and tested. The corrected
  24-window V3 development rerun is the next empirical milestone; the C++
  performance port remains deferred until the Python reference is frozen.

## Execution in one trace

The synthetic demo captures the private-order lifecycle and a cancel/fill race:

```text
0ms:placed -> 5ms:arrived -> 5ms:queued
6ms:cancel_requested -> 10ms:filled -> 11ms:cancel_too_late
```

The order reaches the book after modeled latency, joins the queue, receives a
cancel request, fills against recorded aggressor volume, and makes the delayed
cancel ineffective. This timing behavior is covered by the deterministic test
suite.

## Project status

| Layer | Status | What is established |
|---|---|---|
| Bar backtester | Educational precursor | Reusable interfaces, testing patterns, and research lessons |
| Frozen L2 study | Complete | Auditable 24-window development evidence under the historical execution model |
| Event-driven Python model | Implemented and tested | Deterministic private-event scheduling on the model clock, post-response-gated snapshot recovery, delayed cancels, own FIFO, volume conservation, gap handling, and risk invariants |
| V3 development economics | Pending | The 24-window rerun will produce the first current-model fill, P&L, markout, queue, and conditional-signal estimates |
| Holdout strategy evaluation | Strategy-sealed | Reserved for one candidate that clears development and enters with a locked protocol |
| C++ performance port | Deferred | Begins after Python V3 is frozen and modern C++ fundamentals are in place |

The frozen study and current simulator use distinct execution models. The
historical `legacy_book_update_v1` engine activated arrivals on the next depth
update and made cancellation requests effective immediately. The current
`event_driven_v2` engine schedules entries and cancels on the model clock,
models cancel/fill races, conserves aggressor volume across own FIFO, and gates
snapshot recovery on a post-response depth-receipt boundary. Each model writes
to its own artifact namespace.

The pre-rerun hypotheses are that the pooled population OFI association remains
positive and that the fill-conditioned separation remains below the existing
`1.0 bps` bar at both queue endpoints. The scheduler correction does not enter
the population OFI calculation, although the snapshot policy does; the
fill-conditioned result is directly sensitive to both changes because they can
alter the fill set nonlinearly. These prospective hypotheses will be tested
under a decision rule committed before the rerun.

**Audit history:** The [error analysis](notebooks/error_analysis.md) records
successive corrections to post-only enforcement, UTC and stale-diff replay,
private-event scheduling, and snapshot-response timing. Each correction either
regenerated affected evidence or established an explicit model/version
boundary.

## Research findings

The frozen V2 development panel contains 24 non-overlapping five-hour windows
from April–May 2026. It combines six exploratory anchors with 18 later windows
selected from a frozen integrity inventory; all 24 windows are treated as
development evidence.

The population and fill-conditioned views diverged. Normalized one-second OFI
had a positive coefficient in all 24 windows, while the 30-second
fill-conditioned response was non-monotone and failed the pre-specified
`1.0 bps` screen at both queue endpoints.

| Finding | Frozen result | Interpretation |
|---|---:|---|
| Baseline mean net P&L, no queue credit | `-0.686 USDT/window`, 95% CI `[-1.883, +0.290]` | Interval crosses zero |
| Baseline mean net P&L, proportional credit | `-1.043 USDT/window`, 95% CI `[-2.332, -0.032]` | Negative under this endpoint |
| Microprice, 1s effect | `+0.0190 bps/s.d.`, HAC `t = 1.74` | Missed significance and size screens |
| Normalized OFI, 1s effect | `+0.1233 bps/s.d.`, HAC `t = 64.63` | Strong population association; low `R² = 0.0366` |
| OFI fill-conditioned separation | `+0.127 bps` proportional / `-0.376 bps` no credit | Defined gate failed; response non-monotone |
| Queue-credit stress, matched net/BTC | `-9.75` to `-23.65 USDT/BTC` | More modeled queue credit increased fills but worsened economics |

![Population and fill-conditioned OFI evidence](report/figures/ofi_population_vs_fills.png)

The frozen protocol therefore blocked `OFIGatedMM` before strategy or holdout
evaluation. This establishes a strong historical population association and a
failed passive-fill screen for this baseline; other signal transforms and the
performance of the unrun candidate remain separate hypotheses. The 12-window
holdout is strategy-sealed: its identities and pre-strategy regime descriptors
are public, while strategy outcomes remain reserved for one
development-qualified, locked candidate.

The baseline's experimental parameterization uses 0.001 BTC quotes, a 0.01 BTC
maximum position, a 2 USDT half-spread, a five-second requote interval, 10 ms
modeled entry latency, and a 2 bps modeled maker fee. At a representative
71,500 USDT price, that half-spread is about 0.28 bps per side. The pooled
completed-lot break-even maker fee was 1.351 bps with no queue credit and 0.426
bps with proportional credit, making fees a structural hurdle in this
experiment.

These measurements were generated by `legacy_book_update_v1`. A publication
audit subsequently found that the legacy recorder tagged each REST snapshot
before its blocking request completed and replay applied the returned state at
that early tag. Across the 120 selected development hours, the first later local
depth receipt followed the tag by a median 250.5 ms (90th percentile 672.9 ms;
maximum 2,335 ms). Two no-credit and four proportional-credit frozen fills came
from orders placed before the selected policy boundary.

The committed
[`snapshot timing audit`](results/replay_correctness/snapshot_timing_panel24.json)
measures this observable exposure. V3 will use
`post_response_proxy_depth_boundary_v1` and will remeasure the full development
panel, including the later queue-age and execution effects.

## System design

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

The implementation is an offline research simulator over aggregate L2. It
estimates passive execution under explicit queue, timing, fee, and data
assumptions; exchange replication and live-trading controls are outside its
scope.

## Quick verification

Python 3.11 is the supported environment. Clone-level verification uses
committed and synthetic inputs:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps --editable ".[analysis,dev]"

env PYTHONPATH=. pytest -q
env PYTHONPATH=. python scripts/demo_event_driven_execution.py
env PYTHONPATH=. python scripts/verify_v2_artifacts.py
```

A fresh public clone reports `376 passed, 3 skipped`; restoring the selected raw
captures activates the three smoke checks and yields `379 passed`. The demo
verifies execution lifecycle mechanics. The artifact verifier checks committed
identities, artifact shapes, verdict fields, model separation, and the
strategy-sealed holdout boundary.

## Documentation and reproducibility

The [technical report](report/l2_mm_research_report.pdf) contains the complete
methodology, protocols, results, and failure analysis. Supporting references:
[data specification](DATA.md) · [result map](results/README.md) ·
[execution-model contract](notebooks/execution_model_v2.md).

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

## Repository guide

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

## Evidence boundaries

- **Dataset:** one venue, one instrument, and a short, clustered April–May 2026
  research period.
- **Episode construction:** each nominal five-hour result sums five independently
  initialized one-hour episodes. Inventory is marked to session-end mid and
  reset with liquidation cost omitted, so the metric aggregates episodes rather
  than one continuous portfolio path.
- **Queue inference:** aggregate L2 exposes displayed quantity rather than order
  identities; true queue rank, hidden liquidity, and within-level order changes
  remain latent.
- **Event ordering:** queue-cancellation credit is uncalibrated. The model
  processes market data before private actions at equal timestamps, while true
  within-millisecond exchange ordering remains unobservable.
- **Snapshot clock:** V2 used a pre-fetch local tag. Current recovery combines a
  request-time barrier, post-response receipt order, depth event time `E`, and
  trade time `T`. This is an exchange-clock proxy; client-observation latency
  remains uncalibrated, and V3 will remeasure queue age and execution eligibility.
- **Execution calibration:** the model omits separate market-data latency, live
  acknowledgement and fill calibration, endogenous impact, account-specific
  fee tiers, and hidden liquidity.
- **Selection:** 775 of 1,193 inventoried hours met every eligibility rule, and
  recording availability may be non-random.
- **Inference:** a clustered confidence interval for the fill-conditioned OFI
  contrast remains pending.

## Next milestone

1. Commit the V3 development protocol and decision rule before inspecting rerun
   results.
2. Run the corrected baseline over the same 24 development windows at both
   queue endpoints.
3. Regenerate fill, P&L, markout, queue, latency, and conditional-OFI evidence,
   then apply the committed gate.
4. Evaluate the strategy-sealed holdout once only if a locked candidate
   qualifies in development.
5. Freeze the Python reference, artifact schema, golden traces, and parity
   tolerances.

The project then pauses at the tested Python reference while modern C++
fundamentals are developed. A performance port begins only after that learning
phase.
