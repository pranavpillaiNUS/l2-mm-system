# Result namespaces

The result tree contains exploratory, superseded, and canonical outputs. Cite a
result only after checking its execution-model namespace and status.

## Authoritative map

| Namespace | Execution model | Status | Permitted use |
|---|---|---|---|
| `results/panels/btcusdt_l2_panel_v2/` | `legacy_book_update_v1` | Frozen | Historical Phase A/B/C development evidence |
| `results/panels/btcusdt_l2_panel_v3_event_driven/` | `event_driven_v2` | Pending; directory not published yet | Future current-model development evidence only |
| Other root result folders | Mixed exploratory code paths | Historical/superseded unless documented otherwise | Debugging and project history, not headline claims |

The legacy model made entries eligible on the next depth update after modeled
arrival and applied cancellation requests immediately. The current model uses
scheduled entry/cancel arrivals, delayed-cancel races, own-order FIFO, and
recorded-volume conservation. Execution-derived values from the two models must
not be combined.

## Frozen V2 configuration

| Field | Value |
|---|---|
| Venue / instrument | Binance spot BTCUSDT |
| Development sample | 24 non-overlapping five-hour windows |
| Replay episodes | Five independently initialized one-hour sessions per window |
| Strategy | `MicropriceMM` |
| Half-spread / order size | `2.00 USDT` / `0.001 BTC` |
| Maximum position | `0.01 BTC` |
| Requote interval | `5,000 ms` |
| Modeled entry latency | `10 ms`, no jitter |
| Modeled fees | maker `2 bps`, taker `5 bps` |
| Queue-credit endpoints | `0.0`, `1.0` |
| Research snapshot | `1066950` |

## Canonical claims and files

| Claim | Unit / sampling unit | Canonical artifact |
|---|---|---|
| Integrity inventory: 775 valid of 1,193 hours | Hour | [`integrity_manifest.json`](panels/btcusdt_l2_panel_v2/integrity_manifest.json) |
| Panel: 24 development, 12 strategy-sealed holdout windows | Five-hour window | [`window_selection_summary.json`](panels/btcusdt_l2_panel_v2/window_selection_summary.json) |
| Phase A mean: `-0.686`, CI `[-1.883,+0.290]` at credit 0 | USDT per five-hour window | [`phase_a_verdict.json`](panels/btcusdt_l2_panel_v2/phase_a_verdict.json) |
| Phase A mean: `-1.043`, CI `[-2.332,-0.032]` at credit 1 | USDT per five-hour window | [`phase_a_verdict.json`](panels/btcusdt_l2_panel_v2/phase_a_verdict.json) |
| OFI 1s: `+0.1233 bps/s.d.`, HAC `t=64.63`, positive in 24/24 windows | Regular one-second book sample | [`OFI qc1 summary`](panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms/summary.json) |
| Conditional OFI: `+0.127` / `-0.376 bps`; defined gate blocked | Historical maker fill, 30-second horizon | [`OFI qc1 summary`](panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms/summary.json), [`qc0 summary`](panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms_qc0/summary.json) |
| Queue stress: `-9.75` to `-23.65` | USDT matched net P&L per BTC | [`queue summary`](panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary/summary.json) |
| Matched-lot break-even maker fee: `1.351 / 0.426 bps` at credits `0 / 1` | bps per maker fill | [`fee summary`](panels/btcusdt_l2_panel_v2/fee_break_even/btcusdt_microprice_hs2.00_rq5000_panel24/summary.json) |
| Same-ms overlap fills below 0.5%; zero artifact-evidenced contradictions | Fill | [`qc1 audit`](panels/btcusdt_l2_panel_v2/same_ms_audit/btcusdt_microprice_hs2.00_rq5000_panel24/summary.json), [`qc0 audit`](panels/btcusdt_l2_panel_v2/same_ms_audit/btcusdt_microprice_hs2.00_rq5000_panel24_qc0/summary.json) |
| Legacy snapshot tag to first later depth receipt: median `250.5 ms`, p90 `672.9 ms`, max `2,335 ms` | Selected development hour | [`snapshot timing audit`](replay_correctness/snapshot_timing_panel24.json) |
| No candidate strategy holdout result | Artifact boundary | [`holdout protocol`](../notebooks/holdout_protocol.md) |

The OFI conditional gate compares the most-populated negative side-aligned
bucket with the most-populated nonnegative bucket. It is not an extreme-bucket
test. Counts of 201 and 154 exceeded the predefined minimum of 30; that rule is
not a formal power analysis. The complete conditional response is non-monotone.

Each five-hour economic row sums five separately initialized one-hour replay
episodes. Residual inventory is marked at each hourly endpoint and state is
reset without modeled liquidation cost. Full-strategy P&L is therefore a
research diagnostic under that boundary, not continuous five-hour portfolio
P&L. At a representative 71,500 USDT price, the 2 USDT half-spread is about
0.28 bps per side versus a modeled 2 bps maker fee per fill; the fee hurdle is
structurally material.

**Frozen metadata erratum:** the panel fee summary's
`full_strategy_endpoint_note` says “window-close mid.” The computation actually
marks residual inventory at each one-hour session end and resets before the next
session, as the run structure and current generator both show. The frozen JSON is
left byte-for-byte unchanged so its published hash remains stable; the wording
does not alter the numeric rows.

The snapshot timing audit identifies a legacy recorder defect rather than a
robustness pass. V2 applied REST snapshot state at a tag captured before the
blocking response completed. Only two no-credit and four proportional-credit
fills came from orders placed before the selected policy boundary, but these
proxy counts are not upper bounds and later queue age can still differ. Current
replay uses `post_response_proxy_depth_boundary_v1`; the full V3 development
panel must be rerun.

## Verification

From the repository root:

```bash
env PYTHONPATH=. python scripts/verify_v2_artifacts.py
```

This checks selected expected identities, schemas, verdicts, Phase C grid,
same-millisecond audit, execution-model separation, the panel CSV hashes, and
that no tracked result path or content contains a selected holdout-window
identifier outside the allowlisted inventory and selection records. It does not
rerun the historical replay.

## V3 status

There are currently **no published V3 execution-derived economics**. The next
valid current-model output must live under
`results/panels/btcusdt_l2_panel_v3_event_driven/`, contain
`execution_model_version = event_driven_v2`, cover the frozen 24-window
development panel, and carry a complete artifact manifest before comparison.
