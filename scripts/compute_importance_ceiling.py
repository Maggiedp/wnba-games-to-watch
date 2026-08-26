"""Compute the regular-season importance ceiling from a completed prior season.

Walks the target season chronologically. For each date, seeds every season team
at 0-0 and applies results strictly before that date — mirroring production
daily_update.compute_standings — then runs one seeded 10k Monte Carlo over the
full remaining schedule and reads each same-day game's corrected all-team swing
from compute_importance_from_matrix, the same method the daily job uses live. A
game's swing peaks on the day it's played (standings most developed), so
recording same-day swings captures each game's peak.

The per-date RNG seed makes the pinned value reproducible (re-running yields an
identical peak) — it is a determinism device for the calibration, not
production's operational seed (which is the last completed date); the season
peak is robust to the choice. Reports the max + high percentiles of the swing
distribution — pin the max (or p99 if the max is a lone outlier) as
REGULAR_SEASON_MAX_SWING in src/scoring/importance.py.

Offline, one-time per offseason. Re-run on the newest completed season. Takes
~10-20 min (a 10k MC per game-date). Per-date progress -> stderr; final
distribution -> stdout.
"""

from __future__ import annotations

import random
import statistics
import sys
from datetime import date

from src.data.espn_api import fetch_games_for_range
from src.scoring.elo import INITIAL_RATING, replay_games
from src.scoring.monte_carlo import (
    _noise_floor_term,
    _partition_outcomes,
    compute_importance_from_matrix,
    run_monte_carlo_simulation,
)
from src.scoring.tiebreakers import increment_h2h

SEASON_YEAR = 2025
NUM_SIMULATIONS = 10000


