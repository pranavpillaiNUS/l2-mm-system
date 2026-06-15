# Phase 2 Artifact Index

Status: spot BTCUSDT Phase 2 development research arc complete. No candidate
strategy advanced to holdout, and the sealed holdout remains untouched.

## Canonical Writeups

- Active V2 research note: `notebooks/research_writeup_v2.md`
- Research log: `notebooks/research_log.md`
- Frozen V1 reference, superseded by V2: `notebooks/research_writeup.md`
- Holdout lock template, still unlocked: `notebooks/holdout_protocol.md`

## Frozen Provenance

- Integrity manifest SHA-256:
  `a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61`
- Selected panel SHA-256:
  `760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122`
- Development windows: `24`
- Holdout windows: `12`
- Holdout regime label: `regime-shifted`

Primary files:

- `results/panels/btcusdt_l2_panel_v2/integrity_manifest.json`
- `results/panels/btcusdt_l2_panel_v2/window_selection_summary.json`
- `results/panels/btcusdt_l2_panel_v2/development_windows.csv`
- `results/panels/btcusdt_l2_panel_v2/holdout_windows.csv`

## Phase A: Baseline Remeasurement

Question: does the V1 passive microprice negative survive the frozen 24-window
development panel at queue-credit endpoints `{0.0, 1.0}`?

Primary artifact:

- `results/panels/btcusdt_l2_panel_v2/phase_a_verdict.json`

Expected verdict:

- Headline: `Conditional V2: queue-model-dependent result`
- Conservative passive-edge verdict: `Strengthens V1`
- Queue credit `1.0`: `Strengthens V1`
- Queue credit `0.0`: `Weakens V1`

## Phase B: OFI Premise Test

Question: does order-flow imbalance give a passive maker a usable edge?

Primary artifacts:

- `results/panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms/summary.json`
- `results/panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms_qc0/summary.json`

Expected verdict:

- Overall: `blocked`
- Unconditional gate: pass
- Conditional-on-fill gate: fail signal
- Pooled 1s normalized OFI beta: about `+0.1192`
- Pooled 1s HAC t-stat: about `+64.63`
- Pooled 1s beta times signal standard deviation: about `+0.1233 bps`

Interpretation: OFI is a strong population-level signal, but not harvestable by
this passive maker because the fills received are the adversely-selected
subsample.

## Phase C: Queue-Credit And Latency Stress

Question: is the baseline negative an artifact of queue-credit or sub-50ms
latency assumptions?

Primary artifact:

- `results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary/summary.json`

Expected pooled rows:

- Queue credits: `0.0`, `0.25`, `0.5`, `0.75`, `1.0`
- Endpoint latencies: `0`, `10`, `50` ms for credits `0.0` and `1.0`
- Matched net per BTC worsens monotonically from about `-9.75` at credit `0.0`
  to about `-23.65` at credit `1.0`
- Matched and full-strategy PnL are invariant to latency in `[0, 50]ms`

## Holdout Status

The holdout is sealed. `notebooks/holdout_protocol.md` must remain an unlocked
template unless a future pre-registered candidate clears development first.

Expected status:

- `notebooks/holdout_protocol.md` contains `UNLOCKED TEMPLATE`
- No candidate holdout result artifacts exist under `results/`
- `results/panels/btcusdt_l2_panel_v2/holdout_windows.csv` is allowed because it
  is the frozen selection record, not a strategy result

## Perp Recording QA

Perp is recording-only and remains parked for analysis. A local capture-quality
check on 2026-06-15 confirmed:

- tmux sessions `rec_btcusdt_perp` and `rec_btcusdt_perp_trades` are live
- latest perp depth file starts with a REST snapshot payload under `data`
- snapshot has `1000` bid levels, `1000` ask levels, and `lastUpdateId`
- first bridge diff satisfies the futures snapshot bridge condition
- first 200 post-bridge futures diffs had no `pu != previous u` mismatch
- latest perp trade file uses raw `@trade` fields `E`, `T`, `X`, `e`, `m`,
  `p`, `q`, `s`, `t`
- first 500 checked raw trade IDs were sequential

This is capture QA only. It is not a spot-vs-perp analysis result.

## Verification

Run:

```bash
env PYTHONPATH=. python scripts/verify_v2_artifacts.py
env PYTHONPATH=. pytest -q tests --ignore=tests/test_recorder.py
```

Expected latest local deterministic suite:

```text
223 passed
```

`tests/test_recorder.py` is a live network/recorder test and is intentionally
excluded from the deterministic suite.
