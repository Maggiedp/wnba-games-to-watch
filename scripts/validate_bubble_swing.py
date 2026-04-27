"""Compare old (two-team-only) vs new (all-team) importance swing on real 2025 data.

Picks a late-2025 snapshot date (real bubble race), builds standings as of that
date, and scores the next 30 days of games under both metrics. Prints the top
games by each, plus the biggest re-rankings between them.

One-off offline analysis — not used in production. Safe to delete after review.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta

from src.data.espn_api import fetch_games_for_range
from src.scoring.elo import INITIAL_RATING, replay_games
from src.scoring.importance import normalize_importance_score
from src.scoring.monte_carlo import (
    run_monte_carlo_simulation,
)

# Late 2025 — well into bubble territory (regular season ends ~Sep 11, 2025)
SNAPSHOT_DATE = date(2025, 8, 18)
WINDOW_DAYS = 30
NUM_SIMULATIONS = 2000


def old_swing(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    game_index: int,
) -> float:
    """Two-team-only swing — the metric before this change."""
    if game_index >= len(remaining_games):
        return 0.0
    team_a, team_b = remaining_games[game_index]
    if team_a not in current_standings or team_b not in current_standings:
        return 0.0
    games_without = remaining_games[:game_index] + remaining_games[game_index + 1 :]

    s_a = {n: dict(d) for n, d in current_standings.items()}
    s_a[team_a]["wins"] += 1
    s_a[team_b]["losses"] += 1
    probs_a = run_monte_carlo_simulation(
        s_a, games_without, num_simulations=NUM_SIMULATIONS
    )

    s_b = {n: dict(d) for n, d in current_standings.items()}
    s_b[team_b]["wins"] += 1
    s_b[team_a]["losses"] += 1
    probs_b = run_monte_carlo_simulation(
        s_b, games_without, num_simulations=NUM_SIMULATIONS
    )

    swing_a = abs(probs_a.get(team_a, 0.0) - probs_b.get(team_a, 0.0))
    swing_b = abs(probs_b.get(team_b, 0.0) - probs_a.get(team_b, 0.0))
    return swing_a + swing_b


def new_swing(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    game_index: int,
) -> float:
    """All-team swing — the new metric."""
    if game_index >= len(remaining_games):
        return 0.0
    team_a, team_b = remaining_games[game_index]
    if team_a not in current_standings or team_b not in current_standings:
        return 0.0
    games_without = remaining_games[:game_index] + remaining_games[game_index + 1 :]

    s_a = {n: dict(d) for n, d in current_standings.items()}
    s_a[team_a]["wins"] += 1
    s_a[team_b]["losses"] += 1
    probs_a = run_monte_carlo_simulation(
        s_a, games_without, num_simulations=NUM_SIMULATIONS
    )

    s_b = {n: dict(d) for n, d in current_standings.items()}
    s_b[team_b]["wins"] += 1
    s_b[team_a]["losses"] += 1
    probs_b = run_monte_carlo_simulation(
        s_b, games_without, num_simulations=NUM_SIMULATIONS
    )

    return sum(
        abs(probs_a.get(name, 0.0) - probs_b.get(name, 0.0))
        for name in current_standings
    )


def main() -> None:
    print(f"Snapshot: {SNAPSHOT_DATE}, simulating {WINDOW_DAYS} days forward")
    print("Fetching 2024 + 2025 games...")
    history = fetch_games_for_range(date(2024, 5, 1), SNAPSHOT_DATE - timedelta(days=1))
    completed = [
        g for g in history if g.get("winner_team") and g.get("season_type", 2) != 1
    ]
    print(f"  {len(completed)} completed games for Elo replay")

    elo_ratings = replay_games(completed).final_ratings

    standings: dict[str, dict] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "elo": INITIAL_RATING}
    )
    for g in completed:
        if g.get("date", "") < "2025-01-01":
            continue
        winner = g.get("winner_team")
        loser = g.get("team_a") if winner == g.get("team_b") else g.get("team_b")
        if winner and loser:
            standings[winner]["wins"] += 1
            standings[loser]["losses"] += 1
    for name in list(standings):
        standings[name]["elo"] = elo_ratings.get(name, INITIAL_RATING)
    standings = dict(standings)
    print(f"  {len(standings)} teams in 2025 standings as of {SNAPSHOT_DATE}")
    for name, d in sorted(standings.items(), key=lambda x: x[1]["wins"], reverse=True):
        print(f"    {name:30s}  {d['wins']:2d}-{d['losses']:2d}  elo={d['elo']:.0f}")

    end = SNAPSHOT_DATE + timedelta(days=WINDOW_DAYS)
    upcoming = fetch_games_for_range(SNAPSHOT_DATE, end)
    upcoming = [
        g
        for g in upcoming
        if g.get("season_type", 2) != 1
        and g.get("team_a") in standings
        and g.get("team_b") in standings
    ]
    upcoming.sort(key=lambda g: (g.get("date", ""), g.get("time", "")))
    remaining = [(g["team_a"], g["team_b"]) for g in upcoming]
    print(f"  {len(remaining)} upcoming games in window")

    random.seed(20250825)
    rows = []
    for i, g in enumerate(upcoming):
        s_old = old_swing(standings, remaining, i)
        s_new = new_swing(standings, remaining, i)
        rows.append(
            {
                "date": g.get("date", ""),
                "matchup": f"{g['team_a']} @ {g['team_b']}",
                "swing_old": s_old,
                "swing_new": s_new,
                "score_old": normalize_importance_score(s_old, max_swing=0.75),
                "score_new": normalize_importance_score(s_new, max_swing=0.75),
            }
        )

    print("\n=== Top 10 by OLD metric (two-team only, max_swing=0.75) ===")
    for r in sorted(rows, key=lambda r: -r["score_old"])[:10]:
        print(
            f"  {r['date']}  {r['matchup']:50s}  "
            f"old={r['score_old']:5.1f} ({r['swing_old']:.2f})  "
            f"new={r['score_new']:5.1f} ({r['swing_new']:.2f})"
        )

    print("\n=== Top 10 by NEW metric (all-team, max_swing=1.5) ===")
    for r in sorted(rows, key=lambda r: -r["score_new"])[:10]:
        print(
            f"  {r['date']}  {r['matchup']:50s}  "
            f"old={r['score_old']:5.1f} ({r['swing_old']:.2f})  "
            f"new={r['score_new']:5.1f} ({r['swing_new']:.2f})"
        )

    print("\n=== Biggest score gains (new - old) — bubble watchers picked up ===")
    for r in sorted(rows, key=lambda r: -(r["score_new"] - r["score_old"]))[:10]:
        delta = r["score_new"] - r["score_old"]
        print(
            f"  {r['date']}  {r['matchup']:50s}  "
            f"old={r['score_old']:5.1f}  new={r['score_new']:5.1f}  Δ={delta:+5.1f}"
        )

    print("\n=== Biggest score drops (new - old) ===")
    for r in sorted(rows, key=lambda r: r["score_new"] - r["score_old"])[:10]:
        delta = r["score_new"] - r["score_old"]
        print(
            f"  {r['date']}  {r['matchup']:50s}  "
            f"old={r['score_old']:5.1f}  new={r['score_new']:5.1f}  Δ={delta:+5.1f}"
        )


if __name__ == "__main__":
    main()
