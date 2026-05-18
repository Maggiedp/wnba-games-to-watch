"""FastAPI app for WNBA Games to Watch."""

import asyncio
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.api.routes import GameResponse, PlayoffOddsResponse, format_games_response
from src.constants import Broadcasters  # noqa: F401 — used in get_broadcasters endpoint
from src.data.espn_api import (
    ESPNAPIError,
    ESPNNotFoundError,
    fetch_live_win_probability,
    fetch_today_game_statuses,
    today_et,
)
from src.db.queries import (
    get_completed_rankings,
    get_daily_rankings,
    get_playoff_probabilities,
    get_rankings_by_broadcaster,
    get_teams_by_ids,
    get_upcoming_rankings,
)
from src.db.schema import get_session, init_db

logger = logging.getLogger(__name__)

init_db()

app = FastAPI(
    title="WNBA Games to Watch", description="Find the best WNBA games to watch"
)

_TRIGGER_SECRET = os.environ.get("TRIGGER_SECRET", "")


@app.get("/", response_class=HTMLResponse)
async def homepage():
    from src.api.routes import render_homepage

    return render_homepage()


@app.get("/api/games/today", response_model=list[GameResponse])
def get_today_games():
    today = today_et()
    session = get_session()
    try:
        rankings = get_daily_rankings(session, today)
        try:
            game_statuses = fetch_today_game_statuses(today)
        except Exception as e:
            logger.warning("Failed to fetch today's game statuses from ESPN: %s", e)
            game_statuses = {}
        return format_games_response(
            rankings, session, game_status_by_espn_id=game_statuses
        )
    finally:
        session.close()


@app.get("/api/games/upcoming", response_model=list[GameResponse])
async def get_upcoming_games_endpoint(days: int = Query(7, ge=1, le=30)):  # noqa: ARG001
    start_date = today_et()
    session = get_session()
    try:
        rankings = get_upcoming_rankings(session, start_date)
        return format_games_response(rankings, session)
    finally:
        session.close()


@app.get("/api/games/completed", response_model=list[GameResponse])
async def get_completed_games_endpoint():
    """Return all completed 2026 games sorted by excitement desc."""
    session = get_session()
    try:
        rankings = get_completed_rankings(session, season_year=2026)
        return format_games_response(rankings, session)
    finally:
        session.close()


@app.get("/api/games/filter", response_model=list[GameResponse])
async def get_games_by_broadcaster(broadcaster: str, mode: str = "upcoming"):
    start_date = today_et()
    session = get_session()
    try:
        rankings = get_rankings_by_broadcaster(
            session, start_date, broadcaster, mode=mode
        )
        return format_games_response(rankings, session)
    finally:
        session.close()


@app.get("/api/broadcasters")
async def get_broadcasters():
    return {"broadcasters": Broadcasters.ALL}


@app.get("/api/live-wp")
def get_live_win_probability(espn_id: str = Query(...)):
    try:
        return fetch_live_win_probability(espn_id)
    except ESPNNotFoundError:
        raise HTTPException(status_code=404, detail="Game not found on ESPN")
    except ESPNAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/playoff-odds", response_model=list[PlayoffOddsResponse])
async def get_playoff_odds(date: str = Query(default=None)):
    """Return per-team playoff probabilities, sorted highest-first."""
    if date is None:
        date = today_et()
    session = get_session()
    try:
        prob_by_id = get_playoff_probabilities(session, date)
        if not prob_by_id:
            return []
        teams = get_teams_by_ids(session, set(prob_by_id.keys()))
        return sorted(
            [
                PlayoffOddsResponse(
                    team=teams[tid].name,
                    abbreviation=teams[tid].abbreviation or "",
                    logo_url=teams[tid].logo_url or "",
                    probability=prob_by_id[tid],
                )
                for tid in prob_by_id
                if tid in teams
            ],
            key=lambda x: -x.probability,
        )
    finally:
        session.close()


@app.post("/internal/daily-update")
async def trigger_daily_update(x_trigger_secret: str = Header(default="")):
    """Run the daily update job synchronously. Called by Cloud Scheduler."""
    if _TRIGGER_SECRET and x_trigger_secret != _TRIGGER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    from scripts.daily_update import main

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, main)
    if result != 0:
        raise HTTPException(status_code=500, detail="Daily update job failed")
    return {"status": "ok"}


@app.on_event("startup")
async def startup_event():
    logger.info("Starting WNBA Games to Watch API")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down WNBA Games to Watch API")
