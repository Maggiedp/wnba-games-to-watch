#!/usr/bin/env python3
"""Daily update job for WNBA Games to Watch."""

import logging
import random
import sys
from datetime import date, datetime, timedelta

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
    get_team_by_id,
    get_team_by_name,
    upsert_daily_ranking,
    upsert_game,
    upsert_team,
)
from src.db.schema import get_session, init_db
from src.scoring.elo import INITIAL_RATING, replay_games
from src.scoring.importance import compute_importance_score
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

# Only compute playoff importance for games within this window.
# Beyond it the standings will shift enough that the score is noise.
_IMPORTANCE_WINDOW_DAYS = 30

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


def compute_elo_ratings() -> dict[str, float]:
    """Replay all historical games through the Elo engine to produce current ratings.

    Re-fetches history fresh each run — Elo state isn't persisted, so there's
    nothing that can drift out of sync with the rest of the pipeline. Teams
    without any prior games (expansion teams, or any team pre-opening-day)
    will appear at INITIAL_RATING when looked up later.
    """
    yesterday = date.today() - timedelta(days=1)
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
        a_h2h = standings[team_a.name]["h2h"].setdefault(team_b.name, [0, 0])
        b_h2h = standings[team_b.name]["h2h"].setdefault(team_a.name, [0, 0])
        if game.winner_id == team_a.id:
            standings[team_a.name]["wins"] += 1
            standings[team_b.name]["losses"] += 1
            a_h2h[0] += 1
            b_h2h[1] += 1
        else:
            standings[team_b.name]["wins"] += 1
            standings[team_a.name]["losses"] += 1
            b_h2h[0] += 1
            a_h2h[1] += 1
    logger.info(f"Computed standings for {len(standings)} teams")
    return standings


def compute_daily_scores(session, games: list[dict], standings: dict) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    importance_cutoff = (
        datetime.now() + timedelta(days=_IMPORTANCE_WINDOW_DAYS)
    ).strftime("%Y-%m-%d")

    upcoming_games = [
        g
        for g in games
        if g.get("date", "") >= today and g.get("status") != GameStatus.FINAL
    ]
    if not upcoming_games:
        logger.info("No upcoming games to score")
        return []

    # Seed with date of last completed game so scores are stable until new results arrive.
    last_completed_date = max(
        (g["date"] for g in games if g.get("status") == GameStatus.FINAL),
        default=today,
    )
    random.seed(int(last_completed_date.replace("-", "")))
    logger.info(f"Monte Carlo seed: last completed game on {last_completed_date}")

    # Exclude preseason games from standings simulation — they don't affect playoff seeding.
    # Build parallel event_id index so duplicate matchups map to the right slot.
    remaining_games = []
    remaining_event_index: dict[str, int] = {}
    for g in games:
        if g.get("status") != GameStatus.FINAL and g.get("season_type", 2) != 1:
            eid = g.get("event_id", "")
            if eid:
                remaining_event_index[eid] = len(remaining_games)
            remaining_games.append((g["team_a"], g["team_b"]))

    logger.info(
        f"Scoring {len(remaining_games)} remaining games "
        f"(importance only for games through {importance_cutoff})..."
    )

    scored = []
    for game in upcoming_games:
        team_a, team_b = game["team_a"], game["team_b"]
        bpi_a = standings.get(team_a, {}).get("bpi", 0.0)
        bpi_b = standings.get(team_b, {}).get("bpi", 0.0)
        quality = compute_quality_score(bpi_a, bpi_b)

        game_date = game.get("date", today)
        importance: float | None
        if game.get("season_type", 2) == 1:
            # Preseason games don't count for playoff seeding.
            importance = 0.0
        elif game_date <= importance_cutoff:
            game_index = remaining_event_index.get(game.get("event_id", ""))
            if game_index is not None:
                importance = compute_importance_score(
                    standings, remaining_games, game_index
                )
            else:
                importance = None
        else:
            # Beyond the window we don't simulate — leave importance unknown
            # rather than zero, so the UI can show "—" instead of implying
            # the game doesn't matter.
            importance = None

        # When importance is unknown, treat its contribution as zero for the
        # overall score so we still have *a* number to rank on. The UI hides
        # the missing importance value itself.
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
                "importance": importance,  # may be None if not simulated
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
        upsert_daily_ranking(
            session,
            date=game["date"],
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            quality_score=game["quality"],
            importance_score=game["importance"],
            overall_score=game["overall"],
            broadcaster=game.get("broadcaster", ""),
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
            elo_ratings = compute_elo_ratings()
            standings = compute_standings(session, elo_ratings)
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
