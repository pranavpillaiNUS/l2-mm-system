# Passive Market Making on BTCUSDT L2 Data: A Population Signal That Failed the Passive-Fill Gate

A deterministic L2 order-book replay and execution simulator for studying passive
market making on real Binance spot BTCUSDT data. The central result is a contrast
between a population-level association and the fill-conditioned gate, not a
profitable strategy or an identified causal effect. This note is the short
version. The full protocol and per-phase detail are in
`notebooks/research_writeup_v2.md`, and every headline number reconciles against the
artifacts listed at the end.

**Provenance and current status.** This note reports the frozen V2 artifact set
generated at research commit `1066950` under execution model
`legacy_book_update_v1`. In that model, a modeled order arrival became active
on the next depth update and a cancellation request took effect immediately.
The numbers below remain historical results for that specified experiment. They
are not results from the current engine. The Phase 2.5 `event_driven_v2`
Python implementation has passed local acceptance, documented in
[`notebooks/execution_model_v2.md`](execution_model_v2.md). Its results belong
only under `results/panels/btcusdt_l2_panel_v3_event_driven`. Its development
rerun under `post_response_proxy_depth_boundary_v1` is pending, and no V3 execution-derived
conclusion has been published or validated yet. The C++ performance port is
deferred until the Python reference is frozen and a separate modern C++
learning phase is complete.

## Headline finding

Order flow imbalance (OFI) was strongly associated with one-second forward mid-price movement
across the population of book states: pooled HAC (Newey-West) `t = 64.6`, a positive
1s coefficient in all 24 of 24 development windows (per-window `|t|` from 4.48 to
26.90), about `+0.123 bps` per signal standard deviation, and a clean monotone bucket
dose-response from `-0.276 bps` to `+0.284 bps`. This is a strong, sign-stable
association within the selected development panel.

The pre-specified fill-conditioned screen did not show the same robust monotone
separation. Its defined 30s comparison was `+0.127 bps` under proportional queue
credit and `-0.376 bps` under no credit, both far below the heuristic `1.0 bps`
materiality bar. The selected bucket counts (`n = 201` and `154`) exceeded the
predefined minimum of 30. That is not a formal power analysis. The complete
conditional response was non-monotone, including a smaller intermediate bucket
with a more favorable mean. Under the frozen protocol, the premise gate failed,
so running `OFIGatedMM` was not justified. This is a decision about candidate
advancement, not a claim that OFI universally disappears after fills or that the
unrun strategy would necessarily lose: suppressing one quote side would change
the fill set. The result is consistent with adverse selection of the baseline
fill sample. Signal existence does not imply edge under passive execution.

## System and data

The replay engine reconstructs the L2 book from recorded snapshots and diffs and
orders events on a modeled clock: ordinary depth diffs use Binance event time
`E` and trades use matching time `T`. REST snapshots have no exchange timestamp.
The legacy recorder tagged them before the blocking request completed, and
frozen V2 replay applied the returned state at that early tag. Current replay
emits a fail-closed barrier at recorded local request time, requires a
sequence-valid retained depth receipt after the recorded response (or a legacy
upper-bound proxy), and releases only the final reconstructed state at that
record's `E`. This is a post-response-gated exchange-clock proxy, not a
calibrated observation clock. `E`, `T`, and local recorder observations have
different semantics and uncalibrated latency. The replay is deterministic by
construction: `Decimal` prices and quantities (no float), a seeded PRNG for
latency jitter, no runtime clock reads, and SHA-256 state hashing at checkpoints. The
execution simulator models post-only limit behavior, configurable latency, a FIFO
queue derived from visible L2 aggregate size, partial fills, and maker/taker fee
assignment, with an explicit `queue_cancellation_credit` lever in `[0, 1]` for how much
queue position improves when displayed size falls without a matching trade. Venue is
Binance spot BTCUSDT. The development panel is 24 non-overlapping five-hour windows,
selected from a strict frozen hourly integrity manifest. Queue position is the
first-order model risk and is treated as such throughout.

