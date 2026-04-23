"""Tests for quality scoring."""

from src.scoring.quality import (
    harmonic_mean_shifted,
    normalize_quality_score,
    compute_quality_score,
    _shift,
)


def test_shift_makes_positive():
    """All BPI values within the expected range should be positive after shifting."""
    assert _shift(-8.0) > 0
    assert _shift(0.0) > 0
    assert _shift(8.0) > 0


def test_harmonic_mean_equal_teams():
    """Equal teams should return a score equal to their shifted value."""
    hm = harmonic_mean_shifted(5.0, 5.0)
    assert hm == _shift(5.0)


def test_harmonic_mean_penalizes_lopsided():
    """A lopsided matchup should score lower than an even one with similar average."""
    # (10, -8) average ≈ 1, but very lopsided
    # (1, 1) average = 1, evenly matched
    hm_lopsided = harmonic_mean_shifted(10.0, -8.0)
    hm_even = harmonic_mean_shifted(1.0, 1.0)
    assert hm_lopsided < hm_even


def test_harmonic_mean_mixed_signs():
    """Should handle mixed positive/negative BPI without errors."""
    result = harmonic_mean_shifted(5.668, -3.901)  # Lynx vs Mystics
    assert result > 0


def test_normalize_bounds():
    """Extremes should map to 0 and 100."""
    assert normalize_quality_score(_shift(-8.0)) == 0.0
    assert normalize_quality_score(_shift(8.0)) == 100.0


def test_compute_quality_score_ordering():
    """Better matchups should score higher."""
    # Two strong teams equally matched
    top_game = compute_quality_score(5.0, 4.0)
    # Two weak teams
    weak_game = compute_quality_score(-4.0, -5.0)
    # One strong, one weak (lopsided)
    lopsided = compute_quality_score(5.0, -5.0)

    assert top_game > lopsided
    assert weak_game < top_game  # weak-vs-weak scores lower than strong-vs-strong
    assert 0 <= top_game <= 100
    assert 0 <= lopsided <= 100
