# Phase 2 Artifact Index

Status: the spot BTCUSDT Phase 2 development research arc is complete as a
frozen historical V2 study under `legacy_book_update_v1`. The Phase 2.5 Python
execution-model implementation has passed local acceptance under
`event_driven_v2`; its development-panel V3 rerun is pending. No candidate
strategy advanced to holdout. Holdout identities and pre-strategy regime
descriptors were examined, but no candidate strategy result exists.

## Canonical Writeups

- Frozen V2 research note: `notebooks/research_writeup_v2.md`
- Short public note: `notebooks/research_note_public.md`
- Current execution contract: `notebooks/execution_model_v2.md`
- Research log: `notebooks/research_log.md`
- Frozen V1 reference, superseded by V2: `notebooks/research_writeup.md`
- Holdout lock template, still unlocked: `notebooks/holdout_protocol.md`

## Frozen Provenance

- Research artifact commit: `106695026ca64ae232f07103ae380b49c2a4d49f`
- Execution model: `legacy_book_update_v1`
- Timing semantics: modeled arrivals activate on the next depth update;
  cancellation requests take effect immediately; REST snapshot state is applied
  at a local tag captured before the blocking request completed
- Frozen result root: `results/panels/btcusdt_l2_panel_v2`
- Provenance marker:
  `results/panels/btcusdt_l2_panel_v2/EXECUTION_MODEL.json`
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

All Phase A/B/C execution-derived values indexed below are historical results
from that model and commit. Missing per-artifact execution-model metadata under
the frozen root means `legacy_book_update_v1`; it must not be interpreted as
`event_driven_v2`.

Frozen metadata erratum: the panel fee summary's
`full_strategy_endpoint_note` says “window-close mid.” The actual run sums five
separately marked and reset one-hour sessions. The JSON remains unchanged to
preserve its hash; this is a wording correction, not a numeric revision.

## Phase 2.5: Event-Driven Execution Closure

- Current Python model: `event_driven_v2`
- Snapshot-time policy: `post_response_proxy_depth_boundary_v1`
- Required result namespace:
  `results/panels/btcusdt_l2_panel_v3_event_driven`
- Model and acceptance contract: `notebooks/execution_model_v2.md`
- Status: local implementation and deterministic acceptance checks complete;
  the V3 development rerun is pending, so no V3 execution-derived result or
  research verdict is yet validated
- Affected outputs requiring a V3 development rerun: fills, PnL,
  conditional-on-fill tests, queue diagnostics, and latency conclusions
- V3 comparison rule: compare the same development windows descriptively with
  exact artifact hashes and model provenance; do not reuse the historical
  V2/V1 advancement-verdict ladder across changed execution semantics
- Unaffected frozen inputs: raw-file integrity and panel selection. The snapshot
  timing correction means replay-derived book samples, state hashes, and
  unconditional signals also require regeneration or comparison.
- Holdout status: strategy-sealed; only frozen development windows may be used
  during execution closure
- C++ status: deferred until the Python reference is frozen and a separate
  modern C++ fundamentals phase is complete
- Local acceptance checkpoint: `369 passed in 5.56s`, focused execution and
  strategy subset `126 passed in 2.26s`, frozen V2 verifier passed, and the
  real one-hour replay smoke passed
- Full V3 runs must commit `ARTIFACT_MANIFEST.json`; verify with
  `env PYTHONPATH=. python scripts/verify_v3_artifacts.py`

## Frozen V2 Phase A: Baseline Remeasurement

Question: does the V1 passive microprice negative survive the frozen 24-window
development panel at queue-credit endpoints `{0.0, 1.0}`?

Primary artifact:

- `results/panels/btcusdt_l2_panel_v2/phase_a_verdict.json`

Expected verdict:

- Headline: `Conditional V2: queue-model-dependent result`
- Conservative passive-edge verdict: `Strengthens V1`
- Queue credit `1.0`: `Strengthens V1`
- Queue credit `0.0`: `Weakens V1`

## Frozen V2 Phase B: OFI Premise Test

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

Interpretation: OFI is a strong population-level signal, but the pre-specified
fill-conditioned gate failed at both queue endpoints and the complete
conditional bucket response was non-monotone. The frozen protocol therefore
blocked `OFIGatedMM` from advancing. This does not claim that OFI universally
disappears after fills or that the unrun candidate would necessarily lose,
because gating would change the fill set.

