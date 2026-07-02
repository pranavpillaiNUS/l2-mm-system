# Passive Market Making on BTCUSDT L2 Data: A Signal This Passive Maker Couldn't Harvest

A deterministic L2 order-book replay and execution simulator for studying passive
market making on real Binance spot BTCUSDT data. The central result is a
microstructure selection effect, not a profitable strategy. This note is the short
version; the full protocol and per-phase detail are in
`notebooks/research_writeup_v2.md`, and every headline number reconciles against the
artifacts listed at the end.

## Headline finding

Order flow imbalance (OFI) strongly predicted one-second forward mid-price movement
across the population of book states: pooled HAC (Newey-West) `t = 64.6`, a positive
1s coefficient in all 24 of 24 development windows (per-window `|t|` from 4.48 to
26.90), about `+0.123 bps` per signal standard deviation, and a clean monotone bucket
dose-response from `-0.276 bps` to `+0.284 bps`. By the standards of this data that is
an unusually strong, stable signal.

But that predictive content did not survive conditioning on the passive fills the
simulated maker actually received. The 30s side-aligned separation between
favorable-OFI and adverse-OFI fills was `+0.127 bps` under proportional queue credit
and `-0.376 bps` under no credit, both far below the pre-registered `1.0 bps`
materiality bar and both well powered (smallest bucket `n = 201` and `154`). Under the
frozen protocol, this premise test failed, so running `OFIGatedMM` was not justified.
That is a decision about candidate advancement, not a counterfactual claim that the
unrun strategy would necessarily lose: suppressing one quote side would change the
fill set. The observed conditional failure is consistent with adverse selection of the
baseline fill sample. The events that fill a resting quote are not a representative
draw from the book states where OFI is predictive, so signal existence does not imply
edge once execution conditioning is applied honestly.

## System and data

The replay engine reconstructs the L2 book from recorded snapshots and diffs and
replays trades and book updates in exchange-timestamp order. It is deterministic by
construction: `Decimal` prices and quantities (no float), a seeded PRNG for latency
jitter, no wall-clock dependence, and SHA-256 state hashing at checkpoints. The
execution simulator models post-only limit behavior, configurable latency, a FIFO
queue derived from visible L2 aggregate size, partial fills, and maker/taker fee
assignment, with an explicit `queue_cancellation_credit` lever in `[0, 1]` for how much
queue position improves when displayed size falls without a matching trade. Venue is
Binance spot BTCUSDT. The development panel is 24 non-overlapping five-hour windows,
selected from a strict frozen hourly integrity manifest. Queue position is the
first-order model risk and is treated as such throughout.

## Evidence

Baseline (Phase A): a passive microprice strategy quoting a `$2` half-spread (about
`0.28 bps` from the reference at a representative `$71.5k` price level), 5s requote, 2
bps maker fee. Across the 24 windows the baseline is net-negative in 18 of 24 at each
queue endpoint. Under the realistic proportional queue model the window-level mean net
PnL is `-1.04` with 95% bootstrap CI `[-2.33, -0.03]`, which excludes zero; under no
credit it is `-0.69`, CI `[-1.88, +0.29]`, which crosses zero. The two endpoints
classify differently, so the headline is `Conditional V2: queue-model-dependent`, with
the conservative passive-edge verdict `Strengthens V1`. Matched round-trip PnL crosses
zero at both endpoints, so the firmer full-strategy loss is driven by residual
inventory marked at the window close, not by completed round trips.

Microprice premise: the strategy's own assumption, that microprice deviation predicts
forward mid drift, is weak. Pooled 1s predictiveness is `+0.019 bps` per signal
standard deviation, HAC `t = +1.74`, positive in 19 of 24 windows, failing both the
significance and the economic-size bars. The conditional-on-fill version did not rescue
it either. So before OFI, the project had already shown that the baseline's premise is
not a reliable standalone edge.

