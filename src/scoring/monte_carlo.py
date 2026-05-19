"""Monte Carlo simulation for WNBA playoff probability.

Uses Elo for game-level win probability (calibrated against 2024+2025 results
via scripts/validate_elo.py). BPI is no longer consulted here — it survives
in the standings dict as a sibling field used only by quality scoring.
"""

import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field

from src.constants import assert_all_teams_have_conferences
from src.scoring.elo import DEFAULT_HOME_ADVANTAGE, INITIAL_RATING, expected_win_prob
from src.scoring.tiebreakers import PLAYOFF_TEAMS, increment_h2h, resolve_seeding

logger = logging.getLogger(__name__)


@dataclass
class TeamStanding:
    """Team standing in a simulated season."""

    name: str
    wins: int = 0
    losses: int = 0
    elo: float = INITIAL_RATING
    # Per-opponent record: opponent_name -> [wins_vs_them, losses_vs_them].
    # Mutable list (not tuple) so we can update in place during simulation.
    h2h: dict[str, list[int]] = field(default_factory=dict)

    @property
    def win_pct(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0


@dataclass
class RoundProbabilities:
    """Per-team probability of reaching each playoff round, from a single MC run."""

    make_playoffs: dict[str, float] = field(default_factory=dict)
    reach_semis: dict[str, float] = field(default_factory=dict)
    reach_finals: dict[str, float] = field(default_factory=dict)
    win_championship: dict[str, float] = field(default_factory=dict)


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
    return_matrix: bool = False,
) -> (
    RoundProbabilities
    | tuple[RoundProbabilities, list[list[bool | None]], list[set[str]]]
):
    """Run Monte Carlo simulations to compute round-by-round playoff probabilities.

    Each sim plays the regular season to completion, then plays out the
    8-team bracket via src.scoring.playoffs.simulate_playoffs. Counters
    are accumulated for each round and divided by num_simulations.

    Args:
        current_standings: {team_name: {"wins", "losses", "elo", ...}}.
        remaining_games: list of (home_team, away_team) tuples.
        num_simulations: Number of simulations to run.
        home_advantage: Elo-point bonus for the home team.
        return_matrix: When True, also return the per-sim outcome matrix and
            playoff sets (needed for compute_importance_from_matrix).

    Returns:
        If return_matrix=False: RoundProbabilities with per-team probs for each round.
        If return_matrix=True: (RoundProbabilities, outcome_matrix, playoff_sets)
            outcome_matrix: list[list[bool]] shape (num_sims, num_remaining_games),
                True = team_a won that game in that sim.
            playoff_sets: list[set[str]] shape (num_sims,),
                set of team names that made the playoffs in that sim.
    """
    assert_all_teams_have_conferences(current_standings)
    made_counts: dict[str, int] = defaultdict(int)
    semi_counts: dict[str, int] = defaultdict(int)
    final_counts: dict[str, int] = defaultdict(int)
    champ_counts: dict[str, int] = defaultdict(int)
    outcome_matrix: list[list[bool]] = []
    playoff_sets: list[set[str]] = []

    for _ in range(num_simulations):
        standings = {
            name: TeamStanding(
                name=name,
                wins=data["wins"],
                losses=data["losses"],
                elo=data.get("elo", INITIAL_RATING),
                h2h={opp: list(rec) for opp, rec in data.get("h2h", {}).items()},
            )
            for name, data in current_standings.items()
        }

        game_outcomes: list[bool | None] = []
        for team_a, team_b in remaining_games:
            if team_a not in standings or team_b not in standings:
                logger.warning(f"Team not in standings: {team_a} or {team_b}")
                game_outcomes.append(None)
                continue

            elo_a = standings[team_a].elo
            elo_b = standings[team_b].elo

            a_won = simulate_game(elo_a, elo_b, home_advantage=home_advantage)
            game_outcomes.append(a_won)
            if a_won:
                standings[team_a].wins += 1
                standings[team_b].losses += 1
            else:
                standings[team_b].wins += 1
                standings[team_a].losses += 1
            increment_h2h(standings[team_a].h2h, team_b, won=a_won)
            increment_h2h(standings[team_b].h2h, team_a, won=not a_won)

        seeded = resolve_seeding(standings)
        playoff_team_set = set(seeded[:PLAYOFF_TEAMS])
        for team_name in playoff_team_set:
            made_counts[team_name] += 1

        if len(playoff_team_set) == PLAYOFF_TEAMS:
            # Local import avoids circular dependency (playoffs imports simulate_game).
            from src.scoring.playoffs import simulate_playoffs  # noqa: PLC0415

            bracket = simulate_playoffs(
                seeded[:PLAYOFF_TEAMS], standings, home_advantage=home_advantage
            )
            for t in bracket["reached_semis"]:
                semi_counts[t] += 1
            for t in bracket["reached_finals"]:
                final_counts[t] += 1
            champ_counts[bracket["won_championship"]] += 1

        if return_matrix:
            outcome_matrix.append(game_outcomes)
            playoff_sets.append(playoff_team_set)

    all_teams = list(current_standings.keys())

    def _to_prob(counts: dict[str, int]) -> dict[str, float]:
        return {n: counts.get(n, 0) / num_simulations for n in all_teams}

    result = RoundProbabilities(
        make_playoffs=_to_prob(made_counts),
        reach_semis=_to_prob(semi_counts),
        reach_finals=_to_prob(final_counts),
        win_championship=_to_prob(champ_counts),
    )

    if return_matrix:
        return result, outcome_matrix, playoff_sets
    return result


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
    3. Return sum of |P(playoffs | a wins) - P(playoffs | b wins)| across **all** teams.

    Summing across every team (not just the two on the court) captures bubble
    watchers — games whose outcome shifts the playoff fate of teams not playing
    in them. Locked-in or locked-out teams contribute 0 naturally.
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
    ).make_playoffs

    standings_b_wins = {name: dict(data) for name, data in current_standings.items()}
    standings_b_wins[team_b]["wins"] += 1
    standings_b_wins[team_a]["losses"] += 1
    probs_b_win = run_monte_carlo_simulation(
        standings_b_wins,
        games_without,
        num_simulations=num_simulations,
        home_advantage=home_advantage,
    ).make_playoffs

    total_swing = sum(
        abs(probs_a_win.get(name, 0.0) - probs_b_win.get(name, 0.0))
        for name in current_standings
    )
    swing_a = abs(probs_a_win.get(team_a, 0.0) - probs_b_win.get(team_a, 0.0))
    swing_b = abs(probs_b_win.get(team_b, 0.0) - probs_a_win.get(team_b, 0.0))
    logger.debug(
        f"Game swing ({team_a} vs {team_b}): "
        f"{team_a}={swing_a:.3f}, {team_b}={swing_b:.3f}, "
        f"all-team total={total_swing:.3f}"
    )
    return total_swing


