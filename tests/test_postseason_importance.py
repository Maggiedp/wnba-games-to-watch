"""Tests for per-game postseason importance derivation."""

from src.scoring.importance import (
    POSTSEASON_MAX_SWING,
    normalize_postseason_importance,
)


def test_postseason_max_swing_is_two():
    """Ceiling = 2.0 (theoretical max: Σ|ΔP(champ)| when one game cleanly flips two teams)."""
    assert POSTSEASON_MAX_SWING == 2.0


def test_normalize_postseason_importance_zero_swing():
    assert normalize_postseason_importance(0.0) == 0.0


def test_normalize_postseason_importance_midpoint():
    # swing of 1.0 (half the max) → 50.0
    assert normalize_postseason_importance(1.0) == 50.0


def test_normalize_postseason_importance_caps_at_hundred():
    # swing of 2.0 → 100.0; anything beyond also caps at 100.0
    assert normalize_postseason_importance(2.0) == 100.0
    assert normalize_postseason_importance(3.5) == 100.0


def test_normalize_postseason_importance_floor_at_zero():
    # Defensive: negative values (shouldn't occur but be safe) → 0.0
    assert normalize_postseason_importance(-0.1) == 0.0
