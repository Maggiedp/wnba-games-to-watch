#!/usr/bin/env python3
"""Daily update job for WNBA Games to Watch."""

import logging
import random
import sys
from datetime import date, datetime

from src.constants import GameStatus
from src.data.espn_api import (
    fetch_bpi_ratings,
    fetch_games_for_range,
    fetch_schedule_and_results,
    fetch_team_details,
)
from src.data.wnba_schedule import (
    enhance_games_with_broadcasters,
    fetch_wnba_schedule_broadcasters,
)
from src.db.queries import (
    get_all_teams,
    get_completed_games,
    get_importance_max_swing,
    get_team_by_id,
    get_team_by_name,
    save_importance_max_swing,
    upsert_daily_ranking,
    upsert_game,
    upsert_playoff_probability,
    upsert_team,
)
from src.db.schema import get_session, init_db
from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    INITIAL_RATING,
    expected_win_prob,
    replay_games,
)
from src.scoring.importance import normalize_importance_score
from src.scoring.monte_carlo import (
    compute_importance_from_matrix,
    run_monte_carlo_simulation,
)
from src.scoring.quality import compute_quality_score
from src.scoring.tiebreakers import increment_h2h

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/daily_update.log"),
    ],
)
logger = logging.getLogger(__name__)


def _make_team_id_resolver(session):
    """Return a cached team-name → team-id lookup for a single session."""
    cache: dict[str, int | None] = {}

    def resolve(name: str) -> int | None:
        if name not in cache:
            team = get_team_by_name(session, name)
            cache[name] = team.id if team else None
        return cache[name]

    return resolve


# How far back to pull games for Elo warm-up. Two prior seasons gives enough
# updates that ratings have separated from the 1500 seed by opening day.
_ELO_HISTORY_START = date(2024, 5, 1)


def fetch_and_store_bpi_ratings(session) -> dict[str, float]:
    logger.info("Fetching BPI ratings from ESPN...")
    ratings = fetch_bpi_ratings()
    team_details = fetch_team_details()
    if not ratings:
        logger.error("Failed to fetch BPI ratings")
        return {}
    for name, bpi in ratings.items():
        details = team_details.get(name, {})
        upsert_team(
            session,
            name,
            bpi,
            abbreviation=details.get("abbreviation", ""),
            logo_url=details.get("logo_url", ""),
        )

    # Expansion teams won't appear in historical BPI — seed them at 0.0 so
    # their games still get stored and ranked by quality. Shifted harmonic mean
    # treats 0 as league-average (BPI is zero-centered), so this is a reasonable default.
    for name in set(team_details) - set(ratings):
        details = team_details[name]
        upsert_team(
            session,
            name,
            0.0,
            abbreviation=details.get("abbreviation", ""),
            logo_url=details.get("logo_url", ""),
        )
        logger.info(f"Seeded expansion team with BPI=0: {name}")

    from src.scoring.quality import _BPI_MIN, _BPI_MAX

    bpi_vals = list(ratings.values())
    observed_min, observed_max = min(bpi_vals), max(bpi_vals)
    if observed_min < _BPI_MIN or observed_max > _BPI_MAX:
        logger.warning(
            f"BPI out of normalization range: observed [{observed_min:.2f}, {observed_max:.2f}] "
            f"vs scale [{_BPI_MIN}, {_BPI_MAX}] — quality scores will be clamped. "
            f"Consider updating _BPI_MIN/_BPI_MAX in quality.py for next season."
        )

    logger.info(f"Stored {len(team_details)} teams")
    return ratings


def fetch_and_store_games(session) -> list[dict]:
    logger.info("Fetching schedule and results from ESPN...")
    games = fetch_schedule_and_results()
    if not games:
        logger.warning("No games fetched from ESPN")
        return []

    logger.info("Fetching broadcaster info from WNBA.com...")
    today = datetime.now().strftime("%Y-%m-%d")
    broadcasters = fetch_wnba_schedule_broadcasters(today)
    games = enhance_games_with_broadcasters(games, broadcasters)

    get_cached_team_id = _make_team_id_resolver(session)
    stored = 0
    for game in games:
        team_a, team_b = game.get("team_a", ""), game.get("team_b", "")
        if not team_a or not team_b:
            continue
        team_a_id = get_cached_team_id(team_a)
        team_b_id = get_cached_team_id(team_b)
        if not team_a_id or not team_b_id:
            logger.warning(f"Unknown team(s): {team_a!r}, {team_b!r} — skipping")
            continue
        winner_team = game.get("winner_team")
        upsert_game(
            session,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            date=game.get("date", ""),
            time=game.get("time", ""),
            broadcaster=game.get("broadcaster", ""),
            winner_id=get_cached_team_id(winner_team) if winner_team else None,
            final_score_a=game.get("final_score_a"),
            final_score_b=game.get("final_score_b"),
            espn_id=game.get("event_id"),
        )
        stored += 1

    logger.info(f"Upserted {stored} games")
    return games