def compute_importance_from_matrix(
    outcome_matrix: list[list[bool | None]],
    playoff_sets: list[set[str]],
    remaining_games: list[tuple[str, str]],
    team_names: list[str],
) -> list[float]:
    """Compute importance swing for every remaining game from one simulation run.

    Splits the simulation set by who won each game, then computes the all-team
    sum of |playoff_rate(a_won_sims) - playoff_rate(b_won_sims)|.

    Works because Elo ratings are fixed during simulation — game outcomes don't
    affect downstream win probabilities, so observed splits and forced splits
    are drawn from identical distributions.

    Args:
        outcome_matrix: shape (num_sims, num_remaining_games); True = team_a won,
            False = team_b won, None = unknown team (skip this sim for this game).
        playoff_sets: shape (num_sims,); set of team names that made playoffs.
        remaining_games: list of (home_team, away_team) used to produce the matrix.
        team_names: all team names to sum swing across.

    Returns:
        list of raw swing values (one per remaining game, same order).
        Normalize with normalize_importance_score before displaying.
    """
    num_sims = len(outcome_matrix)
    swings: list[float] = []

    for game_idx in range(len(remaining_games)):
        a_indices = [s for s in range(num_sims) if outcome_matrix[s][game_idx] is True]
        b_indices = [s for s in range(num_sims) if outcome_matrix[s][game_idx] is False]

        if not a_indices or not b_indices:
            swings.append(0.0)
            continue

        swing = 0.0
        for team in team_names:
            rate_a = sum(1 for s in a_indices if team in playoff_sets[s]) / len(
                a_indices
            )
            rate_b = sum(1 for s in b_indices if team in playoff_sets[s]) / len(
                b_indices
            )
            swing += abs(rate_a - rate_b)
        swings.append(swing)

    return swings
