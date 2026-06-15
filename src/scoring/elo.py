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

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

INITIAL_RATING = 1500.0
# Calibrated against the old Portland Fire (~30% win rate in 2000–01) and
# general WNBA/NBA expansion baselines; maps to ~36% expected win rate vs. a
# league-average opponent. INITIAL_RATING is the regression target (league
# mean); this is a separate, lower prior for genuinely new franchises.
# The Golden State Valkyries (2025) are a known outlier — well-funded and
# built to compete immediately — so their 50% year-1 rate is not the right
# baseline. Re-evaluate after Toronto Tempo and Portland Fire complete 2026.
EXPANSION_SEED_RATING = 1350.0
# Calibrated against 2016–2025 WNBA results (skip 2020 neutral-site bubble),
# 2016 warm-up + 1910 evaluated games. Optimal (K, H) = (16, 45) with MOV on
# by log-loss grid search; H=50 is within the flat region (ΔLogLoss <0.0003).
# K is roughly half the no-MOV optimum (~K=34) because the MOV multiplier
# now carries part of the per-game responsiveness K used to provide alone.
# Re-validate after the 2026 season via scripts/validate_elo.py.
DEFAULT_K = 16.0
DEFAULT_HOME_ADVANTAGE = 50.0
# Pull each team's rating this fraction of the way toward INITIAL_RATING at
# the start of every new season. Calibrated against 2016–present WNBA data
# (scripts/validate_regression.py): 0.5 minimizes early-season Brier score
# across 7 season boundaries. Higher than FiveThirtyEight's NBA 1/3 because
# the WNBA's shorter season (~40 games) leaves end-of-season ratings noisier.
DEFAULT_SEASON_REGRESSION = 0.5
_ELO_SCALE = 400.0


def _season_for_date(date_str: str) -> int | None:
    """Season key for an ISO YYYY-MM-DD date, or None if empty.

    WNBA seasons fit inside a calendar year (May–October), so year is a
    sufficient season key.
    """
    if not date_str:
        return None
    return date.fromisoformat(date_str).year


def _regress_toward_mean(ratings: dict[str, float], factor: float) -> None:
    if factor <= 0:
        return
    keep = 1.0 - factor
    for team, rating in ratings.items():
        ratings[team] = INITIAL_RATING + (rating - INITIAL_RATING) * keep


def _mov_multiplier(mov: int, winner_elo_advantage: float) -> float:
    """FiveThirtyEight-style margin-of-victory multiplier.

    `mov` is the absolute final-score margin (winner − loser, > 0).
    `winner_elo_advantage` is the winner's pre-game rating minus the loser's
    rating, *including* the home-court adjustment for the actual home team.
    Negative on upsets, which inflates the multiplier slightly.

    The autocorrelation correction (the 2.2 / (… + 2.2) factor) damps the
    multiplier when a heavy favorite wins big, so dominant teams don't run
    away with the rating system. Returns 1.0 for non-positive MOV (treat as
    "no information").
    """
    if mov <= 0:
        return 1.0
    return math.log(mov + 1) * (2.2 / (winner_elo_advantage * 0.001 + 2.2))


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
    mov: int | None = None,
) -> tuple[float, float]:
    """Return new (rating_a, rating_b) after one game.

    Zero-sum: whatever A gains, B loses. The update uses the home-adjusted
    expected value, so a home favorite gets less credit for winning than a
    road favorite would.

    When `mov` (absolute final-score margin) is provided, the K-factor is
    scaled by `_mov_multiplier` so blowouts move ratings more than narrow
    wins, with autocorrelation damping for heavy favorites.
    """
    expected_a = expected_win_prob(rating_a, rating_b, home_advantage)
    actual_a = 1.0 if team_a_won else 0.0
    multiplier = 1.0
    if mov is not None:
        winner_adv = (
            (rating_a + home_advantage) - rating_b
            if team_a_won
            else rating_b - (rating_a + home_advantage)
        )
        multiplier = _mov_multiplier(mov, winner_adv)
    delta = k * multiplier * (actual_a - expected_a)
    return rating_a + delta, rating_b - delta


@dataclass
class EloReplay:
    """Result of replaying a list of games through the Elo engine."""

    final_ratings: dict[str, float]
    # One entry per game, in the order games were processed:
    # {"team_a", "team_b", "pre_a", "pre_b", "winner", "date", "home_adv"}
    history: list[dict] = field(default_factory=list)


