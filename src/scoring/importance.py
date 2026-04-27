"""Compute game importance score based on playoff impact."""

import logging
from src.scoring.monte_carlo import compute_importance_swing

logger = logging.getLogger(__name__)


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


def compute_importance_score(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    game_index: int,
) -> float:
    """Compute normalized importance score for a game (0-100)."""
    swing = compute_importance_swing(
        current_standings,
        remaining_games,
        game_index,
        num_simulations=2000,
    )

    normalized = normalize_importance_score(swing)
    logger.debug(
        f"Importance score for game {game_index}: swing={swing:.3f} -> score={normalized:.1f}/100"
    )

    return normalized


def compute_all_game_importance(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
) -> list[float]:
    """Compute importance scores for all remaining games.

    First computes current playoff probabilities, then scores each game.
    """
    importance_scores = []
    for i in range(len(remaining_games)):
        score = compute_importance_score(current_standings, remaining_games, i)
        importance_scores.append(score)

    return importance_scores
