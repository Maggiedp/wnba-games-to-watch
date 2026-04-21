"""Compute game importance score based on playoff impact."""

import logging
from src.scoring.monte_carlo import run_monte_carlo_simulation, compute_importance_swing

logger = logging.getLogger(__name__)


def normalize_importance_score(swing: float, max_swing: float = 0.2) -> float:
    """Normalize importance swing to 0-100 scale.

    In a 10,000 simulation Monte Carlo, the maximum realistic swing
    for a single game is roughly 0.15-0.25 (15-25% change in playoff odds).

    Games that swing playoff odds by:
    - 0.01 (1%) are not very important
    - 0.10 (10%) are quite important
    - 0.20+ (20%+) are extremely important
    """
    # Clamp to expected range
    clamped = min(max_swing, swing)

    # Linear normalization: 0 -> 0, max_swing -> 100
    normalized = (clamped / max_swing) * 100
    return max(0.0, min(100.0, normalized))


def compute_importance_score(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    game_index: int,
    current_playoff_probs: dict[str, float],
) -> float:
    """Compute normalized importance score for a game (0-100).

    High importance games are those that significantly impact playoff odds.
    """
    swing = compute_importance_swing(
        current_standings,
        remaining_games,
        game_index,
        current_playoff_probs,
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
    logger.info("Computing current playoff probabilities...")
    current_probs = run_monte_carlo_simulation(
        current_standings, remaining_games, num_simulations=10000
    )

    logger.info("Computing importance for each game...")
    importance_scores = []

    for i, (team_a, team_b) in enumerate(remaining_games):
        score = compute_importance_score(
            current_standings, remaining_games, i, current_probs
        )
        importance_scores.append(score)

    return importance_scores