def replay_games(
    games: list[dict],
    initial_ratings: dict[str, float] | None = None,
    k: float = DEFAULT_K,
    home_advantage: float = 0.0,
    season_regression: float = DEFAULT_SEASON_REGRESSION,
    use_mov: bool = True,
    presorted: bool = False,
    rest_travel_adjust: Callable[[dict], float] | None = None,
) -> EloReplay:
    """Replay games chronologically and return final ratings + per-game history.

    Games must each have: team_a, team_b, winner_team (or None to skip), date.
    ESPN marks team_a as the home team in `_parse_event`, so `home_advantage`
    is applied to team A. Games with a falsy winner_team are skipped (unplayed
    / tied / malformed). Games are sorted by (date, event_id) for deterministic
    ordering; ties fall back to input order via a stable sort.

    `season_regression` pulls every existing rating that fraction of the way
    toward INITIAL_RATING at each detected season boundary (year change in the
    ISO date string). Pass 0 to disable. Teams that first appear mid-season
    aren't affected by prior boundaries — they enter at INITIAL_RATING when
    their first game is processed.

    When `use_mov=True`, each game's `final_score_a`/`final_score_b` are read
    and passed to `update_ratings` so blowouts move ratings more than narrow
    wins. Games missing scores still update at multiplier=1.0.

    Pass `presorted=True` when the caller has already sorted by (date, event_id)
    to skip the redundant sort — useful in grid-search loops over thousands of
    replays of the same game list.

    `rest_travel_adjust` is an optional callback that receives the per-game
    rest/travel feature dict and returns a NET Elo delta added to team A
    (positive favors home team A, negative favors away team B). When provided,
    this delta is added to `home_advantage` for both the win-probability
    prediction and the rating update. Each history entry records `home_adv` =
    the effective advantage used. When None, `home_adv` equals `home_advantage`
    and behavior is byte-identical to not passing the parameter.
    """
    ratings: dict[str, float] = dict(initial_ratings or {})
    history: list[dict] = []
    prev_season: int | None = None

    ordered = (
        games
        if presorted
        else sorted(games, key=lambda g: (g.get("date", ""), g.get("event_id", "")))
    )

    features: list[dict] | None = None
    if rest_travel_adjust is not None:
        from src.scoring.rest_travel import compute_rest_travel_features

        # compute_rest_travel_features sorts identically by (date, event_id),
        # so its output aligns with `ordered`.
        features = compute_rest_travel_features(ordered)

    for idx, g in enumerate(ordered):
        winner = g.get("winner_team")
        if not winner:
            continue
        ta, tb = g["team_a"], g["team_b"]
        if winner not in (ta, tb):
            continue

        season = _season_for_date(g.get("date", ""))
        if season is not None:
            if prev_season is not None and season != prev_season:
                _regress_toward_mean(ratings, season_regression)
            prev_season = season

        ra = ratings.setdefault(ta, EXPANSION_SEED_RATING)
        rb = ratings.setdefault(tb, EXPANSION_SEED_RATING)
        team_a_won = winner == ta

        mov: int | None = None
        if use_mov:
            sa, sb = g.get("final_score_a"), g.get("final_score_b")
            if sa is not None and sb is not None:
                mov = abs(int(sa) - int(sb))

        ha = home_advantage
        if features is not None:
            ha = home_advantage + rest_travel_adjust(features[idx])

        new_ra, new_rb = update_ratings(
            ra, rb, team_a_won, k=k, home_advantage=ha, mov=mov
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
                "home_adv": ha,
            }
        )

    return EloReplay(final_ratings=ratings, history=history)


def build_elo_timeline(
    history: list[dict], season_prefix: str
) -> dict[str, list[dict]]:
    """Per-team rating-entering-each-game timeline for one season.

    `history` is `EloReplay.history` (already chronological). For each game whose
    `date` starts with `season_prefix` (e.g. "2026"), record the pre-game rating
    each team carried into that game (`pre_a` for team_a, `pre_b` for team_b).
    The result is `{team_name: [{"date", "rating"}, ...]}` in date order — each
    team's rating trajectory across the season. We plot the rating a team *enters*
    each game with; the post-game update shows up as the next game's entry point.
    """
    timeline: dict[str, list[dict]] = {}
    for g in history:
        if not g["date"].startswith(season_prefix):
            continue
        timeline.setdefault(g["team_a"], []).append(
            {"date": g["date"], "rating": g["pre_a"]}
        )
        timeline.setdefault(g["team_b"], []).append(
            {"date": g["date"], "rating": g["pre_b"]}
        )
    return timeline
