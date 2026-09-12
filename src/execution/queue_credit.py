"""Helpers for the queue-cancellation credit configuration axis."""

from decimal import Decimal, InvalidOperation


def parse_queue_credit(value) -> Decimal:
    """Parse and validate a queue cancellation credit in [0.0, 1.0]."""
    try:
        credit = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            "queue_cancellation_credit must be a finite decimal in [0.0, 1.0]"
        ) from exc
    if (
        not credit.is_finite()
        or credit < Decimal("0")
        or credit > Decimal("1")
    ):
        raise ValueError("queue_cancellation_credit must be in [0.0, 1.0]")
    if credit == 0:
        return Decimal("0")
    return credit


def credit_from_legacy_mode(mode: str) -> Decimal:
    """Map old V1 queue-mode labels to the V2 credit axis."""
    if mode == "proportional":
        return Decimal("1.0")
    if mode == "none":
        return Decimal("0.0")
    raise ValueError("legacy queue mode must be 'proportional' or 'none'")


def legacy_mode_from_credit(credit) -> str | None:
    """Return the old label for endpoint credits. Otherwise no legacy label."""
    parsed = parse_queue_credit(credit)
    if parsed == Decimal("1"):
        return "proportional"
    if parsed == Decimal("0"):
        return "none"
    return None


def queue_credit_suffix(credit) -> str:
    """Stable artifact suffix for non-default queue-credit runs."""
    parsed = parse_queue_credit(credit)
    if parsed == Decimal("1"):
        return ""
    return f"_qc{parsed.normalize()}"
