"""Playoff bracket simulation for WNBA.

Pure functions, no I/O. Uses the same Elo-based `simulate_game` from
monte_carlo.py so playoff games are consistent with regular-season sims.

Bracket is fixed (no reseeding, per WNBA rules since 2024).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.scoring.elo import DEFAULT_HOME_ADVANTAGE
from src.scoring.monte_carlo import simulate_game

if TYPE_CHECKING:
    from src.scoring.monte_carlo import TeamStanding


# Home-court patterns. "H" = higher seed hosts, "L" = lower seed hosts.
HOME_PATTERN_BO3 = ("H", "L", "H")  # First Round (1-1-1)
HOME_PATTERN_BO5 = ("H", "H", "L", "L", "H")  # Semifinals (2-2-1)
HOME_PATTERN_BO7 = ("H", "H", "L", "L", "H", "L", "H")  # Finals (2-2-1-1-1)


def play_series(
    higher_seed: str,
    lower_seed: str,
    home_pattern: tuple[str, ...],
    standings: dict[str, "TeamStanding"],
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> str:
    """Simulate a best-of-N. Returns the winning team name.

    Each game in the pattern is simulated with home-court advantage applied
    to whichever team is hosting that game. The series short-circuits as
    soon as one team has the majority wins required.
    """
    games_needed = len(home_pattern) // 2 + 1
    higher_wins = 0
    lower_wins = 0
    higher_elo = standings[higher_seed].elo
    lower_elo = standings[lower_seed].elo

    for host in home_pattern:
        if host == "H":
            higher_won = simulate_game(
                higher_elo, lower_elo, home_advantage=home_advantage
            )
        else:
            # Lower seed hosts: swap args so the +H bonus goes to the host,
            # then invert the result so it still reports "did higher seed win".
            higher_won = not simulate_game(
                lower_elo, higher_elo, home_advantage=home_advantage
            )

        if higher_won:
            higher_wins += 1
            if higher_wins == games_needed:
                return higher_seed
        else:
            lower_wins += 1
            if lower_wins == games_needed:
                return lower_seed

    raise RuntimeError(
        f"play_series did not reach a winner — series length {len(home_pattern)} "
        f"is inconsistent with games_needed {games_needed}"
    )


def simulate_playoffs(*args, **kwargs):
    """Implemented in Task 2."""
    raise NotImplementedError
