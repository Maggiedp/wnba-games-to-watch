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

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np

from scripts._calibration import brier_score, log_loss
from src.data.espn_api import fetch_games_for_range
from src.scoring.elo import INITIAL_RATING, replay_games
from src.scoring.monte_carlo import TeamStanding, run_monte_carlo_simulation
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


def _fetch_all_games() -> list[dict]:
    """Fetch all configured seasons in parallel; return chronologically sorted completed games."""

    def _fetch_one(season: tuple[date, date, str]) -> list[dict]:
        start, end, label = season
        games = [
            g
            for g in fetch_games_for_range(start, end)
            if g.get("winner_team") and g.get("season_type", 2) != 1
        ]
        print(f"  {label}: {len(games)} completed regular/postseason games")
        return games

    with ThreadPoolExecutor(max_workers=len(_SEASONS)) as pool:
        results = list(pool.map(_fetch_one, _SEASONS))

    all_games = [g for season_games in results for g in season_games]
    all_games.sort(key=lambda g: (g.get("date", ""), g.get("event_id", "")))
    return all_games


def _playoff_calibration_table(probs: np.ndarray, outcomes: np.ndarray) -> list[dict]:
    rows = []
    for i in range(len(_CALIBRATION_BOUNDS) - 1):
        lo, hi = _CALIBRATION_BOUNDS[i], _CALIBRATION_BOUNDS[i + 1]
        mask = (probs >= lo) & (probs < hi)
        if not mask.any():
            continue
        rows.append(
            {
                "bucket": f"{lo:.0%}–{min(hi, 1.0):.0%}",
                "n": int(mask.sum()),
                "avg_pred": float(probs[mask].mean()),
                "actual": float(outcomes[mask].mean()),
            }
        )
    return rows


def _print_calibration(label: str, probs: np.ndarray, outcomes: np.ndarray) -> None:
    brier = brier_score(probs, outcomes)
    ll = log_loss(probs, outcomes)
    print(
        f"\nCalibration ({label}, N={len(outcomes)}, Brier={brier:.4f}, LogLoss={ll:.4f}):"
    )
    print(f"  {'Bucket':<10} {'N':>5} {'Predicted':>10} {'Actual':>10} {'Error':>8}")
    for row in _playoff_calibration_table(probs, outcomes):
        err = row["actual"] - row["avg_pred"]
        print(
            f"  {row['bucket']:<10} {row['n']:>5} {row['avg_pred']:>10.1%} "
            f"{row['actual']:>10.1%} {err:>+8.1%}"
        )


def main() -> None:
    print("=== Playoff Odds Calibration Validation ===\n")
    print("Fetching historical WNBA games...")
    all_games = _fetch_all_games()
    print(f"Total: {len(all_games)} completed games\n")

    by_year: dict[int, list[dict]] = defaultdict(list)
    for g in all_games:
        year_str = g.get("date", "")[:4]
        if year_str.isdigit():
            by_year[int(year_str)].append(g)

    # (predicted_prob, actual_made_playoffs_float, snapshot_md)
    records: list[tuple[float, float, str]] = []
    # year -> md -> (playoff_probs_dict, actual_playoffs_set) — 2024/2025 only
    sample: dict[int, dict[str, tuple[dict[str, float], set[str]]]] = {}

    for eval_year in sorted(_EVAL_YEARS):
        season_games = by_year.get(eval_year, [])
        if not season_games:
            print(f"{eval_year}: no games found, skipping")
            continue

        # End-of-season Elo for tiebreaker consistency in actual-playoff determination
        cutoff = f"{eval_year}-10-31"
        games_through_season = [g for g in all_games if g.get("date", "") <= cutoff]
        end_elo = replay_games(games_through_season, presorted=True).final_ratings
        actual_playoffs = _get_actual_playoffs(season_games, end_elo)
        print(
            f"\n{eval_year}: {len(season_games)} games  |  actual playoffs: {sorted(actual_playoffs)}"
        )

        for md in _SNAPSHOT_MD:
            snapshot_date = f"{eval_year}-{md}"
            games_through_snapshot = [
                g for g in all_games if g.get("date", "") <= snapshot_date
            ]
            if not games_through_snapshot:
                continue
            snapshot_elo = replay_games(
                games_through_snapshot, presorted=True
            ).final_ratings

            standings = _build_standings(
                season_games, snapshot_elo, up_to_date=snapshot_date
            )
            n_completed = sum(s["wins"] + s["losses"] for s in standings.values()) // 2
            if n_completed < _MIN_COMPLETED:
                print(
                    f"  {md}: {n_completed} completed games — skipping (< {_MIN_COMPLETED})"
                )
                continue

            remaining = _get_remaining_games(
                season_games, standings, after_date=snapshot_date
            )
            if not remaining:
                print(f"  {md}: no remaining games — skipping")
                continue

            playoff_probs = run_monte_carlo_simulation(
                standings, remaining, num_simulations=_NUM_SIMS
            )
            print(
                f"  {md}: {len(standings)} teams, {n_completed} completed, "
                f"{len(remaining)} remaining → MC done"
            )

            for team, prob in playoff_probs.items():
                records.append((prob, float(team in actual_playoffs), md))

            if eval_year in (2024, 2025):
                sample.setdefault(eval_year, {})[md] = (playoff_probs, actual_playoffs)

    if not records:
        print("No data collected — check season fetch.")
        return

    probs_all = np.array([r[0] for r in records])
    outcomes_all = np.array([r[1] for r in records])

    print(f"\n{'=' * 60}")
    print(f"Total observations: {len(records)} (team × snapshot pairs)")
    _print_calibration("All snapshots combined", probs_all, outcomes_all)

    for md in _SNAPSHOT_MD:
        mask = np.array([r[2] == md for r in records])
        if mask.sum() > 0:
            _print_calibration(f"Snapshot {md}", probs_all[mask], outcomes_all[mask])

    # Top-bucket overconfidence verdict
    top_mask = probs_all >= 0.9
    print(f"\n{'=' * 60}")
    if top_mask.sum() > 0:
        top_pred = float(probs_all[top_mask].mean())
        top_actual = float(outcomes_all[top_mask].mean())
        err = top_actual - top_pred
        print(
            f"90–100% bucket: N={top_mask.sum()}, "
            f"predicted={top_pred:.1%}, actual={top_actual:.1%}, error={err:+.1%}"
        )
        if err < -0.05:
            print(
                "OVERCONFIDENCE CONFIRMED: proceed with hot-simulation implementation (Phase 2)."
            )
        elif err > 0.05:
            print("UNDER-CONFIDENCE: top bucket predicted too low by >5pp.")
        else:
            print(
                "Top bucket well-calibrated (error within +/-5pp). Hot sims may not be needed."
            )
    else:
        print("No observations in 90–100% bucket.")

    # Sample check: eyeball 2024 and 2025 Aug-15 predictions vs actual
    for check_year in [2024, 2025]:
        if check_year not in sample or "08-15" not in sample[check_year]:
            continue
        probs_dict, actual = sample[check_year]["08-15"]
        print(f"\nSample: {check_year} Aug 15 predictions vs actuals")
        print(f"  {'Team':<30} {'Predicted':>10} {'Made playoffs':>14}")
        for team, prob in sorted(probs_dict.items(), key=lambda x: -x[1]):
            made = "Yes" if team in actual else "No"
            print(f"  {team:<30} {prob:>10.1%} {made:>14}")


if __name__ == "__main__":
    main()
