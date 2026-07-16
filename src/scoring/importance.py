"""Compute game importance score based on playoff impact."""

# Regular-season importance ceiling = the peak all-team playoff-odds swing
# actually observed in the prior completed season, computed with the corrected
# compute_importance_from_matrix method at 10k sims by
# scripts/compute_importance_ceiling.py. Pinned so the 0-100 scale reads in
# honest season-wide stakes: a moderate mid-season swing is moderate, and only
# genuine stretch-run bubble games approach 100. Reviewed, season-boundary-
# recalibrated constant (like DEFAULT_K and the BPI range) — re-run the scan and
# bump it each offseason. Previously auto-derived from an equal-standings sim
# (~0.29), which under-anchored and inflated mid-season games.
# 2025 scan: max 0.5093 (Aug 9, Golden State Valkyries vs LA Sparks), p99 0.44.
REGULAR_SEASON_MAX_SWING = 0.5093


def normalize_importance_score(
    swing: float, max_swing: float = REGULAR_SEASON_MAX_SWING
) -> float:
    """Normalize importance swing to 0-100 scale.

    Swing = sum across **all** teams of |P(makes playoffs | team_a wins) - P(makes playoffs | team_b wins)|.
    Captures bubble watchers, not just the two teams on the court.

    `max_swing` defaults to REGULAR_SEASON_MAX_SWING (the pinned prior-season
    peak). Production and offline validators pass it explicitly.
    """
    clamped = min(max_swing, swing)
    return max(0.0, min(100.0, (clamped / max_swing) * 100))


# Theoretical max for postseason championship swing. A single bracket game that
# cleanly flips the champion between two teams produces |Δ|=1.0 on each side
# → Σ|Δ| = 2.0 across all teams. Game 7 of a Finals between evenly-matched
# teams hits roughly that. Fixed ceiling keeps scores comparable across
# postseasons; per-bracket calibration would distort with the draw.
POSTSEASON_MAX_SWING = 2.0


def normalize_postseason_importance(swing: float) -> float:
    """Map a championship swing (0.0–2.0) to a 0–100 importance score."""
    clamped = max(0.0, min(POSTSEASON_MAX_SWING, swing))
    return (clamped / POSTSEASON_MAX_SWING) * 100.0