## Frozen V2 evidence

Baseline (Phase A): a passive microprice strategy quoting a `$2` half-spread (about
`0.28 bps` from the reference at a representative `$71.5k` price level), 5s requote, 2
bps maker fee. Across the 24 windows the baseline is net-negative in 18 of 24 at each
queue endpoint. Under the proportional queue model the window-level mean net
PnL is `-1.04` with 95% bootstrap CI `[-2.33, -0.03]`, which excludes zero. Under no
credit it is `-0.69`, CI `[-1.88, +0.29]`, which crosses zero. The two endpoints
classify differently, so the headline is `Conditional V2: queue-model-dependent`, with
the conservative passive-edge verdict `Strengthens V1`. Matched round-trip and
residual-inventory intervals each cross zero at both endpoints, full-strategy PnL
combines matched economics, fees, and terminal inventory marking, so no single
component is assigned as the causal driver.

That fee/spread comparison is a central part of the result: pooled completed
lots would break even at maker fees of `1.351 bps` with no queue credit and
`0.426 bps` with proportional credit, below the modeled `2 bps`. Each nominal
five-hour result also sums five independently initialized one-hour episodes.
Inventory is marked to the hourly session-end mid and then reset without a
modeled liquidation, so full-strategy PnL is not a continuous five-hour
portfolio path and the residual component is not assigned as a causal loss
mechanism.

The frozen result also carries the legacy snapshot-time defect described above.
Across all 120 selected development hours, the first later local depth receipt
followed the pre-fetch tag by a median `250.5 ms`, p90 `672.9 ms`, and maximum
`2,335 ms`. Two no-credit and four proportional-credit fills came from orders
placed before the selected policy boundary, which equaled the valid bridge in
all 120 files. These are proxy diagnostics, not upper bounds or proof that the
V2 result is invariant, queue age can affect later fills. The V3 development
rerun must remeasure the complete result under the new policy.

Microprice premise: the strategy's own assumption, that microprice deviation predicts
forward mid drift, is weak. Pooled 1s predictiveness is `+0.019 bps` per signal
standard deviation, HAC `t = +1.74`, positive in 19 of 24 windows, failing both the
significance and the economic-size bars. The conditional-on-fill version did not rescue
it either. So before OFI, the project had already shown that the baseline's premise is
not a reliable standalone edge.

OFI premise: the headline above. The strong unconditional signal is the reason OFI was
worth testing as a gate. The conditional-on-fill failure is the reason the OFI-gated
candidate (`OFIGatedMM`) never advanced.

## Robustness and discipline

Corrections removed contaminated positive-looking outputs before the frozen
study was interpreted:

- Post-only artifact. The first profitable-looking runs were contaminated: latency
  turned intended passive limits into taker fills. Enforcing post-only made the
  simulation honest and the apparent edge disappeared.
- Replay correctness. A timestamp/snapshot synchronization bug was found and fixed. The
  affected studies were regenerated. A separate six-anchor trade-gap policy audit
  reports exact zero deltas.

Within the corrected frozen `legacy_book_update_v1` boundary, subsequent
bounded checks did not change the negative/conditional decision:

- Leakage hardening. The conditional OFI test was tightened to strictly-pre-fill
  samples with a shuffle tripwire that requires a near-zero statistic when the
  signal-to-outcome pairing is destroyed. The separation barely moved (`+0.126 ->
  +0.127`, `-0.379 -> -0.376`) and no status changed. The bounded artifact audit
  found no evidenced contradiction, but it cannot resolve every within-ms queue
  attribution after the fact.
- Queue and latency stress (Phase C). Matched net PnL per BTC is negative across the
  entire cancellation-credit grid, `-9.75` at credit `0.0` worsening monotonically to
  `-23.65` at credit `1.0`. The frozen 0/10/50 ms sweep did not change the
  simulated fill set because the legacy model activated orders only on the next
  depth update. This exposes timing quantization and motivates the V3 rerun. It is
  not evidence that real latency is irrelevant.