## Frozen V2 Phase C: Queue-Credit And Latency Stress

Question: how does the baseline change across queue-credit assumptions, and
what does the legacy sub-50 ms sweep reveal about its activation rule?

Primary artifact:

- `results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary/summary.json`

Expected pooled rows:

- Queue credits: `0.0`, `0.25`, `0.5`, `0.75`, `1.0`
- Endpoint latencies: `0`, `10`, `50` ms for credits `0.0` and `1.0`
- Matched net per BTC worsens monotonically from about `-9.75` at credit `0.0`
  to about `-23.65` at credit `1.0`
- Matched and full-strategy PnL are numerically invariant to latency in
  `[0, 50]ms` because legacy order eligibility was quantized to the next depth
  update. This is not evidence of genuine sub-50 ms latency robustness.

## Snapshot-Time Audit

Question: how early did the legacy pre-fetch snapshot tag precede an observable
post-request market-data boundary?

Primary artifact:

- `results/replay_correctness/snapshot_timing_panel24.json`

Expected result:

- 120 selected development depth files; 120 valid bridges; zero unbridged
  snapshots
- Tag to first later local depth receipt: median `250.5 ms`, p90 `672.9 ms`,
  maximum `2,335 ms`
- Frozen fills from orders placed before the selected policy boundary: `2 / 1,595` at no
  credit and `4 / 2,041` under proportional credit
- These proxy counts do not bound every pre-observation or later queue-age
  effect; V3 must rerun under `post_response_proxy_depth_boundary_v1`

## Same-Millisecond Attribution Audit

Question: can the depth-before-trade same-ms tie rule plausibly drive the V2
baseline conclusion?

Primary artifacts:

- `results/panels/btcusdt_l2_panel_v2/same_ms_audit/btcusdt_microprice_hs2.00_rq5000_panel24/summary.json`
- `results/panels/btcusdt_l2_panel_v2/same_ms_audit/btcusdt_microprice_hs2.00_rq5000_panel24_qc0/summary.json`
- `results/panels/btcusdt_l2_panel_v2/same_ms_audit/data_overlap_cache.json`

Expected result:

- Proportional credit: `10` same-ms overlap fills out of `2041` fills (`0.49%`)
- No credit: `7` same-ms overlap fills out of `1595` fills (`0.44%`)
- Artifact-evidenced wrong-attribution cases: `0` at both endpoints
- Both endpoints remain below escalation thresholds

## Holdout Status

The holdout is strategy-sealed. `notebooks/holdout_protocol.md` must remain an
unlocked template unless a future pre-specified candidate clears development
first.

Expected status:

- `notebooks/holdout_protocol.md` contains `UNLOCKED TEMPLATE`
- No active candidate qualified for holdout
- Candidate strategy remains `TBD`
- No candidate holdout result artifacts exist under `results/`
- `results/panels/btcusdt_l2_panel_v2/holdout_windows.csv` is allowed because it
  is the frozen selection record, not a strategy result

## Perp Recording QA

Perp is recording-only and remains parked for analysis. A historical local
capture-quality check on 2026-06-15 observed:

- tmux sessions `rec_btcusdt_perp` and `rec_btcusdt_perp_trades` were live at
  the time of that check; no current-liveness claim is made
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
env PYTHONPATH=. pytest -q
```

Historical local V2 closure check on 2026-06-16:

```text
223 passed in 5.37s
```

The live recorder check is intentionally separate from the test suite and can
be run manually with `scripts/smoke_test_recorder.py`.

The verifier checks the integrity and expected shape of the frozen artifacts;
it does not rerun the legacy experiment, validate `event_driven_v2`, or prove
that either execution model matches exchange ground truth. Within
`legacy_book_update_v1`, matched economics remained negative across the tested
queue-credit grid. Identical sub-50 ms point estimates diagnose the model's
next-depth-update activation quantization; they do not validate latency
insensitivity. The artifacts do not establish the same result under
`event_driven_v2`, the post-response-gated snapshot policy, or anything about other
spreads, strategies, signals, or venues.
