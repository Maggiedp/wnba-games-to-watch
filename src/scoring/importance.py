"""Compute game importance score based on playoff impact."""


def normalize_importance_score(swing: float, max_swing: float = 0.75) -> float:
    """Normalize importance swing to 0-100 scale.

    Swing = sum across **all** teams of |P(makes playoffs | team_a wins) - P(makes playoffs | team_b wins)|.
    Captures bubble watchers, not just the two teams on the court.

    Empirical scale on real 2025 data (scripts/validate_bubble_swing.py):
        Aug 18 4-way bubble peak ~0.58, Aug 25 peak ~0.47, Sep 1 (bubble cleared) peak ~0.10.
        max_swing=0.75 maps a hot late-season bubble game to ~77/100. Synthetic
        4-way-tied fixture hits ~2.0 (saturates at 100 — extreme cases that don't
        appear in real WNBA). Coincidentally matches the previous two-team-only
        calibration: bubble watchers add ~what dilution between playing teams costs.
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
