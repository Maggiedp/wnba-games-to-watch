"""Elo rating engine for WNBA team strength.

Ratings are updated game-by-game in chronological order. Each game's
pre-game ratings are used to predict the winner (no look-ahead bias),
so replaying the season also produces an honest validation trace.

The scale is standard chess Elo: 1500 default, 400-point diff → ~91%
win probability. K is the responsiveness knob — higher K means ratings
move more per game. Games are treated as neutral-site (matches the
current Monte Carlo assumption).
"""

from __future__ import annotations

from dataclasses import dataclass, field

INITIAL_RATING = 1500.0
# Calibrated against 2024 (warm-up) + 2025 (eval, 311 games) WNBA results.
# Optimal (K, H) = (28, 50) by log-loss grid search. The loss surface is flat
# within ±5 of each, so the exact values aren't load-bearing — re-validate
# after the 2026 season via scripts/validate_elo.py.
DEFAULT_K = 28.0
DEFAULT_HOME_ADVANTAGE = 50.0
_ELO_SCALE = 400.0


def expected_win_prob(
    rating_a: float,
    rating_b: float,
    home_advantage: float = 0.0,
) -> float:
    """Probability that team A beats team B.

    `home_advantage` is an Elo-point bonus added to team A. Pass the home bonus
    when A is at home, negate it when A is the road team, or leave 0 for neutral.
    """
    return 1.0 / (1.0 + 10 ** ((rating_b - (rating_a + home_advantage)) / _ELO_SCALE))


def update_ratings(
    rating_a: float,
    rating_b: float,
    team_a_won: bool,
    k: float = DEFAULT_K,
    home_advantage: float = 0.0,
) -> tuple[float, float]:
    """Return new (rating_a, rating_b) after one game.

    Zero-sum: whatever A gains, B loses. The update uses the home-adjusted
    expected value, so a home favorite gets less credit for winning than a
    road favorite would.
    """
    expected_a = expected_win_prob(rating_a, rating_b, home_advantage)
    actual_a = 1.0 if team_a_won else 0.0
    delta = k * (actual_a - expected_a)
    return rating_a + delta, rating_b - delta


@dataclass
class EloReplay:
    """Result of replaying a list of games through the Elo engine."""

    final_ratings: dict[str, float]
    # One entry per game, in the order games were processed:
    # {"team_a", "team_b", "pre_a", "pre_b", "winner", "date"}
    history: list[dict] = field(default_factory=list)


def replay_games(
    games: list[dict],
    initial_ratings: dict[str, float] | None = None,
    k: float = DEFAULT_K,
    home_advantage: float = 0.0,
) -> EloReplay:
    """Replay games chronologically and return final ratings + per-game history.

    Games must each have: team_a, team_b, winner_team (or None to skip), date.
    ESPN marks team_a as the home team in `_parse_event`, so `home_advantage`
    is applied to team A. Games with a falsy winner_team are skipped (unplayed
    / tied / malformed). Games are sorted by (date, event_id) for deterministic
    ordering; ties fall back to input order via a stable sort.
    """
    ratings: dict[str, float] = dict(initial_ratings or {})
    history: list[dict] = []

    ordered = sorted(games, key=lambda g: (g.get("date", ""), g.get("event_id", "")))

    for g in ordered:
        winner = g.get("winner_team")
        if not winner:
            continue
        ta, tb = g["team_a"], g["team_b"]
        if winner not in (ta, tb):
            continue

        ra = ratings.setdefault(ta, INITIAL_RATING)
        rb = ratings.setdefault(tb, INITIAL_RATING)
        team_a_won = winner == ta

        new_ra, new_rb = update_ratings(
            ra, rb, team_a_won, k=k, home_advantage=home_advantage
        )
        ratings[ta] = new_ra
        ratings[tb] = new_rb

        history.append(
            {
                "team_a": ta,
                "team_b": tb,
                "pre_a": ra,
                "pre_b": rb,
                "winner": winner,
                "date": g.get("date", ""),
            }
        )

    return EloReplay(final_ratings=ratings, history=history)
