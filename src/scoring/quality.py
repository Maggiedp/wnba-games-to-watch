"""Compute game quality score based on team strength."""

import logging

logger = logging.getLogger(__name__)

# BPI observed range from 2025 season data. Offset shifts all values positive
# before harmonic mean, since HM is only meaningful for positive numbers.
_BPI_MIN = -10.0
_BPI_MAX = 10.0
_BPI_OFFSET = abs(_BPI_MIN) + 1.0  # 11.0 → shifted range is 1..21


def _shift(bpi: float) -> float:
    """Shift a BPI value into the positive domain for harmonic mean."""
    return bpi + _BPI_OFFSET


def harmonic_mean_shifted(bpi_a: float, bpi_b: float) -> float:
    """Compute harmonic mean of two BPI ratings after shifting to positive domain."""
    sa, sb = _shift(bpi_a), _shift(bpi_b)
    return 2 * sa * sb / (sa + sb)


def normalize_quality_score(shifted_hm: float) -> float:
    """Normalize a shifted harmonic mean to 0-100.

    Shifted HM range: HM(1, 1)=1 at worst, HM(21, 21)=21 at best.
    """
    shifted_min = _shift(_BPI_MIN)  # 1.0
    shifted_max = _shift(_BPI_MAX)  # 21.0
    clamped = max(shifted_min, min(shifted_max, shifted_hm))
    return (clamped - shifted_min) / (shifted_max - shifted_min) * 100


def compute_quality_score(bpi_a: float, bpi_b: float) -> float:
    """Compute normalized game quality score (0-100).

    Higher = better matchup (strong teams, evenly matched).
    Uses harmonic mean so a lopsided matchup (5 vs -5) scores lower
    than an even one (0 vs 0), even if arithmetic means are similar.
    """
    hm = harmonic_mean_shifted(bpi_a, bpi_b)
    score = normalize_quality_score(hm)
    logger.debug(
        f"Quality: BPI({bpi_a:.2f}, {bpi_b:.2f}) → HM={hm:.2f} → {score:.1f}/100"
    )
    return score
