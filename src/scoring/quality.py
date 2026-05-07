"""Compute game quality score based on team strength."""

import logging

logger = logging.getLogger(__name__)

# BPI observed range from 2025/2026 season data (~-6 to +6), with buffer for outlier seasons.
# Offset shifts all values positive before harmonic mean, since HM is only meaningful for positive numbers.
_BPI_MIN = -8.0
_BPI_MAX = 8.0
_BPI_OFFSET = abs(_BPI_MIN) + 1.0  # 9.0 → shifted range is 1..17


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


def compute_quality_score(
    bpi_a: float,
    bpi_b: float,
    bpi_min: float = _BPI_MIN,
    bpi_max: float = _BPI_MAX,
) -> float:
    """Compute normalized game quality score (0-100).

    Higher = better matchup (strong teams, evenly matched).
    Uses harmonic mean so a lopsided matchup (5 vs -5) scores lower
    than an even one (0 vs 0), even if arithmetic means are similar.

    `bpi_min`/`bpi_max` define the normalization endpoints. Pass the
    season's observed team BPI range to use the full 0–100 scale;
    leave as defaults for fixed-scale scoring.
    """
    offset = abs(bpi_min) + 1.0
    sa, sb = bpi_a + offset, bpi_b + offset
    hm = 2 * sa * sb / (sa + sb)
    shifted_max = bpi_max + offset
    score = max(0.0, min(100.0, (hm - 1.0) / (shifted_max - 1.0) * 100))
    logger.debug(
        f"Quality: BPI({bpi_a:.2f}, {bpi_b:.2f}) → HM={hm:.2f} → {score:.1f}/100"
    )
    return score