def compute_elo_ratings() -> dict[str, float]:
    """Replay all historical games through the Elo engine to produce current ratings.

    Re-fetches history fresh each run — Elo state isn't persisted, so there's
    nothing that can drift out of sync with the rest of the pipeline. Teams
    without any prior games (expansion teams, or any team pre-opening-day)
    will appear at INITIAL_RATING when looked up later.
    """
    yesterday = date.today() - date.resolution
    logger.info(f"Fetching Elo history: {_ELO_HISTORY_START} through {yesterday}...")
    all_games = fetch_games_for_range(_ELO_HISTORY_START, yesterday)
    completed = [
        g for g in all_games if g.get("winner_team") and g.get("season_type", 2) != 1
    ]
    logger.info(f"Replaying {len(completed)} completed games through Elo")
    replay = replay_games(completed)
    return replay.final_ratings


def compute_standings(session, elo_ratings: dict[str, float]) -> dict[str, dict]:
    all_teams = get_all_teams(session)
    standings = {
        t.name: {
            "wins": 0,
            "losses": 0,
            "bpi": t.bpi_rating,
            "elo": elo_ratings.get(t.name, INITIAL_RATING),
            "h2h": {},
        }
        for t in all_teams
    }
    completed = get_completed_games(session, season_year=2026)
    for game in completed:
        team_a = get_team_by_id(session, game.team_a_id)
        team_b = get_team_by_id(session, game.team_b_id)
        if not team_a or not team_b:
            continue
        a_won = game.winner_id == team_a.id
        if a_won:
            standings[team_a.name]["wins"] += 1
            standings[team_b.name]["losses"] += 1
        else:
            standings[team_b.name]["wins"] += 1
            standings[team_a.name]["losses"] += 1
        increment_h2h(standings[team_a.name]["h2h"], team_b.name, won=a_won)
        increment_h2h(standings[team_b.name]["h2h"], team_a.name, won=not a_won)
    logger.info(f"Computed standings for {len(standings)} teams")
    return standings


def _calibrate_season_max_swing(standings: dict, all_games: list[dict]) -> float:
    """Return the expected peak importance swing for this season.

    Runs a single 10k Monte Carlo from equal (0-0) standings over all
    non-preseason games, then returns the max swing across every game.
    Called once per season on the first daily-update run and cached in DB.
    """
    zero_standings = {
        team: {**info, "wins": 0, "losses": 0, "h2h": {}}
        for team, info in standings.items()
    }
    remaining = [
        (g["team_a"], g["team_b"]) for g in all_games if g.get("season_type", 2) != 1
    ]
    _, outcome_matrix, playoff_sets = run_monte_carlo_simulation(
        zero_standings, remaining, num_simulations=10000, return_matrix=True
    )
    team_names = list(zero_standings.keys())
    swings = compute_importance_from_matrix(
        outcome_matrix, playoff_sets, remaining, team_names
    )
    return max(swings) if swings else 0.75


