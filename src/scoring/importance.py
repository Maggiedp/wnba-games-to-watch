"""Compute game importance score based on playoff impact."""

# Regular-season importance ceiling = the peak all-team round-reached swing
# actually observed in the prior completed season, computed with the corrected
# compute_importance_from_matrix method (summed over each team's five exclusive
# fate levels — missed/lost-QF/lost-SF/lost-Finals/champion, not just binary
# make-playoffs) at 10k sims by scripts/compute_importance_ceiling.py (which
# mirrors production standings construction — all teams seeded 0-0 — and seeds
# the RNG per date, so the value is reproducible). Pinned so the 0-100 scale
# reads in honest season-wide stakes: a moderate mid-season swing is moderate,
# and only genuine stretch-run bubble games approach 100. Reviewed,
# season-boundary-recalibrated constant (like DEFAULT_K and the BPI range) —
# re-run the scan and bump it each offseason. Don't go back to auto-deriving it
# from an equal-standings sim (~0.29) — that under-anchored and inflated
# mid-season games.
# 2025 scan (round-reached fate): max 1.0032 (Aug 9, Golden State Valkyries vs
# LA Sparks), p99 0.8889. The prior binary make-playoffs fate scanned to
# max 0.5174 / p99 0.4266 on the same draws; the fate changed because a
# clinched playoff field drove that swing — and every remaining game — to 0.
REGULAR_SEASON_MAX_SWING = 1.0032


def normalize_importance_score(
    swing: float, max_swing: float = REGULAR_SEASON_MAX_SWING
) -> float:
    """Normalize importance swing to 0-100 scale.

    Swing = sum across **all** teams and all five fate levels (missed
    playoffs, lost QF, lost SF, lost Finals, champion) of
    |P(fate level | team_a wins) - P(fate level | team_b wins)|.
    Captures bubble watchers, not just the two teams on the court.

    `max_swing` defaults to REGULAR_SEASON_MAX_SWING (the pinned prior-season
    peak). Production and offline validators pass it explicitly.
    """
    clamped = min(max_swing, swing)
    return max(0.0, min(100.0, (clamped / max_swing) * 100))


# Structural max for a postseason bracket game's round-reached swing. In a
# win-or-go-home game the loser's fate collapses to lost_<round> with
# probability 1 while the winner's fate spreads across the deeper levels
# summing to 1, so each participant contributes exactly 2 units of |delta| —
# one moving out of its current level, one distributed across where it goes
# next. Two participants -> 4.0, which is also the hard analytic bound (no
# team can move more than 2 units of total variation).
#
# UNLIKE REGULAR_SEASON_MAX_SWING this is NOT a season-calibrated constant and
# must NOT be re-derived each offseason — it follows from the structure of the
# fate variable, not from any season's observed distribution. Measured against
# synthetic bracket states on the 2025 field, the three win-or-go-home states
# score 3.9473 (QF G3) / 3.9565 (SF G5) / 3.9681 (Finals G7); the shortfall is
# the subtracted noise floor, which is correct.
POSTSEASON_MAX_SWING = 4.0


def normalize_postseason_importance(swing: float) -> float:
    """Map a round-reached bracket swing (0.0–4.0) to a 0–100 importance score."""
    clamped = max(0.0, min(POSTSEASON_MAX_SWING, swing))
    return (clamped / POSTSEASON_MAX_SWING) * 100.0
