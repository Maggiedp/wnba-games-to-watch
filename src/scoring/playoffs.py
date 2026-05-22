"""Playoff bracket simulation for WNBA.

Pure functions, no I/O. Uses the same Elo-based `simulate_game` from
monte_carlo.py so playoff games are consistent with regular-season sims.

Bracket is fixed (no reseeding, per WNBA rules since 2024).

The simulator can optionally consume a `BracketState` reconstructed from
completed postseason games — decided series use the observed winner,
in-progress series resume from the actual score with the correct home
pattern offset. This keeps the round probabilities honest once playoffs
have started; without it the sim would replay the bracket from scratch
each daily run regardless of real-world results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.scoring.elo import DEFAULT_HOME_ADVANTAGE
from src.scoring.monte_carlo import simulate_game

if TYPE_CHECKING:
    from src.scoring.monte_carlo import TeamStanding


# Home-court patterns. "H" = higher seed hosts, "L" = lower seed hosts.
HOME_PATTERN_BO3 = ("H", "L", "H")  # First Round (1-1-1)
HOME_PATTERN_BO5 = ("H", "H", "L", "L", "H")  # Semifinals (2-2-1)
HOME_PATTERN_BO7 = ("H", "H", "L", "L", "H", "L", "H")  # Finals (2-2-1-1-1)

_GAMES_NEEDED_BO3 = 2
_GAMES_NEEDED_BO5 = 3
_GAMES_NEEDED_BO7 = 4


@dataclass
class SeriesState:
    """Observed state of one bracket series.

    higher/lower are None for SF/F slots before upstream rounds resolve.
    winner is set when wins reach `games_needed`.
    """

    higher: str | None = None
    lower: str | None = None
    higher_wins: int = 0
    lower_wins: int = 0
    winner: str | None = None
    games_needed: int = 0


BracketState = dict[str, SeriesState]


def play_series(
    higher_seed: str,
    lower_seed: str,
    home_pattern: tuple[str, ...],
    standings: dict[str, "TeamStanding"],
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    starting_higher_wins: int = 0,
    starting_lower_wins: int = 0,
    recorder: list[bool] | None = None,
) -> str:
    """Simulate a best-of-N. Returns the winning team name.

    Each game in the pattern is simulated with home-court advantage applied
    to whichever team is hosting that game. The series short-circuits as
    soon as one team has the majority wins required.

    `starting_higher_wins` / `starting_lower_wins` resume an in-progress
    series; the simulator skips the games already played in `home_pattern`.
    If the starting score already decides the series, returns the winner
    without simulating anything.

    `recorder`, if provided, has one bool appended per game *actually
    simulated* (True = higher seed won). Pre-played games (starting_*_wins)
    are not appended — only the games this call simulates.
    """
    games_needed = len(home_pattern) // 2 + 1
    higher_wins = starting_higher_wins
    lower_wins = starting_lower_wins
    if higher_wins >= games_needed:
        return higher_seed
    if lower_wins >= games_needed:
        return lower_seed

    games_played = higher_wins + lower_wins
    remaining_pattern = home_pattern[games_played:]
    higher_elo = standings[higher_seed].elo
    lower_elo = standings[lower_seed].elo

    for host in remaining_pattern:
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

        if recorder is not None:
            recorder.append(higher_won)

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


def empty_bracket_state(seeded: list[str]) -> BracketState:
    """Initialize a BracketState with QF matchups filled from seeding,
    SF/F slots empty (filled lazily by reconstruct_bracket_state)."""
    if len(seeded) != 8:
        raise ValueError(
            f"empty_bracket_state requires exactly 8 seeded teams, got {len(seeded)}"
        )
    return {
        "qf1": SeriesState(
            higher=seeded[0], lower=seeded[7], games_needed=_GAMES_NEEDED_BO3
        ),
        "qf2": SeriesState(
            higher=seeded[3], lower=seeded[4], games_needed=_GAMES_NEEDED_BO3
        ),
        "qf3": SeriesState(
            higher=seeded[2], lower=seeded[5], games_needed=_GAMES_NEEDED_BO3
        ),
        "qf4": SeriesState(
            higher=seeded[1], lower=seeded[6], games_needed=_GAMES_NEEDED_BO3
        ),
        "sf1": SeriesState(games_needed=_GAMES_NEEDED_BO5),
        "sf2": SeriesState(games_needed=_GAMES_NEEDED_BO5),
        "f": SeriesState(games_needed=_GAMES_NEEDED_BO7),
    }


def reconstruct_bracket_state(
    seeded: list[str],
    completed_postseason_games: list[dict],
) -> BracketState:
    """Walk completed postseason games and build the current bracket state.

    Args:
        seeded: top-8 team names in seed order (index 0 = #1 seed).
        completed_postseason_games: list of dicts with keys
            `team_a`, `team_b`, `winner_team`, and `date` (for ordering).
            Order doesn't matter — sorted internally.

    Returns:
        BracketState keyed by 'qf1'..'qf4', 'sf1', 'sf2', 'f'. Series whose
        upstream round hasn't resolved have `higher=None`, `lower=None`.

    Games that don't fit the current bracket state (e.g. teams not in the
    seeded list, winner not in the matched pair) are skipped.
    """
    state = empty_bracket_state(seeded)
    sorted_games = sorted(
        completed_postseason_games,
        key=lambda g: (g.get("date", ""), str(g.get("event_id", "") or "")),
    )

    for game in sorted_games:
        team_a = game.get("team_a")
        team_b = game.get("team_b")
        winner = game.get("winner_team")
        if not team_a or not team_b or not winner:
            continue
        pair = {team_a, team_b}

        match_id = None
        for sid, s in state.items():
            if (
                s.higher
                and s.lower
                and s.winner is None
                and {s.higher, s.lower} == pair
            ):
                match_id = sid
                break
        if match_id is None:
            continue

        s = state[match_id]
        if winner == s.higher:
            s.higher_wins += 1
        elif winner == s.lower:
            s.lower_wins += 1
        else:
            continue

        if s.higher_wins >= s.games_needed:
            s.winner = s.higher
        elif s.lower_wins >= s.games_needed:
            s.winner = s.lower
        else:
            continue  # series still in flight; no downstream slot to populate

        _populate_downstream_slots(state, seeded)

    return state


def _populate_downstream_slots(state: BracketState, seeded: list[str]) -> None:
    """Fill SF and F higher/lower when upstream winners are known."""
    pairs = [("sf1", "qf1", "qf2"), ("sf2", "qf3", "qf4")]
    for sf_id, q1, q2 in pairs:
        if state[sf_id].higher is not None:
            continue
        w1, w2 = state[q1].winner, state[q2].winner
        if w1 and w2:
            state[sf_id].higher, state[sf_id].lower = _higher_lower(w1, w2, seeded)
    if state["f"].higher is None:
        s1, s2 = state["sf1"].winner, state["sf2"].winner
        if s1 and s2:
            state["f"].higher, state["f"].lower = _higher_lower(s1, s2, seeded)


def simulate_playoffs(
    seeded: list[str],
    standings: dict[str, "TeamStanding"],
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    bracket_state: BracketState | None = None,
    recorder: dict[tuple[str, int], bool] | None = None,
) -> dict[str, set[str] | str]:
    """Play the full 8-team WNBA bracket (no reseeding).

    If `bracket_state` is provided, decided series use the recorded winner
    and in-progress series resume from the recorded score. Otherwise the
    bracket plays out from scratch.

    `recorder`, if provided, accumulates `(slot_id, game_num_in_series) -> bool`
    entries for every game actually simulated inside this call. Game numbers
    are 1-indexed and continue past the starting score for resumed series
    (e.g. a series resumed at 1-1 records the next game as game 3).

    When `bracket_state` is provided AND a slot has a canonical pair
    (`higher` and `lower` both set), the recorder writes entries for that
    slot ONLY in sims where the sim's bracket participants match the
    canonical pair. Sims where per-sim seeding has drifted from observed
    (e.g. last regular-season games shuffled standings differently in this
    sim) are excluded from the recorder for matched slots, so each
    `(slot_id, game_num)` key references one consistent team pair across
    sims and `compute_postseason_swing_from_matrix` produces a semantically
    meaningful partition. Slots without a canonical pair (bracket_state
    None, or slot's higher/lower still None because upstream is
    unresolved) still record from every sim — those entries are never
    queried, since `_find_bracket_slot` filters them out at lookup time.

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

    def play_or_resume(
        sid: str, higher: str, lower: str, pattern: tuple[str, ...]
    ) -> str:
        s = bracket_state.get(sid) if bracket_state is not None else None
        slot_pair_canonical = (
            s is not None and s.higher is not None and s.lower is not None
        )
        observed_match = slot_pair_canonical and {s.higher, s.lower} == {
            higher,
            lower,
        }
        # Determine starting wins so the local-recorder offset matches the
        # caller's expected game numbers.
        if observed_match:
            if s.higher == higher:
                start_h, start_l = s.higher_wins, s.lower_wins
            else:
                start_h, start_l = s.lower_wins, s.higher_wins
            if s.winner is not None:
                return s.winner
        else:
            start_h, start_l = 0, 0

        # Recorder gate: only record for slots whose canonical pair this
        # sim matches. Slots without a canonical pair record from every
        # sim (harmless — queries filter them out).
        record_this_slot = recorder is not None and (
            not slot_pair_canonical or observed_match
        )

        if not record_this_slot:
            return play_series(
                higher,
                lower,
                pattern,
                standings,
                home_advantage,
                starting_higher_wins=start_h,
                starting_lower_wins=start_l,
            )

        local_recorder: list[bool] = []
        winner = play_series(
            higher,
            lower,
            pattern,
            standings,
            home_advantage,
            starting_higher_wins=start_h,
            starting_lower_wins=start_l,
            recorder=local_recorder,
        )
        offset = start_h + start_l
        for i, higher_won in enumerate(local_recorder):
            recorder[(sid, offset + i + 1)] = higher_won
        return winner

    made_playoffs: set[str] = set(seeded)

    qf1 = play_or_resume("qf1", seeded[0], seeded[7], HOME_PATTERN_BO3)
    qf2 = play_or_resume("qf2", seeded[3], seeded[4], HOME_PATTERN_BO3)
    qf3 = play_or_resume("qf3", seeded[2], seeded[5], HOME_PATTERN_BO3)
    qf4 = play_or_resume("qf4", seeded[1], seeded[6], HOME_PATTERN_BO3)

    reached_semis: set[str] = {qf1, qf2, qf3, qf4}

    sf1_higher, sf1_lower = _higher_lower(qf1, qf2, seeded)
    sf2_higher, sf2_lower = _higher_lower(qf3, qf4, seeded)
    sf1 = play_or_resume("sf1", sf1_higher, sf1_lower, HOME_PATTERN_BO5)
    sf2 = play_or_resume("sf2", sf2_higher, sf2_lower, HOME_PATTERN_BO5)

    reached_finals: set[str] = {sf1, sf2}

    f_higher, f_lower = _higher_lower(sf1, sf2, seeded)
    champion = play_or_resume("f", f_higher, f_lower, HOME_PATTERN_BO7)

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
