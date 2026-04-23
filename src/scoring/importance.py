"""Compute game importance score based on playoff impact."""

import logging
from src.scoring.monte_carlo import compute_importance_swing

logger = logging.getLogger(__name__)


def normalize_importance_score(swing: float, max_swing: float = 0.75) -> float:
    """Normalize importance swing to 0-100 scale.

    Swing = sum of each team's playoff-odds difference between the two forced-outcome scenarios.
    Empirically: top-vs-top early season ~13%, early bubble ~46%, late-season bubble ~70-72%.
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
