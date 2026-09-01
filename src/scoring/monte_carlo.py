"""Monte Carlo simulation for WNBA playoff probability.

Uses Elo for game-level win probability (calibrated against 2024+2025 results
via scripts/validate_elo.py). BPI is no longer consulted here — it survives
in the standings dict as a sibling field used only by quality scoring.
"""

import logging
import math
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
    # Per-team P(finishing as seed k), k in 1..8; sums to make_playoffs[team].
    seed_distribution: dict[str, dict[int, float]] = field(default_factory=dict)


def to_team_standings(current_standings: dict[str, dict]) -> dict[str, TeamStanding]:
    """Coerce a plain standings dict (the daily_update shape) into TeamStanding
    objects suitable for resolve_seeding / simulate_playoffs. Deep-copies h2h so
    callers can mutate without aliasing."""
    return {
        name: TeamStanding(
            name=name,
            wins=data["wins"],
            losses=data["losses"],
            elo=data.get("elo", INITIAL_RATING),
            h2h={opp: list(rec) for opp, rec in data.get("h2h", {}).items()},
        )
        for name, data in current_standings.items()
    }


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


# How far a team got in one simulation. The importance metric sums
# |Δ| across teams AND these levels, so a game that only moves seeding
# (not berths) still registers — see docs "stretch-run importance".
FATE_MISSED = "missed"
FATE_LOST_QF = "lost_qf"
FATE_LOST_SF = "lost_sf"
FATE_LOST_FINALS = "lost_finals"
FATE_CHAMPION = "champion"

FATE_LEVELS = (
    FATE_MISSED,
    FATE_LOST_QF,
    FATE_LOST_SF,
    FATE_LOST_FINALS,
    FATE_CHAMPION,
)


def _fate_levels_for_sim(
    playoff_teams: set[str],
    reached_semis: set[str],
    reached_finals: set[str],
    champion: str | None,
    team_names: list[str],
) -> dict[str, str]:
    """Map every team to how far it got in one simulation.

    Checked champion -> finals -> semis -> qf, so it is correct whether
    simulate_playoffs' sets are cumulative or disjoint. (They are in fact
    cumulative — see the set-semantics note in run_monte_carlo_simulation's
    call site below.)
    """
    fate: dict[str, str] = {}
    for name in team_names:
        if name not in playoff_teams:
            fate[name] = FATE_MISSED
        elif name == champion:
            fate[name] = FATE_CHAMPION
        elif name in reached_finals:
            fate[name] = FATE_LOST_FINALS
        elif name in reached_semis:
            fate[name] = FATE_LOST_SF
        else:
            fate[name] = FATE_LOST_QF
    return fate


