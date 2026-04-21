"""Monte Carlo simulation for WNBA playoff probability."""

import logging
import random
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# WNBA playoff structure: 8 teams make playoffs
PLAYOFF_TEAMS = 8


@dataclass
class TeamStanding:
    """Team standing in a simulated season."""

    name: str
    wins: int = 0
    losses: int = 0
    bpi: float = 0.0

    @property
    def win_pct(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def points(self) -> int:
        """Standing points (wins)."""
        return self.wins


def compute_win_probability(bpi_a: float, bpi_b: float) -> float:
    """Compute probability that team A beats team B using BPI ratings.

    Uses a logistic function based on BPI difference.
    A team with BPI 2 points higher has roughly 55% win prob.
    """
    bpi_diff = bpi_a - bpi_b
    # Scale: ~2 BPI points = ~5% win prob diff
    k = 0.025  # Scaling factor
    prob_a = 1.0 / (1.0 + pow(10, -k * bpi_diff))
    return prob_a


def simulate_game(bpi_a: float, bpi_b: float) -> bool:
    """Simulate a single game, return True if team A wins."""
    prob_a = compute_win_probability(bpi_a, bpi_b)
    return random.random() < prob_a


def run_monte_carlo_simulation(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    num_simulations: int = 10000,
) -> dict[str, float]:
    """Run Monte Carlo simulations to compute playoff probabilities.

    Args:
        current_standings: Dict of team_name -> {"wins": int, "losses": int, "bpi": float}
        remaining_games: List of (team_a, team_b) tuples for remaining games
        num_simulations: Number of simulations to run

    Returns:
        Dict mapping team_name -> playoff_probability (0.0 to 1.0)
    """
    playoff_counts = defaultdict(int)

    for sim_idx in range(num_simulations):
        # Copy current standings
        standings = {
            name: TeamStanding(
                name=name, wins=data["wins"], losses=data["losses"], bpi=data["bpi"]
            )
            for name, data in current_standings.items()
        }

        # Simulate remaining games
        for team_a, team_b in remaining_games:
            if team_a not in standings or team_b not in standings:
                logger.warning(f"Team not in standings: {team_a} or {team_b}")
                continue

            bpi_a = standings[team_a].bpi
            bpi_b = standings[team_b].bpi

            if simulate_game(bpi_a, bpi_b):
                standings[team_a].wins += 1
                standings[team_b].losses += 1
            else:
                standings[team_b].wins += 1
                standings[team_a].losses += 1

        # Determine playoff teams (top 8 by wins)
        sorted_teams = sorted(standings.values(), key=lambda t: t.points, reverse=True)
        playoff_teams = sorted_teams[:PLAYOFF_TEAMS]

        for team in playoff_teams:
            playoff_counts[team.name] += 1

    # Convert counts to probabilities
    playoff_probs = {
        name: count / num_simulations for name, count in playoff_counts.items()
    }

    # Add teams with 0 probability
    for name in current_standings.keys():
        if name not in playoff_probs:
            playoff_probs[name] = 0.0

    return playoff_probs


def compute_importance_swing(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    game_index: int,
    current_probs: dict[str, float],
    num_simulations: int = 2000,
) -> float:
    """Compute playoff odds swing for a specific game.

    For a game between team_a and team_b at game_index:
    1. Simulate assuming team_a wins
    2. Simulate assuming team_b wins
    3. Compute change in playoff probability for both teams
    4. Return the maximum swing (sum of absolute changes)
    """
    if game_index >= len(remaining_games):
        return 0.0

    team_a, team_b = remaining_games[game_index]

    if team_a not in current_standings or team_b not in current_standings:
        return 0.0

    # Simulate with team A winning
    games_with_a_win = (
        remaining_games[:game_index]
        + [(team_a, team_b)]
        + remaining_games[game_index + 1 :]
    )
    probs_a_win = run_monte_carlo_simulation(
        current_standings, games_with_a_win, num_simulations=num_simulations
    )

    # Simulate with team B winning
    games_with_b_win = (
        remaining_games[:game_index]
        + [(team_b, team_a)]
        + remaining_games[game_index + 1 :]
    )
    probs_b_win = run_monte_carlo_simulation(
        current_standings, games_with_b_win, num_simulations=num_simulations
    )

    # Compute swing
    swing_a = abs(probs_a_win.get(team_a, 0.0) - current_probs.get(team_a, 0.0))
    swing_b = abs(probs_b_win.get(team_b, 0.0) - current_probs.get(team_b, 0.0))

    total_swing = swing_a + swing_b
    logger.debug(
        f"Game swing ({team_a} vs {team_b}): "
        f"{team_a} swing={swing_a:.3f}, {team_b} swing={swing_b:.3f}, total={total_swing:.3f}"
    )

    return total_swing
