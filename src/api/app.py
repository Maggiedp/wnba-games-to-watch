"""FastAPI app for WNBA Games to Watch."""

import asyncio
import logging
import os
import threading
import time
from collections import OrderedDict

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
    yesterday_et,
)
from src.db.queries import (
    get_all_known_espn_ids,
    get_calibration_pairs,
    get_completed_rankings,
    get_daily_rankings,
    get_elo_history,
    get_playoff_probabilities,
    get_rankings_by_broadcaster,
    get_teams_by_ids,
    get_upcoming_rankings,
)
from src.db.schema import get_session, init_db
from src.scoring.calibration import compute_calibration

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


@app.get("/transparency", response_class=HTMLResponse)
async def transparency_page():
    from src.api.routes import render_transparency

    return HTMLResponse(render_transparency())


@app.get("/game/{espn_id}", response_class=HTMLResponse)
def game_detail(espn_id: str):
    from src.api.routes import render_game_detail

    session = get_session()
    try:
        html = render_game_detail(session, espn_id)
    finally:
        session.close()
    if html is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return html


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
    # Widen by one ET day so late-ET games crossing the UTC midnight
    # boundary stay visible to non-ET viewers (still in their local
    # today). The frontend's localDateISO filter narrows back per-viewer.
    session = get_session()
    try:
        rankings = get_upcoming_rankings(session, yesterday_et())
        return format_games_response(rankings, session)
    finally:
        session.close()


@app.get("/api/games/live-status")
def get_live_game_statuses():
    """Return {espn_id: status} for today-ET and yesterday-ET games.

    Split off from /api/games/upcoming so a slow ESPN scoreboard call can't
    block the homepage's primary DB-backed response. The frontend fetches
    this in parallel and uses it to drive live-WP hydration. Sync `def` so
    FastAPI runs the blocking ESPN call in a threadpool.

    Widened by one ET day to match /api/games/upcoming. A late-ET game
    keyed to yesterday-ET that's still in progress must be polled for
    live WP from viewers west of Eastern; otherwise the frontend's
    isLiveStatus(g.game_status) gate sees no status and the row stays
    on stale pregame odds.

    Today is the primary call (raises 502 on failure for the
    frontend's backoff). Yesterday is best-effort: a transient ESPN
    burp on the secondary call shouldn't strand today's live games.

    Returns 502 on today-side ESPN failure so the frontend can distinguish
    "ESPN is down" from "no games today" (both would otherwise produce {}).
    The frontend backs off and retries on 5xx.
    """
    try:
        statuses = fetch_today_game_statuses(today_et())
    except ESPNAPIError as e:
        logger.warning("Failed to fetch today's game statuses from ESPN: %s", e)
        raise HTTPException(status_code=502, detail="ESPN scoreboard unreachable")
    try:
        yesterday_statuses = fetch_today_game_statuses(yesterday_et())
    except ESPNAPIError as e:
        logger.warning("Failed to fetch yesterday's game statuses (non-fatal): %s", e)
        yesterday_statuses = {}
    return {**yesterday_statuses, **statuses}


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


# Short-lived cache for /api/live-wp. Without this, every browser polling
# /api/live-wp every 30s during live games would hit ESPN directly — N viewers
# × M live games × every 30s. The cache collapses concurrent viewers into one
# ESPN call per game per TTL window, and the per-key fetch lock prevents a
# cold-start stampede when multiple requests arrive simultaneously.
#
# Failures are negative-cached so an ESPN outage doesn't convoy N waiters
# through N separate 10s timeouts; instead concurrent failing requests fail
# fast from the cached error until the negative TTL expires.
#
# Cache is a bounded LRU so unique-id traffic (legitimate or hostile) can't
# grow the per-id lock map indefinitely.
_LIVE_WP_CACHE_TTL_S = 15
_LIVE_WP_NEG_CACHE_TTL_S = 5  # transient ESPN errors retry quickly
_LIVE_WP_NOT_FOUND_TTL_S = 60  # 404 is closer to permanent
_LIVE_WP_CACHE_MAX_ENTRIES = 64  # ~5× a typical WNBA night's slate
# Each entry is (expires_at, kind, value) where kind ∈ {'ok', 'err'}.
# For 'ok', value is the payload dict; for 'err', value is the raised exception.
_live_wp_cache: "OrderedDict[str, tuple[float, str, object]]" = OrderedDict()
_live_wp_cache_lock = threading.Lock()
_live_wp_fetch_locks: dict[str, threading.Lock] = {}


