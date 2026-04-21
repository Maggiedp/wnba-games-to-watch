"""FastAPI app for WNBA Games to Watch."""

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
import logging
from datetime import datetime
from src.db.schema import init_db, get_session
from src.db.queries import get_upcoming_games, get_daily_rankings, get_rankings_by_broadcaster
from src.api.routes import GameResponse, format_games_response

logger = logging.getLogger(__name__)

# Initialize database on startup
init_db()

app = FastAPI(title="WNBA Games to Watch", description="Find the best WNBA games to watch")


@app.get("/", response_class=HTMLResponse)
async def homepage():
    """Serve the homepage with games and filter UI."""
    from src.api.routes import render_homepage

    return render_homepage()


@app.get("/api/games/today", response_model=list[GameResponse])
async def get_today_games():
    """Get today's ranked games."""
    today = datetime.now().strftime("%Y-%m-%d")
    session = get_session()

    try:
        rankings = get_daily_rankings(session, today)
        return format_games_response(rankings, session)
    finally:
        session.close()


@app.get("/api/games/upcoming", response_model=list[GameResponse])
async def get_upcoming_games_endpoint(days: int = Query(7, ge=1, le=30)):
    """Get upcoming games for the next N days."""
    start_date = datetime.now().strftime("%Y-%m-%d")
    session = get_session()

    try:
        games = get_upcoming_games(session, start_date)
        # For now, return empty list since we don't have rankings for future games yet
        # This will be populated by the daily job
        return []
    finally:
        session.close()


@app.get("/api/games/filter", response_model=list[GameResponse])
async def get_games_by_broadcaster(broadcaster: str):
    """Get games filtered by broadcaster."""
    today = datetime.now().strftime("%Y-%m-%d")
    session = get_session()

    try:
        rankings = get_rankings_by_broadcaster(session, today, broadcaster)
        return format_games_response(rankings, session)
    finally:
        session.close()


@app.get("/api/broadcasters")
async def get_broadcasters():
    """Get list of available broadcasters."""
    broadcasters = [
        "ESPN",
        "NBC",
        "Prime Video",
        "CBS",
        "Paramount+",
        "ION",
        "USA Network",
        "League Pass",
        "NBA TV",
    ]
    return {"broadcasters": broadcasters}


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    logger.info("Starting WNBA Games to Watch API")
    init_db()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down WNBA Games to Watch API")