def _legacy_swing(
    outcome_matrix: list[list[bool | None]],
    playoff_sets: list[set[str]],
    remaining_games: list[tuple[str, str]],
    team_names: list[str],
) -> list[float]:
    """Reproduce the PRE-CHANGE binary make-playoffs swing (as it existed
    before commit 5c08fb5), for a one-time old-vs-new comparison against
    identical simulation draws. This is a throwaway helper for Task 5's
    measurement step ONLY — it duplicates rather than shares logic with
    compute_importance_from_matrix on purpose, and both --fate=old and this
    function are deleted once the new ceiling is pinned (see the plan's
    Task 5 Step 5).
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
            count_a = sum(1 for s in a_indices if team in playoff_sets[s])
            count_b = sum(1 for s in b_indices if team in playoff_sets[s])
            swing += abs(count_a / n_a - count_b / n_b)
            floor += _noise_floor_term((count_a + count_b) / (n_a + n_b), n_a, n_b)
        swings.append(max(0.0, swing - floor))

    return swings


def main() -> None:
    # --fate=old runs the pre-change binary make-playoffs swing (via the
    # local _legacy_swing throwaway helper) for a one-time comparison against
    # --fate=new (the default, current production logic). Bare `in sys.argv`
    # membership matches the house convention (scripts/backfill_excitement.py).
    fate = "old" if "--fate=old" in sys.argv[1:] else "new"

    season = str(SEASON_YEAR)
    print(f"fate mode: {fate}", file=sys.stderr)
    print(f"Fetching 2024-{SEASON_YEAR} games from ESPN...", file=sys.stderr)
    history = fetch_games_for_range(date(2024, 5, 1), date(SEASON_YEAR, 10, 31))
    # Completed regular-season games only (season_type == 2), with a winner.
    completed = [
        g for g in history if g.get("winner_team") and g.get("season_type", 2) == 2
    ]
    completed.sort(key=lambda g: (g.get("date", ""), g.get("time", "")))

    # Target-season regular-season games, the ones we evaluate.
    season_games = [g for g in completed if g.get("date", "").startswith(season)]
    game_dates = sorted({g["date"] for g in season_games})
    print(
        f"  {len(season_games)} {season} regular-season games over "
        f"{len(game_dates)} dates",
        file=sys.stderr,
    )

    # Full season team set, seeded 0-0 each date exactly like production
    # daily_update.compute_standings (which initializes every known team before
    # tallying results) — so the sim's team pool + schedule match the live
    # ranking path, not just the subset that has already played.
    all_season_teams = {
        name for g in season_games for name in (g["team_a"], g["team_b"])
    }

    peak_swing = 0.0
    peak_game = ""
    all_swings: list[float] = []

    for idx, d in enumerate(game_dates, start=1):
        # Deterministic per-date RNG so the pinned ceiling is reproducible;
        # production seeds its Monte Carlo before each run the same way.
        random.seed(int(d.replace("-", "")))

        # Standings + Elo as of the morning of date d: results strictly before d
        # applied on top of an all-teams-0-0 seed (mirrors compute_standings).
        prior = [g for g in completed if g.get("date", "") < d]
        elo = replay_games(prior).final_ratings
        standings: dict[str, dict] = {
            name: {
                "wins": 0,
                "losses": 0,
                "h2h": {},
                "elo": elo.get(name, INITIAL_RATING),
            }
            for name in sorted(all_season_teams)
        }
        for g in prior:
            if not g.get("date", "").startswith(season):
                continue
            winner = g.get("winner_team")
            a, b = g.get("team_a"), g.get("team_b")
            loser = b if winner == a else a
            if not winner or not loser:
                continue
            standings[winner]["wins"] += 1
            standings[loser]["losses"] += 1
            # h2h ([wins, losses]) is required by resolve_seeding's tiebreakers;
            # use the shared helper so this mirrors production compute_standings.
            increment_h2h(standings[winner]["h2h"], loser, won=True)
            increment_h2h(standings[loser]["h2h"], winner, won=False)

        # Full remaining schedule from date d onward = the sim universe (no
        # membership filter — every season team is already seeded above).
        remaining_rows = [g for g in season_games if g.get("date", "") >= d]
        remaining = [(g["team_a"], g["team_b"]) for g in remaining_rows]
        if not remaining:
            continue

        # NB: the fate flag only selects which post-processing function
        # consumes the sim output below — run_monte_carlo_simulation itself
        # always computes both playoff_sets and fate_levels off the same
        # random draws, so --fate=old and --fate=new see identical outcome
        # matrices for a given date and are directly comparable.
        _, matrix, playoff_sets, _, _, fate_levels = run_monte_carlo_simulation(
            standings,
            remaining,
            num_simulations=NUM_SIMULATIONS,
            return_matrix=True,
        )
        team_names = list(standings.keys())
        if fate == "old":
            swings = _legacy_swing(matrix, playoff_sets, remaining, team_names)
        else:
            swings = compute_importance_from_matrix(
                matrix, fate_levels, remaining, team_names
            )

        todays = [g for g in remaining_rows if g.get("date", "") == d]
        day_swings = swings[: len(todays)]
        for g, s in zip(todays, day_swings):
            all_swings.append(s)
            if s > peak_swing:
                peak_swing = s
                peak_game = f"{d}  {g['team_a']} vs {g['team_b']}"
        day_max = max(day_swings) if day_swings else 0.0
        print(
            f"  [{idx}/{len(game_dates)}] {d}: {len(todays)} games, "
            f"day_max={day_max:.4f}, peak_so_far={peak_swing:.4f}",
            file=sys.stderr,
        )

    all_swings.sort()

    def pct(p: float) -> float:
        if not all_swings:
            return 0.0
        k = min(len(all_swings) - 1, int(round(p / 100 * (len(all_swings) - 1))))
        return all_swings[k]

    print(
        f"\n=== {season} regular-season importance swing distribution (fate={fate}) ==="
    )
    print(f"  games evaluated : {len(all_swings)}")
    print(f"  max  : {peak_swing:.4f}   ({peak_game})")
    print(f"  p99  : {pct(99):.4f}")
    print(f"  p95  : {pct(95):.4f}")
    if all_swings:
        print(f"  mean : {statistics.mean(all_swings):.4f}")
    print(
        "\nPin REGULAR_SEASON_MAX_SWING to the max, or to p99 if the max is a "
        "lone outlier well above p99."
    )


if __name__ == "__main__":
    main()