def _read_live_wp_cache(espn_id: str):
    """Return the cached entry if still fresh, else None. Raises cached errors."""
    cached = _live_wp_cache.get(espn_id)
    if not cached or cached[0] <= time.monotonic():
        return None
    _live_wp_cache.move_to_end(espn_id)
    _, kind, value = cached
    if kind == "err":
        raise value  # type: ignore[misc]
    return value


def _store_live_wp_cache(espn_id: str, expires_at: float, kind: str, value: object):
    """Insert/refresh an entry and enforce the size cap, dropping its fetch lock too.

    Known limitation: if MAX_ENTRIES unique ids cycle through during a single
    in-flight fetch, the in-flight id's lock can be evicted while a thread
    still holds it. A subsequent request for that id would create a new lock
    and allow a second concurrent ESPN fetch. Reaching this requires hostile
    high-cardinality traffic; on this app (`--max-instances=1`, ~12 games per
    night) it's not reachable in practice. A true single-flight design
    (cache entry owns its lock, evict only when unlocked) is the correct fix
    if production traffic ever justifies it.
    """
    _live_wp_cache[espn_id] = (expires_at, kind, value)
    _live_wp_cache.move_to_end(espn_id)
    while len(_live_wp_cache) > _LIVE_WP_CACHE_MAX_ENTRIES:
        evicted_id, _ = _live_wp_cache.popitem(last=False)
        _live_wp_fetch_locks.pop(evicted_id, None)


def _fetch_live_wp_cached(espn_id: str) -> dict:
    with _live_wp_cache_lock:
        hit = _read_live_wp_cache(espn_id)
        if hit is not None:
            return hit
        fetch_lock = _live_wp_fetch_locks.setdefault(espn_id, threading.Lock())

    with fetch_lock:
        # Re-check after acquiring per-key lock — another thread may have
        # populated the cache (success or error) while we were waiting.
        with _live_wp_cache_lock:
            hit = _read_live_wp_cache(espn_id)
            if hit is not None:
                return hit
        try:
            payload = fetch_live_win_probability(espn_id)
        except ESPNNotFoundError as e:
            with _live_wp_cache_lock:
                _store_live_wp_cache(
                    espn_id, time.monotonic() + _LIVE_WP_NOT_FOUND_TTL_S, "err", e
                )
            raise
        except ESPNAPIError as e:
            with _live_wp_cache_lock:
                _store_live_wp_cache(
                    espn_id, time.monotonic() + _LIVE_WP_NEG_CACHE_TTL_S, "err", e
                )
            raise
        with _live_wp_cache_lock:
            _store_live_wp_cache(
                espn_id, time.monotonic() + _LIVE_WP_CACHE_TTL_S, "ok", payload
            )
        return payload


# ESPN event IDs are 8–10 digit integers. Pattern-validate to reject garbage
# input cheaply.
_ESPN_ID_PATTERN = r"^\d{1,12}$"

# Allowlist of espn_ids known to the DB, refreshed periodically. The cache
# stops an attacker from forcing one outbound 10s ESPN fetch per arbitrary
# numeric id; only ids we already track ever reach ESPN. ~500 entries max
# per season, so the set is tiny and the refresh DB query is cheap.
_KNOWN_IDS_TTL_S = 60
_known_espn_ids_cache: tuple[float, frozenset[str]] | None = None
_known_espn_ids_lock = threading.Lock()


