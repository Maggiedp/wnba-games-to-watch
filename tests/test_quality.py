"""Tests for quality scoring."""

from src.scoring.quality import (
    harmonic_mean,
    normalize_quality_score,
    compute_quality_score,
)


def test_harmonic_mean():
    """Test harmonic mean calculation."""
    # Equal teams: HM(5, 5) = 5
    assert harmonic_mean(5.0, 5.0) == 5.0

    # Lopsided: HM(10, 0) = 0
    assert harmonic_mean(10.0, 0.0) == 0.0

    # Close teams: HM(5, 4) = 4.44...
    result = harmonic_mean(5.0, 4.0)
    assert 4.4 < result < 4.5


def test_normalize_quality_score():
    """Test normalization of quality scores."""
    # Min value should map to 0
    assert normalize_quality_score(-10.0) == 0.0

    # Max value should map to 100
    assert normalize_quality_score(10.0) == 100.0

    # Mid value should map to 50
    assert normalize_quality_score(0.0) == 50.0

    # Clamping should work
    assert normalize_quality_score(20.0) == 100.0
    assert normalize_quality_score(-20.0) == 0.0


def test_compute_quality_score():
    """Test full quality score computation."""
    # High quality (both strong teams)
    score = compute_quality_score(5.0, 4.0)
    assert 70 < score < 80  # Should be in upper range

    # Low quality (lopsided)
    score = compute_quality_score(10.0, -10.0)
    assert score == 50.0  # Harmonic mean is 0, which maps to 50
