#!/usr/bin/env python3
"""Daily update job for WNBA Games to Watch.

Fetches data, computes scores, and stores results in the database.
"""

import logging
import sys
from datetime import datetime

from src.db.schema import init_db, get_session
from src.db.queries import (
    upsert_team,
    get_all_teams,
    insert_game,
    insert_daily_ranking,
    get_completed_games,
)
from src.data.espn_api import fetch_bpi_ratings, fetch_schedule_and_results
from src.data.wnba_schedule import enhance_games_with_broadcasters, fetch_wnba_schedule_broadcasters
from src.scoring.quality import compute_quality_score
from src.scoring.monte_carlo import run_monte_carlo_simulation
from src.scoring.importance import compute_importance_score

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/daily_update.log"),
    ],
)
logger = logging.getLogger(__name__)


def fetch_and_store_bpi_ratings(session):
    """Fetch BPI ratings from ESPN and store in database."""
    logger.info("Fetching BPI ratings from ESPN...")
    bpi_ratings = fetch_bpi_ratings()

    if not bpi_ratings:
        logger.error("Failed to fetch BPI ratings")
        return {}

    logger.info(f"Storing {len(bpi_ratings)} team ratings")
    for team_name, bpi in bpi_ratings.items():
        upsert_team(session, team_name, bpi)

    return bpi_ratings


def fetch_and_store_games(session):
    """Fetch schedule and game results from ESPN."""
    logger.info("Fetching schedule and results from ESPN...")
    games = fetch_schedule_and_results(season=2025)

    if not games:
        logger.warning("No games fetched from ESPN")
        return []

    logger.info(f"Fetched {len(games)} games from ESPN")

    # Enhance with broadcaster info from WNBA.com
    logger.info("Fetching broadcaster info from WNBA.com...")
    today = datetime.now().strftime("%Y-%m-%d")
    broadcasters = fetch_wnba_schedule_broadcasters(today)
    games = enhance_games_with_broadcasters(games, broadcasters)

    # Store games in database
    logger.info(f"Storing {len(games)} games in database")
    for game in games:
        team_a = game.get("team_a", "")
        team_b = game.get("team_b", "")
        date = game.get("date", "")
        time = game.get("time", "")
        broadcaster = game.get("broadcaster", "")
        winner_team = game.get("winner_team")
        final_score_a = game.get("final_score_a")
        final_score_b = game.get("final_score_b")

        if not team_a or not team_b:
            logger.warning(f"Skipping game with missing teams: {game}")
            continue

        insert_game(
            session,
            team_a_id=get_team_id(session, team_a),
            team_b_id=get_team_id(session, team_b),
            date=date,
            time=time,
            broadcaster=broadcaster,
            winner_id=get_team_id(session, winner_team) if winner_team else None,
            final_score_a=final_score_a,
            final_score_b=final_score_b,
        )

    return games


def get_team_id(session, team_name: str) -> int:
    """Get team ID by name."""
    from src.db.queries import get_team_by_name

    team = get_team_by_name(session, team_name)
    if team:
        return team.id
    return None


def compute_standings(session) -> dict:
    """Compute current season standings from completed games."""
    logger.info("Computing standings from completed games...")
    all_teams = get_all_teams(session)
    standings = {team.name: {"wins": 0, "losses": 0, "bpi": team.bpi_rating} for team in all_teams}

    completed_games = get_completed_games(session, season_year=2025)
    for game in completed_games:
        from src.db.queries import get_team_by_name

        team_a = get_team_by_name(session, game.team_a_id)
        team_b = get_team_by_name(session, game.team_b_id)

        # This is a simplification - we should look up team names by ID
        # For now, skip if we can't resolve
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


def compute_daily_scores(session, games, standings):
    """Compute quality and importance scores for all games."""
    logger.info(f"Computing scores for {len(games)} games...")

    # Filter to today's games
    today = datetime.now().strftime("%Y-%m-%d")
    todays_games = [g for g in games if g.get("date") == today and g.get("status") != "final"]

    if not todays_games:
        logger.info("No upcoming games today")
        return []

    # Get remaining schedule for importance calculation
    remaining_games = [
        (g.get("team_a"), g.get("team_b")) for g in games if g.get("status") != "final"
    ]

    # Compute current playoff probabilities
    logger.info("Running Monte Carlo simulation...")
    playoff_probs = run_monte_carlo_simulation(standings, remaining_games, num_simulations=10000)

    # Score each game
    scored_games = []
    for i, game in enumerate(todays_games):
        team_a = game.get("team_a", "")
        team_b = game.get("team_b", "")
        broadcaster = game.get("broadcaster", "")

        bpi_a = standings.get(team_a, {}).get("bpi", 0.0)
        bpi_b = standings.get(team_b, {}).get("bpi", 0.0)

        # Quality score
        quality = compute_quality_score(bpi_a, bpi_b)

        # Importance score
        game_index = remaining_games.index((team_a, team_b)) if (team_a, team_b) in remaining_games else -1
        if game_index >= 0:
            importance = compute_importance_score(standings, remaining_games, game_index, playoff_probs)
        else:
            importance = 0.0

        # Combined score (60% quality, 40% importance)
        overall = (quality * 0.6) + (importance * 0.4)

        scored_games.append(
            {
                "team_a": team_a,
                "team_b": team_b,
                "date": today,
                "quality": quality,
                "importance": importance,
                "overall": overall,
                "broadcaster": broadcaster,
            }
        )

        logger.info(
            f"{team_a} vs {team_b}: quality={quality:.1f}, importance={importance:.1f}, overall={overall:.1f}"
        )

    return scored_games


def store_daily_rankings(session, scored_games):
    """Store daily rankings in the database."""
    logger.info(f"Storing {len(scored_games)} game rankings...")

    for game in scored_games:
        team_a_id = get_team_id(session, game["team_a"])
        team_b_id = get_team_id(session, game["team_b"])

        if not team_a_id or not team_b_id:
            logger.warning(f"Skipping ranking for {game['team_a']} vs {game['team_b']}: team not found")
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

    logger.info("Daily rankings stored successfully")


def main():
    """Main entry point for the daily update job."""
    try:
        logger.info("=== Starting daily update job ===")

        # Initialize database
        init_db()
        session = get_session()

        try:
            # Fetch and store data
            bpi_ratings = fetch_and_store_bpi_ratings(session)
            games = fetch_and_store_games(session)

            # Compute standings
            standings = compute_standings(session)

            # Compute scores
            scored_games = compute_daily_scores(session, games, standings)

            # Store rankings
            store_daily_rankings(session, scored_games)

            logger.info("=== Daily update job completed successfully ===")
            return 0

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Daily update job failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
