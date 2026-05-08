"""
Small bootstrap helpers for baseline strategy diagnostics.

The main use here is estimating how noisy the passive MM baseline is before
we compare a new strategy against it. The caller decides the sampling unit:
matched lot, 1-hour session, or 5-hour window.
"""
from dataclasses import dataclass
from decimal import Decimal
from random import Random
from typing import Sequence


@dataclass(frozen=True)
class BootstrapResult:
    metric: str
    unit: str
    n: int
    mean: Decimal | None
    ci_low: Decimal | None
    ci_high: Decimal | None
    confidence: Decimal
    iterations: int


def mean_decimal(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def percentile(values: Sequence[Decimal], q: Decimal) -> Decimal | None:
    """
    Linear-interpolated percentile for q in [0, 1].
    """
    if not values:
        return None
    if q < Decimal("0") or q > Decimal("1"):
        raise ValueError("q must be between 0 and 1")

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    pos = q * Decimal(len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    weight = pos - Decimal(lo)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * weight


def bootstrap_mean_ci(
    values: Sequence[Decimal],
    *,
    metric: str,
    unit: str,
    confidence: Decimal = Decimal("0.95"),
    iterations: int = 10_000,
    seed: int = 7,
) -> BootstrapResult:
    """
    Bootstrap a confidence interval for the sample mean.

    With one observation the bootstrap is degenerate, so the CI collapses to
    that one value. This is honest: there is no cross-unit variation to learn
    from yet.
    """
    vals = list(values)
    n = len(vals)
    sample_mean = mean_decimal(vals)

    if n == 0:
        return BootstrapResult(
            metric=metric,
            unit=unit,
            n=0,
            mean=None,
            ci_low=None,
            ci_high=None,
            confidence=confidence,
            iterations=iterations,
        )

    if n == 1:
        return BootstrapResult(
            metric=metric,
            unit=unit,
            n=1,
            mean=sample_mean,
            ci_low=sample_mean,
            ci_high=sample_mean,
            confidence=confidence,
            iterations=iterations,
        )

    rng = Random(seed)
    means: list[Decimal] = []
    for _ in range(iterations):
        total = Decimal("0")
        for _ in range(n):
            total += vals[rng.randrange(n)]
        means.append(total / Decimal(n))

    alpha = (Decimal("1") - confidence) / Decimal("2")
    return BootstrapResult(
        metric=metric,
        unit=unit,
        n=n,
        mean=sample_mean,
        ci_low=percentile(means, alpha),
        ci_high=percentile(means, Decimal("1") - alpha),
        confidence=confidence,
        iterations=iterations,
    )