def run_monte_carlo_simulation(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    num_simulations: int = 10000,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    return_matrix: bool = False,
    bracket_state=None,
) -> (
    RoundProbabilities
    | tuple[
        RoundProbabilities,
        list[list[bool | None]],
        list[set[str]],
        list[dict[tuple[str, int], bool]],
        list[str | None],
        list[dict[str, str]],
    ]
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
        return_matrix: When True, also return the per-sim outcome matrix,
            playoff sets, bracket outcomes, champions, and fate levels.

    Returns:
        If return_matrix=False: RoundProbabilities with per-team probs for each round.
        If return_matrix=True: 6-tuple
            (round_probs, outcome_matrix, playoff_sets, bracket_outcomes, champions,
             fate_levels)
            outcome_matrix: list[list[bool]] shape (num_sims, num_remaining_games),
                True = team_a won that game in that sim.
            playoff_sets: list[set[str]] shape (num_sims,),
                set of team names that made the playoffs in that sim.
            bracket_outcomes: list[dict[(slot_id, game_num), bool]] shape (num_sims,),
                per-sim record of every bracket game simulated. Empty dict for sims
                where fewer than 8 teams seeded (no bracket played).
            champions: list[str | None] shape (num_sims,),
                champion team name per sim, or None if no bracket was played.
            fate_levels: list[dict[str, str]] shape (num_sims,),
                per-sim map of team name -> one of FATE_LEVELS (how far that
                team got in that sim).
    """
    assert_all_teams_have_conferences(current_standings)
    made_counts: dict[str, int] = defaultdict(int)
    semi_counts: dict[str, int] = defaultdict(int)
    final_counts: dict[str, int] = defaultdict(int)
    champ_counts: dict[str, int] = defaultdict(int)
    seed_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    outcome_matrix: list[list[bool]] = []
    playoff_sets: list[set[str]] = []
    bracket_outcomes_per_sim: list[dict[tuple[str, int], bool]] = []
    champions_per_sim: list[str | None] = []
    fate_levels_per_sim: list[dict[str, str]] = []

    all_team_names = list(current_standings.keys())

    for _ in range(num_simulations):
        standings = to_team_standings(current_standings)

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
        for seed_idx, team_name in enumerate(seeded[:PLAYOFF_TEAMS], start=1):
            seed_counts[team_name][seed_idx] += 1

        sim_bracket_outcomes: dict[tuple[str, int], bool] = {}
        sim_champion: str | None = None
        sim_fate: dict[str, str] = {}

        if len(playoff_team_set) == PLAYOFF_TEAMS:
            # Local import avoids circular dependency (playoffs imports simulate_game).
            from src.scoring.playoffs import simulate_playoffs  # noqa: PLC0415

            bracket = simulate_playoffs(
                seeded[:PLAYOFF_TEAMS],
                standings,
                home_advantage=home_advantage,
                bracket_state=bracket_state,
                recorder=sim_bracket_outcomes if return_matrix else None,
            )
            for t in bracket["reached_semis"]:
                semi_counts[t] += 1
            for t in bracket["reached_finals"]:
                final_counts[t] += 1
            champ_counts[bracket["won_championship"]] += 1
            sim_champion = bracket["won_championship"]
            if return_matrix:
                sim_fate = _fate_levels_for_sim(
                    playoff_team_set,
                    set(bracket["reached_semis"]),
                    set(bracket["reached_finals"]),
                    sim_champion,
                    all_team_names,
                )
        elif return_matrix:
            # No bracket played: qualifiers get lost_qf, everyone else missed.
            sim_fate = {
                name: (FATE_LOST_QF if name in playoff_team_set else FATE_MISSED)
                for name in all_team_names
            }

        if return_matrix:
            outcome_matrix.append(game_outcomes)
            playoff_sets.append(playoff_team_set)
            bracket_outcomes_per_sim.append(sim_bracket_outcomes)
            champions_per_sim.append(sim_champion)
            fate_levels_per_sim.append(sim_fate)

    all_teams = list(current_standings.keys())

    def _to_prob(counts: dict[str, int]) -> dict[str, float]:
        return {n: counts.get(n, 0) / num_simulations for n in all_teams}

    seed_distribution = {
        name: {
            seed: count / num_simulations
            for seed, count in sorted(seed_counts.get(name, {}).items())
        }
        for name in all_teams
    }

    result = RoundProbabilities(
        make_playoffs=_to_prob(made_counts),
        reach_semis=_to_prob(semi_counts),
        reach_finals=_to_prob(final_counts),
        win_championship=_to_prob(champ_counts),
        seed_distribution=seed_distribution,
    )

    if return_matrix:
        return (
            result,
            outcome_matrix,
            playoff_sets,
            bracket_outcomes_per_sim,
            champions_per_sim,
            fate_levels_per_sim,
        )
    return result


def _partition_outcomes(
    outcome_matrix: list[list[bool | None]], game_idx: int
) -> tuple[list[int], list[int]]:
    """Sim indices where team_a won vs. team_b won for one game (None = skip)."""
    num_sims = len(outcome_matrix)
    a_indices = [s for s in range(num_sims) if outcome_matrix[s][game_idx] is True]
    b_indices = [s for s in range(num_sims) if outcome_matrix[s][game_idx] is False]
    return a_indices, b_indices


def _partition_bracket(
    bracket_outcomes: list[dict[tuple[str, int], bool]],
    focal_slot: str,
    focal_game_num: int,
) -> tuple[list[int], list[int]]:
    """Sim indices where the higher seed won vs. the lower seed won for one
    bracket game (missing key / None = focal game not played in that sim)."""
    higher_indices: list[int] = []
    lower_indices: list[int] = []
    for i, outcomes in enumerate(bracket_outcomes):
        played = outcomes.get((focal_slot, focal_game_num))
        if played is True:
            higher_indices.append(i)
        elif played is False:
            lower_indices.append(i)
    return higher_indices, lower_indices


def _noise_floor_term(pooled_rate: float, n_a: int, n_b: int) -> float:
    """E[|rate_a - rate_b|] under H0 (game outcome independent of the team's fate).

    Under H0 the rate difference is ~ Normal(0, p(1-p)(1/n_a + 1/n_b)), so its
    absolute value is half-normal with mean sqrt(2/pi) * sigma. Summing |delta|
    over teams without subtracting this is positively biased — under the
    five-level round-reached fate this floor is summed per team PER LEVEL
    (five times the terms of the old binary make-playoffs floor), so it is
    substantially larger in swing units than the binary-fate figure once
    quoted here; see METHODOLOGY.md "noise floor" for the current
    quantitative treatment rather than a number pinned in this docstring.
    """
    variance = pooled_rate * (1.0 - pooled_rate) * (1.0 / n_a + 1.0 / n_b)
    return math.sqrt(2.0 / math.pi * variance)


def _fate_counts(
    indices: list[int], fate_levels: list[dict[str, str]], team: str
) -> dict[str, int]:
    """Per-level tally of one team's round-reached fate across a bucket of sims.

    One pass over the bucket, NOT one pass per level: this keeps callers at
    O(games x teams x sims) rather than five times that, which matters because
    the daily job runs synchronously against Cloud Run's request timeout.

    Sims where the team has no recorded fate (fewer than 8 teams seeded, so no
    bracket was played) contribute to no level, exactly as the inline versions
    this replaces did.
    """
    counts = dict.fromkeys(FATE_LEVELS, 0)
    for s in indices:
        level = fate_levels[s].get(team)
        if level is not None:
            counts[level] += 1
    return counts


def compute_importance_from_matrix(
    outcome_matrix: list[list[bool | None]],
    fate_levels: list[dict[str, str]],
    remaining_games: list[tuple[str, str]],
    team_names: list[str],
) -> list[float]:
    """Compute importance swing for every remaining game from one simulation run.

    Splits the simulation set by who won each game, then for each team sums
    |rate(level, a_won_sims) - rate(level, b_won_sims)| over the five
    round-reached fate levels, minus the analytic noise floor (sum of
    per-team-per-level half-normal means under H0), clamped at 0 — so
    finite-sample noise doesn't inflate dead-rubbers. Using round-reached
    fate (rather than binary make-playoffs) means a game that only moves
    seeding — not who's in the field — still scores nonzero once the
    playoff picture is locked.

    Works because Elo ratings are fixed during simulation — game outcomes don't
    affect downstream win probabilities, so observed splits and forced splits
    are drawn from identical distributions.

    Args:
        outcome_matrix: shape (num_sims, num_remaining_games); True = team_a won,
            False = team_b won, None = unknown team (skip this sim for this game).
        fate_levels: shape (num_sims,); per-sim map of team name -> one of
            FATE_LEVELS (how far that team got in that sim).
        remaining_games: list of (home_team, away_team) used to produce the matrix.
        team_names: all team names to sum swing across.

    Returns:
        list of corrected swing values (one per remaining game, same order).
        Normalize with normalize_importance_score before displaying.
    """
    swings: list[float] = []

    for game_idx in range(len(remaining_games)):
        a_indices, b_indices = _partition_outcomes(outcome_matrix, game_idx)

        if not a_indices or not b_indices:
            swings.append(0.0)
            continue

        n_a, n_b = len(a_indices), len(b_indices)
        swing = 0.0
        floor = 0.0
        for team in team_names:
            counts_a = _fate_counts(a_indices, fate_levels, team)
            counts_b = _fate_counts(b_indices, fate_levels, team)

            for level in FATE_LEVELS:
                count_a = counts_a[level]
                count_b = counts_b[level]
                swing += abs(count_a / n_a - count_b / n_b)
                floor += _noise_floor_term((count_a + count_b) / (n_a + n_b), n_a, n_b)
        swings.append(max(0.0, swing - floor))

    return swings


# Cumulative milestones for the "What's at stake" panel. The swing sums
# over exclusive fate levels; the panel reports the milestone whose odds
# moved most, because "odds of reaching the semis" reads naturally and
# "odds of losing in the semifinals" does not.
_MILESTONES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "playoffs",
        frozenset({FATE_LOST_QF, FATE_LOST_SF, FATE_LOST_FINALS, FATE_CHAMPION}),
    ),
    ("semis", frozenset({FATE_LOST_SF, FATE_LOST_FINALS, FATE_CHAMPION})),
    ("finals", frozenset({FATE_LOST_FINALS, FATE_CHAMPION})),
    ("championship", frozenset({FATE_CHAMPION})),
)


def compute_directional_movers_from_matrix(
    outcome_matrix: list[list[bool | None]],
    fate_levels: list[dict[str, str]],
    game_idx: int,
    team_names: list[str],
    top_n: int = 3,
    min_delta: float = 0.03,
) -> list[dict]:
    """Per-team directional milestone movers for one focal game.

    Partitions the sim set by who won ``game_idx`` (same split as
    ``compute_importance_from_matrix``), then for each team finds the
    cumulative milestone (make playoffs / reach semis / reach finals / win
    title) whose odds moved most between the two partitions — because the
    swing itself sums over five *exclusive* round-reached fate levels, but
    "odds of reaching the semis" reads naturally on the panel while "odds
    of losing in the semifinals" does not. Each team reports exactly one
    milestone: its own biggest mover, not a fixed milestone shared across
    teams. Returns up to ``top_n`` teams with the largest such delta,
    keeping only those whose delta is >= ``min_delta``, sorted descending
    by delta. Returns ``[]`` if either outcome bucket is empty (game
    decided/unplayed in all sims). Each dict:
    ``{"team": str, "level": str, "if_a": float, "if_b": float}``.
    """
    a_indices, b_indices = _partition_outcomes(outcome_matrix, game_idx)
    if not a_indices or not b_indices:
        return []

    n_a, n_b = len(a_indices), len(b_indices)
    movers: list[dict] = []
    for team in team_names:
        counts_a = _fate_counts(a_indices, fate_levels, team)
        counts_b = _fate_counts(b_indices, fate_levels, team)

        best: dict | None = None
        best_delta = 0.0
        for label, members in _MILESTONES:
            rate_a = sum(counts_a[lv] for lv in members) / n_a
            rate_b = sum(counts_b[lv] for lv in members) / n_b
            delta = abs(rate_a - rate_b)
            # Strict > keeps the first milestone on a tie, so _MILESTONES
            # order makes the choice deterministic.
            if delta > best_delta:
                best_delta = delta
                best = {
                    "team": team,
                    "level": label,
                    "if_a": rate_a,
                    "if_b": rate_b,
                }
        if best is not None and best_delta >= min_delta:
            movers.append(best)

    movers.sort(key=lambda m: abs(m["if_a"] - m["if_b"]), reverse=True)
    return movers[:top_n]


def compute_postseason_swing_from_matrix(
    focal_slot: str,
    focal_game_num: int,
    bracket_outcomes: list[dict[tuple[str, int], bool]],
    fate_levels: list[dict[str, str]],
    participants: tuple[str, str],
) -> float:
    """Round-reached importance swing for one bracket game.

    Partitions the simulation set by who won the focal bracket game, then sums
    |P(fate = level | higher won) - P(fate = level | lower won)| over the five
    exclusive round-reached fate levels, for the TWO TEAMS PLAYING only, minus
    the analytic noise floor (same correction as compute_importance_from_matrix),
    clamped at 0.

    Participants-only, unlike the regular season's all-teams sum: in the regular
    season a team not playing has real stakes (the bubble race), but in a fixed
    no-reseed bracket a non-participant's only stake is which opponent it draws
    — bookkeeping, not fate. Summing over all teams inflated the early rounds,
    where more teams are still alive, and ranked the quarterfinals above the
    Finals.

    Args:
        focal_slot: bracket slot id, e.g. "qf1", "sf2", "f".
        focal_game_num: 1-indexed game number within the series.
        bracket_outcomes: per-sim dict of (slot, game_num) -> did_higher_win.
        fate_levels: per-sim map of team name -> one of FATE_LEVELS.
        participants: the two team names contesting this game.

    Returns:
        Corrected swing (>= 0.0, <= 4.0). Normalize with
        `normalize_postseason_importance`. 4.0 is the structural maximum: a
        win-or-go-home game moves each participant 2 units of total variation.
        Returns 0.0 if either partition bucket is empty (focal game didn't
        happen in any sim, or all sims agree on the outcome).
    """
    higher_indices, lower_indices = _partition_bracket(
        bracket_outcomes, focal_slot, focal_game_num
    )
    if not higher_indices or not lower_indices:
        return 0.0

    n_h, n_l = len(higher_indices), len(lower_indices)
    swing = 0.0
    floor = 0.0
    for team in participants:
        counts_h = _fate_counts(higher_indices, fate_levels, team)
        counts_l = _fate_counts(lower_indices, fate_levels, team)
        for level in FATE_LEVELS:
            count_h = counts_h[level]
            count_l = counts_l[level]
            swing += abs(count_h / n_h - count_l / n_l)
            floor += _noise_floor_term((count_h + count_l) / (n_h + n_l), n_h, n_l)
    return max(0.0, swing - floor)


def compute_postseason_movers_from_matrix(
    focal_slot: str,
    focal_game_num: int,
    bracket_outcomes: list[dict[tuple[str, int], bool]],
    champions: list[str | None],
    team_names: list[str],
    top_n: int = 3,
    min_delta: float = 0.03,
) -> list[dict]:
    """Per-team directional championship-odds movers for one bracket game.

    Partitions sims by who won the focal bracket game (same split as
    ``compute_postseason_swing_from_matrix``), then computes
    P(champion | higher won) and P(champion | lower won) for each team.
    Returns up to ``top_n`` teams by ``|if_higher - if_lower|`` clearing
    ``min_delta``, sorted descending. Returns ``[]`` if either bucket is empty.
    Each dict: ``{"team": str, "if_higher": float, "if_lower": float}``; the
    caller maps higher/lower to the matchup's team_a/team_b for display.
    """
    higher_indices, lower_indices = _partition_bracket(
        bracket_outcomes, focal_slot, focal_game_num
    )
    if not higher_indices or not lower_indices:
        return []

    def champ_rate(indices: list[int], team: str) -> float:
        return sum(1 for i in indices if champions[i] == team) / len(indices)

    movers: list[dict] = []
    for team in team_names:
        rate_h = champ_rate(higher_indices, team)
        rate_l = champ_rate(lower_indices, team)
        if abs(rate_h - rate_l) >= min_delta:
            movers.append({"team": team, "if_higher": rate_h, "if_lower": rate_l})

    movers.sort(key=lambda m: abs(m["if_higher"] - m["if_lower"]), reverse=True)
    return movers[:top_n]