OFI premise: the headline above. The strong unconditional signal is the reason OFI was
worth testing as a gate; the conditional-on-fill failure is the reason the OFI-gated
candidate (`OFIGatedMM`) never advanced.

## Robustness and discipline

The negative result has survived the obvious objections:

- Post-only artifact. The first profitable-looking runs were contaminated: latency
  turned intended passive limits into taker fills. Enforcing post-only made the
  simulation honest and the apparent edge disappeared.
- Replay correctness. A timestamp/snapshot synchronization bug was found and fixed; the
  negative conclusion was invariant to the fix, so it is a robustness result rather than
  a replay artifact. A separate six-anchor trade-gap policy audit reports exact zero
  deltas.
- Leakage hardening. The conditional OFI test was tightened to strictly-pre-fill
  samples with a shuffle tripwire that requires a near-zero statistic when the
  signal-to-outcome pairing is destroyed. The separation barely moved (`+0.126 ->
  +0.127`, `-0.379 -> -0.376`) and no status changed, so same-ms leakage is negligible.
- Queue and latency stress (Phase C). Matched net PnL per BTC is negative across the
  entire cancellation-credit grid, `-9.75` at credit `0.0` worsening monotonically to
  `-23.65` at credit `1.0`. Changing latency from 0 to 50 ms did not change the
  simulated fill set; this is specific to the quote distance and event path used here,
  not a general claim about latency sensitivity in market making.

Inference discipline is consistent throughout: full-strategy, matched-lot, and
residual-inventory PnL are separated and never conflated; session and window
bootstrap CIs are headlined while the correlated matched-lot bootstrap is treated as a
diagnostic; statistical significance is always reported with economic magnitude (`beta
* signal_std`); thin conditional buckets are labeled `inconclusive_power` rather than
forced into pass/fail; and determinism is verified by a 223-test deterministic suite and
state hashing.

## Related work and scope

The measured mechanisms are established market-microstructure ideas, not a claim of
theoretical novelty. Glosten and Milgrom frame bid-ask spreads as compensation for
trading against better-informed flow; Cont, Kukanov, and Stoikov connect order-flow
imbalance to short-horizon price impact; and Avellaneda and Stoikov formulate the
inventory-aware market-making control problem that this project deliberately did not
add after the development gate failed. The contribution here is the deterministic
measurement pipeline, realistic execution conditioning, and pre-registered decision
rule applied to real BTCUSDT data.

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
`regime-shifted` by a pre-strategy comparability screen, remained sealed because no
candidate passed the development gate: one shot, never used, so the anti-overfitting
guarantee is intact. The queue model is the load-bearing assumption: the negative
matched-PnL sign was robust across the credit grid, but throughput, average fill
quality, and loss magnitude are model-sensitive, so no strong positive claim about fill
economics is made. The `$2` quote distance is small (sub-basis-point), and the results
are specific to that configuration.

The disciplined conclusion is to publish the rigorous negative. A strong
population-level signal that failed this passive-maker premise gate, demonstrated with
realistic execution and an honestly conditioned test, is a more useful and more
credible result than a fragile positive built on contaminated execution assumptions.

## Claim-to-artifact map

| Claim | Artifact |
|---|---|
| Phase A proportional window CI (`-1.04`, `[-2.33, -0.03]`) | `results/panels/btcusdt_l2_panel_v2/phase_a_verdict.json` |
| OFI pooled 1s HAC `t = 64.6`, 24/24 same-sign, `+0.123 bps/sigma` | `results/panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms/summary.json` |
| Conditional separations (`+0.127` proportional, `-0.376` none) | same OFI summary plus the `..._qc0/summary.json` sibling |
| Phase C matched net per BTC (`-9.75` to `-23.65`) | `results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary/queue_credit_summary.csv` |
| Panel sizes and frozen hashes (24 dev, 12 holdout) | `results/panels/btcusdt_l2_panel_v2/window_selection_summary.json` (manifest `a3a99b0a...`, panel `760c55b7...`) |
