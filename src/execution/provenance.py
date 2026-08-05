"""Execution-model provenance and frozen-artifact safety guards."""

from pathlib import Path
from typing import Mapping

from src.execution.queue_credit import parse_queue_credit
from src.execution.simulator import EQUAL_TIMESTAMP_POLICY, EXECUTION_MODEL_VERSION
from src.replay.depth_parser import SNAPSHOT_TIME_POLICY


LEGACY_EXECUTION_MODEL_VERSION = "legacy_book_update_v1"
FROZEN_V2_PANEL_DIRNAME = "btcusdt_l2_panel_v2"
EXECUTION_PROVENANCE_FIELDS = (
    "execution_model_version",
    "equal_timestamp_policy",
    "snapshot_time_policy",
    "trade_gap_policy",
    "entry_latency_ms",
    "entry_jitter_ms",
    "cancel_latency_ms",
    "cancel_jitter_ms",
    "latency_seed",
    "post_only",
    "queue_cancellation_credit",
)


def require_safe_path_component(value: str, *, label: str) -> str:
    """Validate a user-controlled artifact name as one path component."""
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    component = Path(value)
    if (
        not value
        or value in {".", ".."}
        or component.is_absolute()
        or len(component.parts) != 1
        or component.name != value
        or "/" in value
        or "\\" in value
    ):
        raise ValueError(f"{label} must be a single safe path component")
    return value


def execution_provenance_for_replay(
    sim_config,
    *,
    trade_gap_policy: str,
) -> dict[str, object]:
    """Bind simulator and data-censoring semantics into one artifact identity."""
    if trade_gap_policy not in {"ignore", "pause_until_snapshot"}:
        raise ValueError(
            "trade_gap_policy must be 'ignore' or 'pause_until_snapshot'"
        )
    provenance = dict(sim_config.provenance)
    provenance["snapshot_time_policy"] = SNAPSHOT_TIME_POLICY
    provenance["trade_gap_policy"] = trade_gap_policy
    return provenance


def guard_frozen_v2_output_path(
    path: Path,
    *,
    writer_label: str = "current-branch",
) -> None:
    """Refuse any current-branch write inside the frozen V2 panel."""
    resolved = path.expanduser().resolve(strict=False)
    if FROZEN_V2_PANEL_DIRNAME in resolved.parts:
        raise ValueError(
            f"{writer_label} output cannot be written beneath the "
            f"frozen {FROZEN_V2_PANEL_DIRNAME} artifact root: {resolved}"
        )


def guard_event_driven_output_path(path: Path) -> None:
    """Refuse frozen-root targets and existing symlink write redirections."""
    guard_frozen_v2_output_path(
        path,
        writer_label=EXECUTION_MODEL_VERSION,
    )
    lexical = path.expanduser().absolute()
    for component in (lexical, *lexical.parents):
        if component.is_symlink():
            raise ValueError(
                "event-driven output path cannot traverse a symlink: "
                f"{component}"
            )
    if lexical.is_dir():
        redirected = next(
            (child for child in lexical.rglob("*") if child.is_symlink()),
            None,
        )
        if redirected is not None:
            raise ValueError(
                "event-driven output tree contains a symlink write target: "
                f"{redirected}"
            )


def artifact_execution_model(payload: Mapping[str, object]) -> str:
    """Read model metadata; absent metadata is explicitly historical V2."""
    provenance = payload.get("execution_provenance")
    if isinstance(provenance, Mapping):
        model = provenance.get("execution_model_version")
        if model is not None:
            return str(model)

    params = payload.get("params")
    if isinstance(params, Mapping):
        model = params.get("execution_model_version")
        if model is not None:
            return str(model)

    return LEGACY_EXECUTION_MODEL_VERSION


def require_event_driven_provenance(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Return a complete current-model block or reject the artifact."""
    model = artifact_execution_model(payload)
    if model != EXECUTION_MODEL_VERSION:
        raise ValueError(
            f"expected {EXECUTION_MODEL_VERSION} artifact, found {model}"
        )
    provenance = payload.get("execution_provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("event-driven artifact is missing execution provenance")
    missing = [
        field for field in EXECUTION_PROVENANCE_FIELDS
        if field not in provenance
    ]
    if missing:
        raise ValueError(
            "event-driven artifact has incomplete execution provenance: "
            + ", ".join(missing)
        )
    if provenance["equal_timestamp_policy"] != EQUAL_TIMESTAMP_POLICY:
        raise ValueError(
            "event-driven artifact uses an incompatible equal-timestamp policy"
        )
    if provenance["snapshot_time_policy"] != SNAPSHOT_TIME_POLICY:
        raise ValueError(
            "event-driven artifact uses an incompatible snapshot-time policy"
        )
    if provenance["trade_gap_policy"] not in {
        "ignore", "pause_until_snapshot"
    }:
        raise ValueError("event-driven artifact uses an invalid trade-gap policy")
    if provenance["post_only"] is not True:
        raise ValueError("event-driven passive artifact must be post-only")
    integer_fields = (
        "entry_latency_ms",
        "entry_jitter_ms",
        "cancel_latency_ms",
        "cancel_jitter_ms",
        "latency_seed",
    )
    if any(
        type(provenance[field]) is not int or provenance[field] < 0
        for field in integer_fields
    ):
        raise ValueError(
            "event-driven artifact has invalid latency or seed provenance"
        )
    if (
        provenance["entry_jitter_ms"] > provenance["entry_latency_ms"]
        or provenance["cancel_jitter_ms"] > provenance["cancel_latency_ms"]
    ):
        raise ValueError("event-driven artifact jitter exceeds base latency")
    try:
        parse_queue_credit(provenance["queue_cancellation_credit"])
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(
            "event-driven artifact has invalid queue-credit provenance"
        ) from exc
    return {field: provenance[field] for field in EXECUTION_PROVENANCE_FIELDS}
