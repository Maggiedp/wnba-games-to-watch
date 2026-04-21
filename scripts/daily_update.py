#!/usr/bin/env python3
"""Daily update job for WNBA Games to Watch."""

import logging
import sys
from datetime import datetime

from src.constants import GameStatus
from src.data.espn_api import fetch_bpi_ratings, fetch_schedule_and_results
from src.data.wnba_schedule import (
    enhance_games_with_broadcasters,
    fetch_wnba_schedule_broadcasters,
)
from src.db.queries import (
    get_all_teams,
    get_completed_games,
    get_team_by_id,
    get_team_by_name,
    insert_daily_ranking,
    upsert_game,
    upsert_team,
)
from src.db.schema import get_session, init_db
from src.scoring.importance import compute_importance_score
from src.scoring.monte_carlo import run_monte_carlo_simulation
from src.scoring.quality import compute_quality_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/daily_update.log"),
    ],
)
logger = logging.getLogger(__name__)


def fetch_and_store_bpi_ratings(session) -> dict[str, float]:
    logger.info("Fetching BPI ratings from ESPN...")
    ratings = fetch_bpi_ratings()
    if not ratings:
        logger.error("Failed to fetch BPI ratings")
        return {}
    for name, bpi in ratings.items():
        upsert_team(session, name, bpi)
    logger.info(f"Stored {len(ratings)} team BPI ratings")
    return ratings


def fetch_and_store_games(session) -> list[dict]:
    logger.info("Fetching schedule and results from ESPN...")
    games = fetch_schedule_and_results(days_ahead=7)
    if not games:
        logger.warning("No games fetched from ESPN")
        return []

    logger.info("Fetching broadcaster info from WNBA.com...")
    today = datetime.now().strftime("%Y-%m-%d")
    broadcasters = fetch_wnba_schedule_broadcasters(today)
    games = enhance_games_with_broadcasters(games, broadcasters)

    # Cache team lookups to avoid N+1 queries during insertion
    team_cache: dict[str, int | None] = {}

    def get_cached_team_id(name: str) -> int | None:
        if name not in team_cache:
            team = get_team_by_name(session, name)
            team_cache[name] = team.id if team else None
        return team_cache[name]

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
        )
        stored += 1

    logger.info(f"Upserted {stored} games")
    return games


def compute_standings(session) -> dict[str, dict]:
    """Build standings dict from DB team list + completed game results."""
    all_teams = get_all_teams(session)
    standings = {
        t.name: {"wins": 0, "losses": 0, "bpi": t.bpi_rating} for t in all_teams
    }

    completed = get_completed_games(session, season_year=2026)
    for game in completed:
        team_a = get_team_by_id(session, game.team_a_id)
        team_b = get_team_by_id(session, game.team_b_id)
        if not team_a or not team_b:
            continue
        if game.winner_id == team_a.id:
            standings[team_a.name]["wins"] += 1
            standings[team_b.name]["losses"] += 1
        else:
            standings[team_b.name]["wins"] += 1
            standings[team_a.name]["losses"] += 1

    logger.info(f"Computed standings for {len(standings)} teams")
    return standings


def compute_daily_scores(session, games: list[dict], standings: dict) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    todays_games = [
        g
        for g in games
        if g.get("date") == today and g.get("status") != GameStatus.FINAL
    ]

    if not todays_games:
        logger.info("No upcoming games today")
        return []

    remaining_games = [
        (g["team_a"], g["team_b"]) for g in games if g.get("status") != GameStatus.FINAL
    ]

    logger.info(
        f"Running Monte Carlo simulation over {len(remaining_games)} remaining games..."
    )
    playoff_probs = run_monte_carlo_simulation(
        standings, remaining_games, num_simulations=10000
    )

    scored = []
    for game in todays_games:
        team_a, team_b = game["team_a"], game["team_b"]
        bpi_a = standings.get(team_a, {}).get("bpi", 0.0)
        bpi_b = standings.get(team_b, {}).get("bpi", 0.0)

        quality = compute_quality_score(bpi_a, bpi_b)

        try:
            game_index = remaining_games.index((team_a, team_b))
            importance = compute_importance_score(
                standings, remaining_games, game_index, playoff_probs
            )
        except ValueError:
            importance = 0.0

        overall = quality * 0.6 + importance * 0.4

        logger.info(
            f"{team_a} vs {team_b}: quality={quality:.1f} importance={importance:.1f} overall={overall:.1f}"
        )
        scored.append(
            {
                "team_a": team_a,
                "team_b": team_b,
                "date": today,
                "quality": quality,
                "importance": importance,
                "overall": overall,
                "broadcaster": game.get("broadcaster", ""),
            }
        )

    return scored


def store_daily_rankings(session, scored_games: list[dict]) -> None:
    team_cache: dict[str, int | None] = {}

    def get_cached_team_id(name: str) -> int | None:
        if name not in team_cache:
            team = get_team_by_name(session, name)
            team_cache[name] = team.id if team else None
        return team_cache[name]

    stored = 0
    for game in scored_games:
        team_a_id = get_cached_team_id(game["team_a"])
        team_b_id = get_cached_team_id(game["team_b"])
        if not team_a_id or not team_b_id:
            logger.warning(
                f"Skipping ranking for {game['team_a']} vs {game['team_b']}: team not found"
            )
            continue
        insert_daily_ranking(
            session,
            date=game["date"],
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            quality_score=game["quality"],
            importance_score=game["importance"],
            overall_score=game["overall"],
            broadcaster=game["broadcaster"],
        )
        stored += 1

    logger.info(f"Stored {stored} daily rankings")


def main() -> int:
    logger.info("=== Starting daily update job ===")
    try:
        init_db()
        session = get_session()
        try:
            fetch_and_store_bpi_ratings(session)
            games = fetch_and_store_games(session)
            standings = compute_standings(session)
            scored = compute_daily_scores(session, games, standings)
            store_daily_rankings(session, scored)
            logger.info("=== Daily update job completed successfully ===")
            return 0
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Daily update job failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
