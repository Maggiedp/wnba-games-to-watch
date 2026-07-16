"""Compute the regular-season importance ceiling from a completed prior season.

Walks the target season chronologically. For each date, seeds every season team
at 0-0 and applies results strictly before that date — mirroring production
daily_update.compute_standings — then runs one seeded 10k Monte Carlo over the
full remaining schedule and reads each same-day game's corrected all-team swing
from compute_importance_from_matrix, the same method the daily job uses live. A
game's swing peaks on the day it's played (standings most developed), so
recording same-day swings captures each game's peak.

The per-date RNG seed makes the pinned value reproducible (production seeds its
Monte Carlo the same way). Reports the max + high percentiles of the swing
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
    compute_importance_from_matrix,
    run_monte_carlo_simulation,
)

SEASON_YEAR = 2025
NUM_SIMULATIONS = 10000


def main() -> None:
    season = str(SEASON_YEAR)
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
            # h2h: {opponent: [wins, losses]} — required by resolve_seeding's
            # tiebreakers (matches production compute_standings).
            standings[winner]["h2h"].setdefault(loser, [0, 0])[0] += 1
            standings[loser]["h2h"].setdefault(winner, [0, 0])[1] += 1

        # Full remaining schedule from date d onward = the sim universe (no
        # membership filter — every season team is already seeded above).
        remaining_rows = [g for g in season_games if g.get("date", "") >= d]
        remaining = [(g["team_a"], g["team_b"]) for g in remaining_rows]
        if not remaining:
            continue

        _, matrix, playoff_sets, _, _ = run_monte_carlo_simulation(
            standings,
            remaining,
            num_simulations=NUM_SIMULATIONS,
            return_matrix=True,
        )
        team_names = list(standings.keys())
        swings = compute_importance_from_matrix(
            matrix, playoff_sets, remaining, team_names
        )

        todays = [g for g in remaining_rows if g.get("date", "") == d]
        for i in range(len(todays)):
            s = swings[i]
            all_swings.append(s)
            if s > peak_swing:
                peak_swing = s
                peak_game = f"{d}  {todays[i]['team_a']} vs {todays[i]['team_b']}"
        day_max = max(swings[: len(todays)]) if todays else 0.0
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

    print(f"\n=== {season} regular-season importance swing distribution ===")
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