def compute_daily_scores(
    session, games: list[dict], standings: dict
) -> tuple[list[dict], dict[str, float]]:
    """Score upcoming games and return (scored_games, playoff_probs).

    Uses a single 10k-sim Monte Carlo run. Game importance is derived by
    splitting that run's outcome matrix — no additional simulations needed.
    Playoff probabilities (one per team) are the aggregate of the same run.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    upcoming_games = [
        g
        for g in games
        if g.get("date", "") >= today and g.get("status") != GameStatus.FINAL
    ]
    if not upcoming_games:
        logger.info("No upcoming games to score")
        return [], {}

    # Seed with date of last completed game so scores are stable until new results arrive.
    last_completed_date = max(
        (g["date"] for g in games if g.get("status") == GameStatus.FINAL),
        default=today,
    )
    random.seed(int(last_completed_date.replace("-", "")))
    logger.info(f"Monte Carlo seed: last completed game on {last_completed_date}")

    # All non-final, non-preseason games form the simulation universe.
    remaining_games = []
    remaining_event_index: dict[str, int] = {}
    for g in games:
        if g.get("status") != GameStatus.FINAL and g.get("season_type", 2) != 1:
            eid = g.get("event_id", "")
            if eid:
                remaining_event_index[eid] = len(remaining_games)
            remaining_games.append((g["team_a"], g["team_b"]))

    logger.info(
        f"Running 10k Monte Carlo over {len(remaining_games)} remaining games..."
    )
    playoff_probs, outcome_matrix, playoff_sets = run_monte_carlo_simulation(
        standings,
        remaining_games,
        num_simulations=10000,
        return_matrix=True,
    )

    team_names = list(standings.keys())
    raw_swings = compute_importance_from_matrix(
        outcome_matrix, playoff_sets, remaining_games, team_names
    )
    logger.info(f"Computed importance swings for {len(raw_swings)} remaining games")

    # Quality: normalize by current season's live BPI spread (slow-moving, reflects
    # real team-strength changes).
    bpi_values = [s["bpi"] for s in standings.values()]
    bpi_min, bpi_max = min(bpi_values), max(bpi_values)

    # Importance: normalize by the season-start ceiling (computed once from equal
    # standings over the full schedule, then cached in DB). This keeps scores
    # comparable all season — later games naturally score higher as swings grow.
    season_year = int(today[:4])
    importance_ceiling = get_importance_max_swing(session, season_year)
    if importance_ceiling is None:
        logger.info(
            "First run of season — calibrating importance ceiling from equal standings..."
        )
        importance_ceiling = _calibrate_season_max_swing(standings, games)
        save_importance_max_swing(session, season_year, importance_ceiling)
        logger.info(f"Season importance ceiling: {importance_ceiling:.3f}")

    logger.info(
        f"Normalization: BPI=[{bpi_min:.2f}, {bpi_max:.2f}], "
        f"importance_ceiling={importance_ceiling:.3f}"
    )

    scored = []
    for game in upcoming_games:
        team_a, team_b = game["team_a"], game["team_b"]
        bpi_a = standings.get(team_a, {}).get("bpi", 0.0)
        bpi_b = standings.get(team_b, {}).get("bpi", 0.0)
        quality = compute_quality_score(bpi_a, bpi_b, bpi_min=bpi_min, bpi_max=bpi_max)
        elo_a = standings.get(team_a, {}).get("elo", INITIAL_RATING)
        elo_b = standings.get(team_b, {}).get("elo", INITIAL_RATING)
        win_prob_a = expected_win_prob(elo_a, elo_b, DEFAULT_HOME_ADVANTAGE)

        game_date = game.get("date", today)
        importance: float | None
        if game.get("season_type", 2) == 1:
            importance = 0.0
        else:
            game_index = remaining_event_index.get(game.get("event_id", ""))
            if game_index is not None:
                importance = normalize_importance_score(
                    raw_swings[game_index], max_swing=importance_ceiling
                )
            else:
                importance = None

        importance_for_overall = importance if importance is not None else 0.0
        overall = quality * 0.6 + importance_for_overall * 0.4
        imp_log = f"{importance:.1f}" if importance is not None else "—"
        logger.info(
            f"{game_date} {team_a} vs {team_b}: "
            f"quality={quality:.1f} importance={imp_log} overall={overall:.1f}"
        )
        scored.append(
            {
                "team_a": team_a,
                "team_b": team_b,
                "date": game_date,
                "time": game.get("time", ""),
                "quality": quality,
                "importance": importance,
                "overall": overall,
                "broadcaster": game.get("broadcaster", ""),
                "win_prob_a": win_prob_a,
            }
        )

    return scored, playoff_probs


def store_daily_rankings(session, scored_games: list[dict]) -> None:
    get_cached_team_id = _make_team_id_resolver(session)
    stored = 0
    for game in scored_games:
        team_a_id = get_cached_team_id(game["team_a"])
        team_b_id = get_cached_team_id(game["team_b"])
        if not team_a_id or not team_b_id:
            logger.warning(
                f"Skipping ranking for {game['team_a']} vs {game['team_b']}: team not found"
            )
            continue
        upsert_daily_ranking(
            session,
            date=game["date"],
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            quality_score=game["quality"],
            importance_score=game["importance"],
            overall_score=game["overall"],
            broadcaster=game.get("broadcaster", ""),
            win_prob_a=game.get("win_prob_a"),
        )
        stored += 1

    logger.info(f"Stored {stored} daily rankings")


def store_playoff_probabilities(
    session, playoff_probs: dict[str, float], snapshot_date: str
) -> None:
    """Persist per-team playoff probabilities for a given date."""
    get_cached_team_id = _make_team_id_resolver(session)
    stored = 0
    for team_name, prob in playoff_probs.items():
        team_id = get_cached_team_id(team_name)
        if not team_id:
            logger.warning(f"Skipping playoff prob for unknown team: {team_name}")
            continue
        upsert_playoff_probability(
            session, date=snapshot_date, team_id=team_id, probability=prob
        )
        stored += 1
    logger.info(f"Stored {stored} playoff probabilities for {snapshot_date}")


def main() -> int:
    logger.info("=== Starting daily update job ===")
    try:
        init_db()
        session = get_session()
        try:
            fetch_and_store_bpi_ratings(session)
            games = fetch_and_store_games(session)
            elo_ratings = compute_elo_ratings()
            standings = compute_standings(session, elo_ratings)
            scored, playoff_probs = compute_daily_scores(session, games, standings)
            store_daily_rankings(session, scored)
            today = datetime.now().strftime("%Y-%m-%d")
            store_playoff_probabilities(session, playoff_probs, today)
            logger.info("=== Daily update job completed successfully ===")
            return 0
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Daily update job failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