def _get_known_espn_ids() -> frozenset[str]:
    global _known_espn_ids_cache
    with _known_espn_ids_lock:
        cached = _known_espn_ids_cache
        if cached and cached[0] > time.monotonic():
            return cached[1]
    # Refresh outside the lock so a slow query doesn't serialize readers.
    session = get_session()
    try:
        ids = frozenset(get_all_known_espn_ids(session))
    finally:
        session.close()
    with _known_espn_ids_lock:
        _known_espn_ids_cache = (time.monotonic() + _KNOWN_IDS_TTL_S, ids)
    return ids


@app.get("/api/live-wp")
def get_live_win_probability(espn_id: str = Query(..., pattern=_ESPN_ID_PATTERN)):
    if espn_id not in _get_known_espn_ids():
        raise HTTPException(status_code=404, detail="Unknown game id")
    try:
        return _fetch_live_wp_cached(espn_id)
    except ESPNNotFoundError:
        raise HTTPException(status_code=404, detail="Game not found on ESPN")
    except ESPNAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/playoff-odds", response_model=list[PlayoffOddsResponse])
async def get_playoff_odds(date: str = Query(default=None)):
    """Return per-team round-by-round playoff probabilities.

    Sorted by win_championship_prob desc, with team name as tiebreaker.
    """
    if date is None:
        date = today_et()
    session = get_session()
    try:
        recs = get_playoff_probabilities(session, date)
        if not recs:
            return []
        # Skip legacy rows from before the round-prob migration: if any new
        # column is NULL, we don't have a meaningful champ/finals/semis value.
        # Showing those as 0% would mislead — wait for the next daily-update
        # to populate them.
        recs = {
            tid: r for tid, r in recs.items() if r.win_championship_prob is not None
        }
        if not recs:
            return []
        teams = get_teams_by_ids(session, set(recs.keys()))
        rows = [
            PlayoffOddsResponse(
                team=teams[tid].name,
                abbreviation=teams[tid].abbreviation or "",
                logo_url=teams[tid].logo_url or "",
                make_playoffs_prob=recs[tid].make_playoffs_prob,
                reach_semis_prob=recs[tid].reach_semis_prob or 0.0,
                reach_finals_prob=recs[tid].reach_finals_prob or 0.0,
                win_championship_prob=recs[tid].win_championship_prob or 0.0,
            )
            for tid in recs
            if tid in teams
        ]
        return sorted(rows, key=lambda x: (-x.win_championship_prob, x.team))
    finally:
        session.close()


@app.get("/api/elo-history")
async def get_elo_history_endpoint(season: str = Query(default=None)):
    """Per-team Elo trajectory for a season (DB-only; never calls ESPN)."""
    if season is None:
        season = today_et()[:4]
    session = get_session()
    try:
        rows = get_elo_history(session, season)
        if not rows:
            return {"season": season, "teams": {}}
        teams = get_teams_by_ids(session, {r.team_id for r in rows})
        out: dict[str, list[dict]] = {}
        for r in rows:
            t = teams.get(r.team_id)
            if t is None:
                continue
            out.setdefault(t.name, []).append(
                {"date": r.date, "rating": round(r.rating, 1)}
            )
        return {"season": season, "teams": out}
    finally:
        session.close()


@app.get("/api/calibration")
async def get_calibration_endpoint(season: int = Query(default=None)):
    """Win-probability reliability for completed games (DB-only)."""
    if season is None:
        season = int(today_et()[:4])
    session = get_session()
    try:
        pairs = get_calibration_pairs(session, season)
        result = compute_calibration(pairs)
        return {
            "season": season,
            "n": result.n,
            "brier": round(result.brier, 4),
            "buckets": [
                {
                    "lo": b.lo,
                    "hi": b.hi,
                    "predicted_mean": round(b.predicted_mean, 4),
                    "actual_rate": round(b.actual_rate, 4),
                    "count": b.count,
                }
                for b in result.buckets
            ],
        }
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
