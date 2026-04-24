"""Monte Carlo simulation for WNBA playoff probability.

Uses Elo for game-level win probability (calibrated against 2024+2025 results
via scripts/validate_elo.py). BPI is no longer consulted here — it survives
in the standings dict as a sibling field used only by quality scoring.
"""

import logging
import random
from collections import defaultdict
from dataclasses import dataclass

from src.scoring.elo import DEFAULT_HOME_ADVANTAGE, INITIAL_RATING, expected_win_prob

logger = logging.getLogger(__name__)

# WNBA playoff structure: 8 teams make playoffs
PLAYOFF_TEAMS = 8


@dataclass
class TeamStanding:
    """Team standing in a simulated season."""

    name: str
    wins: int = 0
    losses: int = 0
    elo: float = INITIAL_RATING

    @property
    def win_pct(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0


def simulate_game(
    elo_a: float,
    elo_b: float,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> bool:
    """Simulate a single game, return True if team A (home) wins.

    team_a is assumed to be the home team (matches ESPN `_parse_event`
    convention). Pass home_advantage=0 for neutral-site games.
    """
    return random.random() < expected_win_prob(
        elo_a, elo_b, home_advantage=home_advantage
    )


def run_monte_carlo_simulation(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    num_simulations: int = 10000,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> dict[str, float]:
    """Run Monte Carlo simulations to compute playoff probabilities.

    Args:
        current_standings: {team_name: {"wins", "losses", "elo", ...}}.
            Extra keys (e.g. "bpi") are ignored here.
        remaining_games: list of (home_team, away_team) tuples. home_advantage
            is applied to the first element of each tuple.
        num_simulations: Number of simulations to run.
        home_advantage: Elo-point bonus applied to the home team per game.

    Returns:
        Dict mapping team_name -> playoff_probability (0.0 to 1.0).
    """
    playoff_counts: dict[str, int] = defaultdict(int)

    for _ in range(num_simulations):
        standings = {
            name: TeamStanding(
                name=name,
                wins=data["wins"],
                losses=data["losses"],
                elo=data.get("elo", INITIAL_RATING),
            )
            for name, data in current_standings.items()
        }

        for team_a, team_b in remaining_games:
            if team_a not in standings or team_b not in standings:
                logger.warning(f"Team not in standings: {team_a} or {team_b}")
                continue

            elo_a = standings[team_a].elo
            elo_b = standings[team_b].elo

            if simulate_game(elo_a, elo_b, home_advantage=home_advantage):
                standings[team_a].wins += 1
                standings[team_b].losses += 1
            else:
                standings[team_b].wins += 1
                standings[team_a].losses += 1

        sorted_teams = sorted(standings.values(), key=lambda t: t.wins, reverse=True)
        for team in sorted_teams[:PLAYOFF_TEAMS]:
            playoff_counts[team.name] += 1

    playoff_probs = {
        name: count / num_simulations for name, count in playoff_counts.items()
    }
    for name in current_standings.keys():
        if name not in playoff_probs:
            playoff_probs[name] = 0.0

    return playoff_probs


def compute_importance_swing(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    game_index: int,
    num_simulations: int = 2000,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> float:
    """Compute playoff odds swing for a specific game.

    For a game between team_a and team_b at game_index:
    1. Apply team_a win to standings, simulate remaining games → playoff probs
    2. Apply team_b win to standings, simulate remaining games → playoff probs
    3. Return sum of absolute playoff-prob changes for the two teams across outcomes
    """
    if game_index >= len(remaining_games):
        return 0.0

    team_a, team_b = remaining_games[game_index]

    if team_a not in current_standings or team_b not in current_standings:
        return 0.0

    games_without = remaining_games[:game_index] + remaining_games[game_index + 1 :]

    standings_a_wins = {name: dict(data) for name, data in current_standings.items()}
    standings_a_wins[team_a]["wins"] += 1
    standings_a_wins[team_b]["losses"] += 1
    probs_a_win = run_monte_carlo_simulation(
        standings_a_wins,
        games_without,
        num_simulations=num_simulations,
        home_advantage=home_advantage,
    )

    standings_b_wins = {name: dict(data) for name, data in current_standings.items()}
    standings_b_wins[team_b]["wins"] += 1
    standings_b_wins[team_a]["losses"] += 1
    probs_b_win = run_monte_carlo_simulation(
        standings_b_wins,
        games_without,
        num_simulations=num_simulations,
        home_advantage=home_advantage,
    )

    swing_a = abs(probs_a_win.get(team_a, 0.0) - probs_b_win.get(team_a, 0.0))
    swing_b = abs(probs_b_win.get(team_b, 0.0) - probs_a_win.get(team_b, 0.0))

    total_swing = swing_a + swing_b
    logger.debug(
        f"Game swing ({team_a} vs {team_b}): "
        f"{team_a} swing={swing_a:.3f}, {team_b} swing={swing_b:.3f}, total={total_swing:.3f}"
    )
    return total_swing
