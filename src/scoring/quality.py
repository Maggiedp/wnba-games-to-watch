"""Compute game quality score based on team strength."""

import logging

logger = logging.getLogger(__name__)


def harmonic_mean(bpi_a: float, bpi_b: float) -> float:
    """Compute harmonic mean of two BPI ratings.

    The harmonic mean penalizes lopsided matchups (high variance),
    so a 5 vs 1 game scores lower than a 3 vs 3 game.
    """
    denominator = bpi_a + bpi_b
    if denominator == 0 or bpi_a == 0 or bpi_b == 0:
        return 0.0

    return 2 * bpi_a * bpi_b / denominator


def normalize_quality_score(
    quality: float, min_bpi: float = -10.0, max_bpi: float = 10.0
) -> float:
    """Normalize quality score to 0-100 scale.

    BPI scores typically range from -10 to +10, where:
    - +10 is an elite team
    - 0 is league-average strength
    - -10 is a very weak team

    Harmonic mean of two BPI scores will also be in roughly this range.
    """
    # Clamp to expected range
    clamped = max(min_bpi, min(max_bpi, quality))

    # Linear normalization: -10 -> 0, +10 -> 100
    # First shift to 0-20 range, then scale to 0-100
    normalized = (clamped - min_bpi) / (max_bpi - min_bpi) * 100
    return max(0.0, min(100.0, normalized))


def compute_quality_score(bpi_a: float, bpi_b: float) -> float:
    """Compute normalized game quality score (0-100).

    High quality games are those between similarly strong teams.
    """
    h_mean = harmonic_mean(bpi_a, bpi_b)
    normalized = normalize_quality_score(h_mean)
    logger.debug(
        f"Quality score: BPI({bpi_a}, {bpi_b}) = HM({h_mean:.2f}) = {normalized:.1f}/100"
    )
    return normalized