Inference discipline is consistent throughout: full-strategy, matched-lot, and
residual-inventory PnL are separated and never conflated. The five-hour-window
bootstrap is headlined while session and correlated matched-lot intervals are treated as a
diagnostic, statistical significance is always reported with economic magnitude (`beta
* signal_std`), thin conditional buckets are labeled `inconclusive_power` rather than
forced into pass/fail, and, at frozen closure, determinism was checked by a 223-test
deterministic suite and state hashing. Those checks establish repeatability of that code
path. They do not validate the legacy timing assumptions or substitute for the V3
rerun.

## Related work and scope

The measured mechanisms are established market-microstructure ideas, not a claim of
theoretical novelty. Glosten and Milgrom frame bid-ask spreads as compensation for
trading against better-informed flow, Cont, Kukanov, and Stoikov connect order-flow
imbalance to short-horizon price impact, and Avellaneda and Stoikov formulate the
inventory-aware market-making control problem that this project deliberately did not
add after the development gate failed. The contribution here is the deterministic
measurement pipeline, explicitly modeled execution conditioning, and pre-specified
decision rule applied to real BTCUSDT data. The execution assumptions are material and
versioned rather than presented as exchange truth.

- Lawrence R. Glosten and Paul R. Milgrom (1985), ["Bid, Ask and Transaction
  Prices in a Specialist Market with Heterogeneously Informed
  Traders"](https://doi.org/10.1016/0304-405X(85)90044-3).
- Rama Cont, Arseniy Kukanov, and Sasha Stoikov (2014), ["The Price Impact of
  Order Book Events"](https://doi.org/10.1093/jjfinec/nbt003).
- Marco Avellaneda and Sasha Stoikov (2008), ["High-frequency Trading in a
  Limit Order Book"](https://doi.org/10.1080/14697680701381228).

## Limitations and conclusion

The completed evidence uses one instrument (Binance spot BTCUSDT) and 24 five-hour
development windows from April to May 2026. A frozen 12-window holdout, labeled
`regime-shifted` by a pre-strategy comparability screen, remained strategy-sealed
because no candidate passed the development gate. Its identities and regime
descriptors were examined, but no candidate strategy was evaluated there. The queue
model is the load-bearing assumption: the negative
matched-PnL sign was robust across the credit grid, but throughput, average fill
quality, and loss magnitude are model-sensitive, so no strong positive claim about fill
economics is made. The `$2` quote distance is small (sub-basis-point), and the results
are specific to that configuration.

The disciplined frozen-V2 conclusion was to publish the negative rather than advance a
candidate. A strong population-level signal that failed this passive-maker premise
gate under a documented execution approximation is more informative than a fragile
positive built on contaminated assumptions. Whether the execution-derived magnitudes
and gate outcome persist under `event_driven_v2` is a Phase 2.5/V3 question, not a
claim made by this note.

## Frozen V2 claim-to-artifact map

| Claim | Artifact |
|---|---|
| Phase A proportional window CI (`-1.04`, `[-2.33, -0.03]`) | `results/panels/btcusdt_l2_panel_v2/phase_a_verdict.json` |
| OFI pooled 1s HAC `t = 64.6`, 24/24 same-sign, `+0.123 bps/sigma` | `results/panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms/summary.json` |
| Conditional separations (`+0.127` proportional, `-0.376` none) | same OFI summary plus the `..._qc0/summary.json` sibling |
| Phase C matched net per BTC (`-9.75` to `-23.65`) | `results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary/queue_credit_summary.csv` |
| Panel sizes and frozen hashes (24 dev, 12 holdout) | `results/panels/btcusdt_l2_panel_v2/window_selection_summary.json` (manifest `a3a99b0a...`, panel `760c55b7...`) |
