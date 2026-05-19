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


def simulate_playoffs(
    seeded: list[str],
    standings: dict[str, "TeamStanding"],
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> dict[str, set[str] | str]:
    """Play the full 8-team WNBA bracket (no reseeding).

    Args:
        seeded: top 8 team names, index 0 = #1 seed.
        standings: TeamStanding map (uses .elo on each).
        home_advantage: Elo bonus applied to the host of each game.

    Returns:
        {
          "made_playoffs":    set of 8 team names,
          "reached_semis":    set of 4,
          "reached_finals":   set of 2,
          "won_championship": team name (str),
        }
    """
    if len(seeded) != 8:
        raise ValueError(
            f"simulate_playoffs requires exactly 8 seeded teams, got {len(seeded)}"
        )

    made_playoffs: set[str] = set(seeded)

    # First Round (Bo3): higher seed is always the first arg.
    qf1 = play_series(seeded[0], seeded[7], HOME_PATTERN_BO3, standings, home_advantage)
    qf2 = play_series(seeded[3], seeded[4], HOME_PATTERN_BO3, standings, home_advantage)
    qf3 = play_series(seeded[2], seeded[5], HOME_PATTERN_BO3, standings, home_advantage)
    qf4 = play_series(seeded[1], seeded[6], HOME_PATTERN_BO3, standings, home_advantage)

    reached_semis: set[str] = {qf1, qf2, qf3, qf4}

    # Fixed bracket: QF1/QF2 winners play in one semi, QF3/QF4 in the other.
    sf1_higher, sf1_lower = _higher_lower(qf1, qf2, seeded)
    sf2_higher, sf2_lower = _higher_lower(qf3, qf4, seeded)
    sf1 = play_series(
        sf1_higher, sf1_lower, HOME_PATTERN_BO5, standings, home_advantage
    )
    sf2 = play_series(
        sf2_higher, sf2_lower, HOME_PATTERN_BO5, standings, home_advantage
    )

    reached_finals: set[str] = {sf1, sf2}

    # Finals: higher original seed gets HCA.
    f_higher, f_lower = _higher_lower(sf1, sf2, seeded)
    champion = play_series(
        f_higher, f_lower, HOME_PATTERN_BO7, standings, home_advantage
    )

    return {
        "made_playoffs": made_playoffs,
        "reached_semis": reached_semis,
        "reached_finals": reached_finals,
        "won_championship": champion,
    }


def _higher_lower(a: str, b: str, seeded: list[str]) -> tuple[str, str]:
    """Return (higher_seed, lower_seed) by index in `seeded` (lower index = better)."""
    if seeded.index(a) < seeded.index(b):
        return a, b
    return b, a
