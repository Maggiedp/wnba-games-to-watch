#!/usr/bin/env python3
"""Validate whether the static-Elo Monte Carlo produces calibrated playoff odds.

For each season 2017–2025 (skip 2020), takes three snapshots (June 15, July 15,
August 15), builds standings + Elo ratings from completed games up to that date,
runs the current Monte Carlo, and compares predicted playoff odds to actual outcomes.

Overconfidence = 90–100% predicted bucket has actual rate materially below predicted.

Run from repo root with venv active:
    python -m scripts.validate_playoff_odds
"""

from __future__ import annotations

from datetime import date

from src.scoring.elo import INITIAL_RATING
from src.scoring.monte_carlo import TeamStanding
from src.scoring.tiebreakers import PLAYOFF_TEAMS, increment_h2h, resolve_seeding

_SEASONS = [
    (date(y, 5, 1), date(y, 10, 31), str(y))
    for y in [2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025]
]
_EVAL_YEARS = {2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025}
_SNAPSHOT_MD = ["06-15", "07-15", "08-15"]
_MIN_COMPLETED = 5
_NUM_SIMS = 2000
# Custom bucket boundaries: finer split at the top where overconfidence shows up.
_CALIBRATION_BOUNDS = [0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.01]


def _build_standings(
    season_games: list[dict],
    elo_ratings: dict[str, float],
    up_to_date: str | None = None,
) -> dict[str, dict]:
    """Build standings dict from completed non-preseason games.

    up_to_date: inclusive cutoff (YYYY-MM-DD). None = include all games.
    Returns dict with wins/losses/elo/h2h — same structure run_monte_carlo_simulation expects.
    """
    standings: dict[str, dict] = {}
    for g in season_games:
        if up_to_date is not None and g.get("date", "") > up_to_date:
            continue
        if not g.get("winner_team") or g.get("season_type", 2) == 1:
            continue
        team_a, team_b = g["team_a"], g["team_b"]
        winner = g["winner_team"]
        loser = team_b if winner == team_a else team_a
        for name in (team_a, team_b):
            standings.setdefault(
                name,
                {
                    "wins": 0,
                    "losses": 0,
                    "elo": elo_ratings.get(name, INITIAL_RATING),
                    "h2h": {},
                },
            )
        standings[winner]["wins"] += 1
        standings[loser]["losses"] += 1
        increment_h2h(standings[team_a]["h2h"], team_b, won=(winner == team_a))
        increment_h2h(standings[team_b]["h2h"], team_a, won=(winner == team_b))
    return standings


def _get_remaining_games(
    season_games: list[dict],
    standings: dict[str, dict],
    after_date: str,
) -> list[tuple[str, str]]:
    """Return (home, away) for non-preseason games after after_date with both teams in standings."""
    return [
        (g["team_a"], g["team_b"])
        for g in season_games
        if g.get("date", "") > after_date
        and g.get("season_type", 2) != 1
        and g["team_a"] in standings
        and g["team_b"] in standings
    ]


def _get_actual_playoffs(
    season_games: list[dict],
    end_elo: dict[str, float],
) -> set[str]:
    """Return set of team names that made the playoffs in this season.

    Uses resolve_seeding with the same tiebreaker chain the Monte Carlo uses,
    so the outcome measure is consistent with the model.
    end_elo: end-of-season Elo ratings (used only as the final tiebreaker).
    """
    full_standings = _build_standings(season_games, end_elo)
    if not full_standings:
        return set()
    ts = {
        name: TeamStanding(
            name=name,
            wins=data["wins"],
            losses=data["losses"],
            elo=data["elo"],
            h2h={opp: list(rec) for opp, rec in data["h2h"].items()},
        )
        for name, data in full_standings.items()
    }
    return set(resolve_seeding(ts)[:PLAYOFF_TEAMS])
