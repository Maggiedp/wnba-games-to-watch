"""Compare old (two-team-only) vs new (all-team) importance swing on real 2025 data.

Picks a late-2025 snapshot date (real bubble race), builds standings as of that
date, and scores the next 30 days of games under both metrics. Prints the top
games by each, plus the biggest re-rankings between them.

Offline analysis tool — referenced from src/scoring/importance.py and CLAUDE.md
for the empirical max_swing calibration. Re-run after each new season to verify
the scale still holds.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from src.data.espn_api import fetch_games_for_range
from src.scoring.elo import INITIAL_RATING, replay_games
from src.scoring.importance import normalize_importance_score
from src.scoring.monte_carlo import run_monte_carlo_simulation

# Late 2025 — well into bubble territory (regular season ends ~Sep 11, 2025)
SNAPSHOT_DATE = date(2025, 8, 18)
WINDOW_DAYS = 30
NUM_SIMULATIONS = 2000
MAX_SWING = 0.75


def compute_swings(
    current_standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    game_index: int,
) -> tuple[float, float]:
    """Run Monte Carlo once, return (old two-team-only, new all-team) swings.

    Sharing the two MC runs across both metrics halves runtime vs. computing
    each independently.
    """
    if game_index >= len(remaining_games):
        return 0.0, 0.0
    team_a, team_b = remaining_games[game_index]
    if team_a not in current_standings or team_b not in current_standings:
        return 0.0, 0.0
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

    old = abs(probs_a.get(team_a, 0.0) - probs_b.get(team_a, 0.0)) + abs(
        probs_a.get(team_b, 0.0) - probs_b.get(team_b, 0.0)
    )
    new = sum(
        abs(probs_a.get(name, 0.0) - probs_b.get(name, 0.0))
        for name in current_standings
    )
    return old, new


def main() -> None:
    print(f"Snapshot: {SNAPSHOT_DATE}, simulating {WINDOW_DAYS} days forward")
    print("Fetching 2024 + 2025 games...")
    history = fetch_games_for_range(date(2024, 5, 1), SNAPSHOT_DATE - timedelta(days=1))
    completed = [
        g for g in history if g.get("winner_team") and g.get("season_type", 2) != 1
    ]
    print(f"  {len(completed)} completed games for Elo replay")

    elo_ratings = replay_games(completed).final_ratings

    season_prefix = str(SNAPSHOT_DATE.year)
    standings: dict[str, dict] = {}
    for g in completed:
        if not g.get("date", "").startswith(season_prefix):
            continue
        winner = g.get("winner_team")
        team_a, team_b = g.get("team_a"), g.get("team_b")
        loser = team_b if winner == team_a else team_a
        if not winner or not loser:
            continue
        for name in (winner, loser):
            standings.setdefault(
                name,
                {"wins": 0, "losses": 0, "elo": elo_ratings.get(name, INITIAL_RATING)},
            )
        standings[winner]["wins"] += 1
        standings[loser]["losses"] += 1

    print(
        f"  {len(standings)} teams in {season_prefix} standings as of {SNAPSHOT_DATE}"
    )
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

    random.seed(int(SNAPSHOT_DATE.strftime("%Y%m%d")))
    rows = []
    for i, g in enumerate(upcoming):
        s_old, s_new = compute_swings(standings, remaining, i)
        rows.append(
            {
                "date": g.get("date", ""),
                "matchup": f"{g['team_a']} @ {g['team_b']}",
                "swing_old": s_old,
                "swing_new": s_new,
                "score_old": normalize_importance_score(s_old, max_swing=MAX_SWING),
                "score_new": normalize_importance_score(s_new, max_swing=MAX_SWING),
            }
        )

    def fmt(r: dict) -> str:
        return (
            f"  {r['date']}  {r['matchup']:50s}  "
            f"old={r['score_old']:5.1f} ({r['swing_old']:.2f})  "
            f"new={r['score_new']:5.1f} ({r['swing_new']:.2f})"
        )

    print(f"\n=== Top 10 by OLD metric (two-team only, max_swing={MAX_SWING}) ===")
    for r in sorted(rows, key=lambda r: -r["score_old"])[:10]:
        print(fmt(r))

    print(f"\n=== Top 10 by NEW metric (all-team, max_swing={MAX_SWING}) ===")
    for r in sorted(rows, key=lambda r: -r["score_new"])[:10]:
        print(fmt(r))

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
