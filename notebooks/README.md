# Research documents

The [V3 writeup](research_writeup_v3.md) documents the completed corrected
development study and its blocked primary screen. The compiled
[technical report](../report/l2_mm_research_report.pdf) remains the historical
V2 report, covering system design, research protocol, frozen findings, failures,
limitations, and the claim-to-file map.

The files in this directory preserve the underlying research record. They are
useful for audit depth, but they are not all current summaries.

| Document | Status | Purpose |
|---|---|---|
| [`execution_model_v2.md`](execution_model_v2.md) | Current contract | Exact `event_driven_v2` timing, queue, gap, risk, provenance, and acceptance boundary |
| [`v3_development_protocol.md`](v3_development_protocol.md) | Committed before rerun | Corrected 24-window experiment, both endpoint screens, and diagnostic clustered uncertainty |
| [`research_writeup_v3.md`](research_writeup_v3.md) | Current completed study | Corrected baseline economics, blocked OFI screen, clustered intervals, and frozen source revision |
| [`../cpp/README.md`](../cpp/README.md) | Native design | Implemented order-book port, fixed-point input contract, parity, build, and benchmark scope |
| [`holdout_protocol.md`](holdout_protocol.md) | Unlocked template | One-shot candidate protocol; no candidate qualified |
| [`phase2_artifact_index.md`](phase2_artifact_index.md) | Frozen V2 index | Claim-to-artifact locations for the historical study |
| [`research_writeup_v2.md`](research_writeup_v2.md) | Frozen historical writeup | Full Phase A/B/C protocol and legacy-model results |
| [`research_note_public.md`](research_note_public.md) | Frozen short note | Concise historical narrative, superseded as the repository landing document by the PDF |
| [`research_writeup.md`](research_writeup.md) | Superseded V1 | Six-anchor study retained for provenance |
| [`error_analysis.md`](error_analysis.md) | Cumulative audit | Known bugs, model risks, and dispositions |
| [`research_log.md`](research_log.md) | Chronological record | Decisions and observations as the project evolved; later corrections supersede earlier interpretations |
| [`lessons_learned.md`](lessons_learned.md) | Historical Phase 1 reflection | Learning record; bar-strategy performance statements are not current evidence |

## Version boundary

- `legacy_book_update_v1` generated the frozen economic artifacts under
  `results/panels/btcusdt_l2_panel_v2/`.
- `event_driven_v2` is the current Python execution reference.
- The V3 study completed all 61 workflow steps, with 330 durable artifacts
  frozen against Python research commit `04df629`. Its primary screen is blocked
  at both queue endpoints.
- No candidate strategy has been evaluated on the strategy-sealed holdout.
- The native implementation ports the order book; replay, execution, and
  accounting remain Python.

When documents differ, use the V3 writeup for the completed corrected study,
the execution-model note for current mechanics, and each version's frozen
artifacts for exact values. Historical research logs and the PDF retain their
original evidence boundaries.

To verify the V3 run and regenerate its decision summary after later tooling
changes, restore the selected raw captures and retain the recorded commit in
Git history, then run:

```bash
env PYTHONPATH=. python scripts/verify_v3_artifacts.py --source-revision recorded
env PYTHONPATH=. python scripts/summarize_v3_development.py --source-revision recorded
```

Recorded mode checks source bytes at the manifest's exact Git commit, plus the
raw inputs and durable artifacts. The default without `--source-revision`
continues to require the current working source to match the run.
