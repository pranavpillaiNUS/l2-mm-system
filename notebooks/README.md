# Research documents

The primary public document is the compiled
[technical report](../report/l2_mm_research_report.pdf). It integrates the
system design, research protocol, frozen findings, failures, limitations,
current status, and claim-to-file map.

The files in this directory preserve the underlying research record. They are
useful for audit depth, but they are not all current summaries.

| Document | Status | Purpose |
|---|---|---|
| [`execution_model_v2.md`](execution_model_v2.md) | Current contract | Exact `event_driven_v2` timing, queue, gap, risk, provenance, and acceptance boundary |
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
- No V3 execution-derived result has been published.
- No candidate strategy has been evaluated on the strategy-sealed holdout.

When two documents differ, use the technical report for the current public
interpretation, the execution-model note for current mechanics, and the frozen
artifact itself for an exact historical value.
