"""FastAPI app for WNBA Games to Watch."""

import asyncio
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from secrets import compare_digest
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from src.api.routes import (
    GameResponse,
    PlayoffOddsResponse,
    format_games_response,
    is_live_status,
)
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
    get_game_shapes,
    get_games_by_date,
    get_latest_playoff_probability_date,
    get_playoff_probabilities,
    get_rankings_by_broadcaster,
    get_shape_seasons,
    get_shot_making,
    get_shot_making_seasons,
    get_team_abbrev_map,
    get_team_records,
    get_team_style_season_counts,
    get_team_style_seasons,
    get_team_styles,
    get_teams_by_ids,
    get_upcoming_rankings,
    has_alerted,
    record_alert,
)
from src.notify.telegram import send_telegram, telegram_configured
from src.notify.thriller import (
    compose_alert,
    filter_recent_tipoffs,
    live_excitement_label,
)
from src.db.schema import get_session, init_db
from src.scoring.calibration import compute_calibration
from src.scoring.game_shape import compute_live_shape
from src.scoring.team_style import compute_style_view

logger = logging.getLogger(__name__)

init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting WNBA Games to Watch API")
    try:
        yield
    finally:
        logger.info("Shutting down WNBA Games to Watch API")


app = FastAPI(
    title="WNBA Games to Watch",
    description="Find the best WNBA games to watch",
    lifespan=lifespan,
)

_TRIGGER_SECRET = os.environ.get("TRIGGER_SECRET", "")


@app.middleware("http")
async def security_headers(request, call_next):
    """Set security response headers on every response. HSTS is app-set (not
    Cloudflare-fronted) because the apex is grey-cloud — Cloud Run terminates
    TLS directly. CSP/X-Frame-Options are intentionally omitted: read-only
    content with nothing to hijack."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    return response


@app.get("/", response_class=HTMLResponse)
async def homepage():
    from src.api.routes import render_homepage

    return render_homepage()


@app.get("/transparency", response_class=HTMLResponse)
async def transparency_page():
    from src.api.routes import render_transparency

    return HTMLResponse(render_transparency())


@app.get("/rankings", response_class=HTMLResponse)
async def rankings_page():
    from src.api.routes import render_rankings

    return HTMLResponse(render_rankings())


@app.get("/replay", response_class=HTMLResponse)
async def replay_page():
    from src.api.routes import render_replay

    return HTMLResponse(render_replay())


@app.get("/style", response_class=HTMLResponse)
async def style_page():
    from src.api.routes import render_style

    return HTMLResponse(render_style())


@app.get("/playoff-odds", response_class=HTMLResponse)
async def playoff_odds_page():
    from src.api.routes import render_playoff_odds

    return HTMLResponse(render_playoff_odds())


@app.get("/shot-making", response_class=HTMLResponse)
async def shot_making_page():
    from src.api.routes import render_shot_making

    return HTMLResponse(render_shot_making())


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


def _png_response(content: bytes, max_age: int) -> Response:
    """A PNG response with a public Cache-Control max-age (shared by og.png routes)."""
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": f"public, max-age={max_age}"},
    )


@app.api_route("/game/{espn_id}/og.png", methods=["GET", "HEAD"])
def game_og_image(espn_id: str):
    from src.api.og_image import render_game_card_png

    with _og_cache_lock:
        cached = _og_cache.get(espn_id)
        if cached and cached[0] > time.monotonic():
            _og_cache.move_to_end(espn_id)
            png = cached[1]
        else:
            png = None

    # Render outside the lock — don't hold it across the DB read + PIL draw.
    if png is None:
        session = get_session()
        try:
            png = render_game_card_png(session, espn_id)
        finally:
            session.close()
        if png is None:
            raise HTTPException(status_code=404, detail="Game not found")
        with _og_cache_lock:
            _og_cache[espn_id] = (time.monotonic() + _OG_CACHE_TTL_S, png)
            _og_cache.move_to_end(espn_id)
            while len(_og_cache) > _OG_CACHE_MAX_ENTRIES:
                _og_cache.popitem(last=False)

    # Advertise the same freshness the server cache actually enforces. The
    # underlying overall_score can change between daily runs, so a longer public
    # max-age would let browsers/proxies serve a stale card after the data moved.
    return _png_response(png, _OG_CACHE_TTL_S)


@app.api_route("/og-home.png", methods=["GET", "HEAD"])
def og_home_image():
    from src.api.og_image import render_home_card

    return _png_response(render_home_card(), _OG_STATIC_CACHE_S)


@app.api_route("/og-transparency.png", methods=["GET", "HEAD"])
def og_transparency_image():
    from src.api.og_image import render_transparency_card

    return _png_response(render_transparency_card(), _OG_STATIC_CACHE_S)


@app.api_route("/og-rankings.png", methods=["GET", "HEAD"])
def og_rankings_image():
    from src.api.og_image import render_rankings_card

    return _png_response(render_rankings_card(), _OG_STATIC_CACHE_S)


@app.api_route("/og-replay.png", methods=["GET", "HEAD"])
def og_replay_image():
    from src.api.og_image import render_replay_card

    return _png_response(render_replay_card(), _OG_STATIC_CACHE_S)


@app.api_route("/og-style.png", methods=["GET", "HEAD"])
def og_style_image():
    from src.api.og_image import render_style_card

    return _png_response(render_style_card(), _OG_STATIC_CACHE_S)


@app.api_route("/og-playoff-odds.png", methods=["GET", "HEAD"])
def og_playoff_odds_image():
    from src.api.og_image import render_playoff_odds_card

    return _png_response(render_playoff_odds_card(), _OG_STATIC_CACHE_S)


@app.api_route("/og-shot-making.png", methods=["GET", "HEAD"])
def og_shot_making_image():
    from src.api.og_image import render_shot_making_card

    return _png_response(render_shot_making_card(), _OG_STATIC_CACHE_S)


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
        # include_shapes attaches a thinned (~28pt) fever-line curve per game. The
        # completed payload is fetched on every homepage load (to populate the
        # toggle count) even though the section is collapsed by default, so this is
        # eager work for a not-yet-visible section — a deliberate, accepted tradeoff
        # (curves are thinned to keep it light; see the Replay Value design doc §3).
        # Known limitation, won't-fix unless observed: at season-end scale (~280
        # completed games) the shape data ~doubles this payload. Lever if it ever
        # matters: lazy-fetch curves only on first expand of the completed section.
        return format_games_response(rankings, session, include_shapes=True)
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
        return format_games_response(
            rankings, session, include_shapes=(mode == "completed")
        )
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


def _fetch_live_wp_cached(espn_id: str, timeout: int = 10) -> dict:
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
            payload = fetch_live_win_probability(espn_id, timeout=timeout)
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


# --- og:image card cache: bytes keyed by espn_id, TTL + simple LRU. ---
# og:image fetches are rare (a scraper crawls once per shared link), so this
# is for repeat-hit tidiness, not load. TTL bounds staleness since a game's
# overall_score can change between daily runs.
_OG_CACHE_TTL_S = 3600
_OG_CACHE_MAX_ENTRIES = 64
_OG_STATIC_CACHE_S = 86400  # brand cards change only on deploy — safe to cache 1 day
_og_cache: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()
# Sync `def` endpoints run in FastAPI's threadpool, so guard the cache's
# read-modify-write (move_to_end / eviction loop) like _live_wp_cache does.
_og_cache_lock = threading.Lock()


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


# /api/replay-live runs its per-game ESPN fetches on a SHARED bounded pool (not a
# per-request executor — that multiplied threads under concurrent viewers) and
# caches the whole slate briefly so concurrent viewers share one fetch.
_REPLAY_LIVE_TIMEOUT_S = 6
_REPLAY_LIVE_MAX_WORKERS = 6
_REPLAY_LIVE_CACHE_TTL_S = 15
_replay_live_pool = ThreadPoolExecutor(
    max_workers=_REPLAY_LIVE_MAX_WORKERS, thread_name_prefix="replay-live"
)
_replay_live_cache: "tuple[float, dict] | None" = None
_replay_live_cache_lock = threading.Lock()
_replay_live_build_lock = threading.Lock()

# Serializes the thriller poll's per-game check-send-record critical section so
# two overlapping polls (a slow poll still running at the next 5-min fire, or a
# manual replay) can't both pass the has_alerted check and double-send. One
# in-process lock suffices because --max-instances=1 pins us to a single
# container; record_alert is also idempotent as belt-and-suspenders.
_thriller_alert_lock = threading.Lock()


def _detect_live_shapes():
    """Shared detection: today+yesterday statuses -> DB-known live ids -> per-game
    live shape dicts, plus has_pending. A failed today-scoreboard fetch raises
    HTTPException(502): /api/replay-live surfaces it to the client, and the
    thriller poll lets it 502 the run so an ESPN outage during games is an
    observable/retriable failure rather than a fake-quiet success (the poll only
    reaches here after its DB self-gate confirmed a game just tipped off).
    """
    # Today primary; yesterday best-effort for late-ET games past the UTC-midnight
    # boundary.
    try:
        statuses = fetch_today_game_statuses(today_et())
    except ESPNAPIError as e:
        logger.warning("detect-live: today statuses failed: %s", e)
        raise HTTPException(status_code=502, detail="ESPN scoreboard unreachable")
    try:
        statuses = {**fetch_today_game_statuses(yesterday_et()), **statuses}
    except ESPNAPIError as e:
        logger.warning("detect-live: yesterday statuses failed (non-fatal): %s", e)

    # Gate to DB-known ids BEFORE any ESPN fetch — _fetch_live_wp_cached does not
    # gate (the /api/live-wp endpoint does), so this endpoint must re-apply it.
    # has_pending drives client polling; compute it from the SAME known set we can
    # actually render, so an unknown scoreboard id (schedule drift, an ESPN id
    # change) can't keep the client polling forever for a card that never appears.
    known = _get_known_espn_ids()
    pending, dropped = [], []
    for eid, s in statuses.items():
        if is_live_status(s) or s == "STATUS_SCHEDULED":
            (pending if eid in known else dropped).append(eid)
    if dropped:
        logger.warning(
            "detect-live: %d live/scheduled scoreboard id(s) absent from the DB "
            "allowlist (schedule drift?): %s",
            len(dropped),
            dropped,
        )
    has_pending = bool(pending)
    live_ids = [
        eid for eid, s in statuses.items() if is_live_status(s) and eid in known
    ]
    if not live_ids:
        return [], has_pending

    session = get_session()
    try:
        abbrs = get_team_abbrev_map(session)
    finally:
        session.close()

    # Fetch each live game's WP on the SHARED bounded pool (module-level, created
    # once) so N concurrent viewers can't each spawn their own worker set; a short
    # per-game timeout keeps one slow game from holding the slate. This whole slate
    # is response-cached by the caller, so concurrent viewers share one fetch.
    # _fetch_live_wp_cached is concurrency-safe (per-key locks).
    def _fetch_one(espn_id):
        try:
            return espn_id, _fetch_live_wp_cached(
                espn_id, timeout=_REPLAY_LIVE_TIMEOUT_S
            )
        except ESPNAPIError as e:  # incl. ESPNNotFoundError (subclass)
            logger.warning("detect-live: live-wp failed for %s: %s", espn_id, e)
            return espn_id, None

    payloads = dict(_replay_live_pool.map(_fetch_one, live_ids))

    games = []
    for espn_id in live_ids:  # preserve scoreboard order
        payload = payloads.get(espn_id)
        if payload is None:
            continue  # fetch failed — degrade to fewer cards, never 500
        shape = compute_live_shape(payload.get("plays", []))
        if shape is None:
            continue  # <2 usable plays (just tipped) — retry next poll
        home_name = payload.get("home_team", "")
        away_name = payload.get("away_team", "")
        games.append(
            {
                "espn_id": espn_id,
                "home_team": home_name,
                "away_team": away_name,
                "home_abbr": abbrs.get(home_name, home_name),
                "away_abbr": abbrs.get(away_name, away_name),
                "home_score": payload.get("home_score", ""),
                "away_score": payload.get("away_score", ""),
                "tension": shape.tension,
                "excitement": shape.excitement,
                "lead_changes": shape.lead_changes,
                "curve": shape.curve,
                "live": True,
            }
        )
    return games, has_pending


def _build_replay_live() -> dict:
    """Compute the live slate for /api/replay-live (raises 502 on today failure).
    get_replay_live() wraps this with the response cache."""
    games, has_pending = _detect_live_shapes()
    return {"games": games, "has_pending": has_pending}


@app.get("/api/replay-live")
def get_replay_live():
    """Live games as building fever-line cards for the /replay Live-now strip.

    Server-side sibling to /api/replay (DB-only). Single-flighted + cached briefly
    so concurrent viewers share ONE ESPN slate fetch; per-game fetches run on the
    shared bounded pool. Sync def so the blocking work runs in FastAPI's threadpool.
    """
    global _replay_live_cache
    with _replay_live_cache_lock:
        cached = _replay_live_cache
        if cached and cached[0] > time.monotonic():
            return cached[1]
    # Single-flight the build: one request builds while the rest wait on the build
    # lock, then reuse its cached result instead of each starting their own
    # scoreboard fetch + fan-out (mirrors _fetch_live_wp_cached's per-key lock).
    with _replay_live_build_lock:
        with _replay_live_cache_lock:
            cached = _replay_live_cache
            if cached and cached[0] > time.monotonic():
                return cached[1]  # another request built it while we waited
        result = _build_replay_live()  # raises HTTPException(502) on scoreboard fail
        with _replay_live_cache_lock:
            _replay_live_cache = (time.monotonic() + _REPLAY_LIVE_CACHE_TTL_S, result)
        return result


# How stale the freshest snapshot may be and still stand in for "today" on the
# default /api/playoff-odds request. Covers the pre-6AM daily-run window and a
# missed run; beyond it (offseason / multi-day outage) the page shows its empty
# state rather than presenting last season's final odds as current.
_PLAYOFF_FALLBACK_MAX_AGE_DAYS = 3


@app.get("/api/playoff-odds", response_model=list[PlayoffOddsResponse])
async def get_playoff_odds(date: str = Query(default=None)):
    """Return per-team round-by-round playoff probabilities.

    Sorted by make_playoffs_prob desc (the leftmost, most-read column, so the
    table reads monotonically), then win_championship_prob desc, then team name
    as tiebreakers.
    """
    today = today_et()
    explicit_date = date is not None
    if date is None:
        date = today
    is_today = date == today
    session = get_session()
    try:

        def _populated(d: str) -> dict:
            # Skip legacy rows from before the round-prob migration: if the champ
            # column is NULL we have no meaningful champ/finals/semis value, and
            # showing 0% would mislead. A "populated" snapshot has >= 1 real row.
            recs = get_playoff_probabilities(session, d)
            return {
                tid: r for tid, r in recs.items() if r.win_championship_prob is not None
            }

        recs = _populated(date)
        # No populated snapshot for today yet — the pre-6AM daily-run window, or a
        # missed/late run. On the DEFAULT (no ?date=) request, fall back to the
        # freshest RECENT day so the dedicated /playoff-odds page shows current-ish
        # odds instead of a blank shell. An explicit ?date= query stays exact (a
        # historical lookup must not silently jump to another date).
        #
        # The fallback is age-bounded: it must cover the pre-run window and a
        # missed run, but never present LAST SEASON's final odds as current. In
        # the offseason (or a multi-day outage) the latest populated day is older
        # than the cutoff, so we fall through to the honest empty state instead.
        if not recs and not explicit_date:
            # latest is < today here: we only reach this branch when _populated(today)
            # is empty, and get_latest_playoff_probability_date uses the same
            # "populated" predicate, so it can't return today.
            latest = get_latest_playoff_probability_date(session, today)
            cutoff = (
                date_cls.fromisoformat(today)
                - timedelta(days=_PLAYOFF_FALLBACK_MAX_AGE_DAYS)
            ).isoformat()
            if latest and latest >= cutoff:
                date = latest
                is_today = False
                recs = _populated(date)
        if not recs:
            return []
        teams = get_teams_by_ids(session, set(recs.keys()))
        # Regular-season W-L, attached ONLY for a today (live) request: records
        # are current-season-to-date, so pairing them with a historical ?date=
        # snapshot would publish mixed-time data. See get_team_records.
        records = get_team_records(session, int(date[:4])) if is_today else None
        rows = []
        for tid in recs:
            if tid not in teams:
                continue
            # Today: (w, l), or (0, 0) for a team with no completed games.
            # Historical: (None, None) — record omitted (see above).
            wins, losses = (
                records.get(tid, (0, 0)) if records is not None else (None, None)
            )
            rows.append(
                PlayoffOddsResponse(
                    team=teams[tid].name,
                    abbreviation=teams[tid].abbreviation or "",
                    logo_url=teams[tid].logo_url or "",
                    make_playoffs_prob=recs[tid].make_playoffs_prob,
                    reach_semis_prob=recs[tid].reach_semis_prob or 0.0,
                    reach_finals_prob=recs[tid].reach_finals_prob or 0.0,
                    win_championship_prob=recs[tid].win_championship_prob or 0.0,
                    wins=wins,
                    losses=losses,
                    seed_distribution=recs[tid].seed_distribution,
                )
            )
        return sorted(
            rows,
            key=lambda x: (-x.make_playoffs_prob, -x.win_championship_prob, x.team),
        )
    finally:
        session.close()


@app.get("/api/elo-history")
async def get_elo_history_endpoint(season: int = Query(default=None)):
    """Per-team Elo trajectory for a season (DB-only; never calls ESPN)."""
    if season is None:
        season = int(today_et()[:4])
    session = get_session()
    try:
        rows = get_elo_history(session, season)
        if not rows:
            return {"season": season, "teams": {}}
        teams = get_teams_by_ids(session, {r.team_id for r in rows})
        out: dict[str, list[dict]] = {}
        abbrevs: dict[str, str] = {}
        for r in rows:
            t = teams.get(r.team_id)
            if t is None:
                continue
            out.setdefault(t.name, []).append(
                {"date": r.date, "rating": round(r.rating, 1)}
            )
            abbrevs.setdefault(t.name, t.abbreviation or "")
        return {"season": season, "teams": out, "abbrevs": abbrevs}
    finally:
        session.close()


@app.get("/api/replay")
async def get_replay_endpoint(season: int = Query(default=None)):
    """Game-shape archive for a season (DB-only; reads game_shapes alone — it's
    self-contained, so no join). Returns the season's games + the list of seasons
    that have data (for the page's season selector). When no season is requested,
    defaults to the newest POPULATED season (not the current calendar year, which
    is empty off-season / before a new season's backfill)."""
    session = get_session()
    try:
        seasons = get_shape_seasons(session)
        if season is None:
            season = seasons[0] if seasons else int(today_et()[:4])
        known_ids = get_all_known_espn_ids(session)
        games = []
        for s in get_game_shapes(session, season):
            try:
                curve = json.loads(s.curve)
                if not isinstance(curve, list):
                    raise ValueError("curve is not a list")
            except (ValueError, TypeError):
                # A single corrupt stored curve (partial backfill / manual DB
                # repair) must degrade to a missing card, not blank the whole
                # season. Mirrors the importance_detail shape-guard convention.
                logger.warning(
                    "Skipping game_shape with unparseable curve (espn_id=%s)",
                    s.espn_id,
                )
                continue
            games.append(
                {
                    "espn_id": s.espn_id,
                    "date": s.date,
                    "home_abbr": s.home_abbr,
                    "away_abbr": s.away_abbr,
                    "home_score": s.home_score,
                    "away_score": s.away_score,
                    "winner": s.winner,
                    "excitement": round(s.excitement, 2),
                    "tension": round(s.tension, 4),
                    "comeback": round(s.comeback, 4),
                    "lead_changes": s.lead_changes,
                    "winner_low_wp": round(s.winner_low_wp, 4),
                    "has_detail": s.espn_id in known_ids,
                    "curve": curve,
                }
            )
        return {"season": season, "seasons": seasons, "games": games}
    finally:
        session.close()


@app.get("/api/team-style")
async def get_team_style_endpoint(season: int = Query(default=None)):
    """Per-team play-style fingerprints for a season (DB-only). Normalizes the
    raw team_style metrics league-relative and returns axes/chips/descriptor/
    neighbors. Defaults to the newest POPULATED season (mirrors /api/replay).
    W-L is attached only on a today request (current-season-to-date)."""
    session = get_session()
    try:
        today = today_et()
        seasons = get_team_style_seasons(session)
        if season is None:
            if not seasons:
                season = int(today[:4])
            else:
                season = seasons[0]
                # Don't default to a sparse newest season (e.g. a bootstrap
                # refresh early in a new season): if it has fewer teams than the
                # previous season, fall back to that previous complete season.
                if len(seasons) >= 2:
                    counts = get_team_style_season_counts(session)
                    if counts.get(seasons[0], 0) < counts.get(seasons[1], 0):
                        season = seasons[1]
        style_rows = get_team_styles(session, season)
        if not style_rows:
            return {"season": season, "teams": []}
        teams = get_teams_by_ids(session, {s.team_id for s in style_rows})
        rows = []
        for s in style_rows:
            t = teams.get(s.team_id)
            if t is None:
                continue
            rows.append(
                {
                    "team_id": s.team_id,
                    "team": t.name,
                    "abbr": t.abbreviation or "",
                    "pace": s.pace,
                    "three_pa_rate": s.three_pa_rate,
                    "ft_rate": s.ft_rate,
                    "oreb_pct": s.oreb_pct,
                    "assist_rate": s.assist_rate,
                    "def_pressure": s.def_pressure,
                    "opp_3pa_rate": s.opp_3pa_rate,
                    "games_played": s.games_played,
                }
            )
        view = compute_style_view(rows)
        # Attach logo + W-L (today only) and sort by standings.
        is_today = season == int(today[:4])
        records = get_team_records(session, season) if is_today else None
        for v in view:
            t = teams.get(v["team_id"])
            v["logo_url"] = (t.logo_url or "") if t else ""
            if records is not None:
                v["wins"], v["losses"] = records.get(v["team_id"], (0, 0))
            else:
                v["wins"], v["losses"] = None, None
        if records is not None:
            view.sort(key=lambda v: (-(v["wins"] or 0), (v["losses"] or 0), v["team"]))
        else:
            view.sort(key=lambda v: v["team"])
        return {"season": season, "teams": view}
    finally:
        session.close()


@app.get("/api/shot-making")
async def get_shot_making_endpoint():
    """Shot-making leaderboard for a season (DB-only, precomputed). Ranks players
    by points added over expected (actual - xPPS). Defaults to the newest
    populated season (mirrors /api/replay); v1 exposes no season param."""
    session = get_session()
    try:
        seasons = get_shot_making_seasons(session)
        season = seasons[0] if seasons else int(today_et()[:4])
        rows = get_shot_making(session, season)
        rows.sort(key=lambda r: r.points_added, reverse=True)
        players = []
        for i, r in enumerate(rows, start=1):
            try:
                diet = json.loads(r.diet) if r.diet else {}
            except (ValueError, TypeError):
                diet = {}
            players.append(
                {
                    "rank": i,
                    "athlete_id": r.athlete_id,
                    "athlete_name": r.athlete_name,
                    "team_id": r.team_id,
                    "team_abbr": r.team_abbr,
                    "fga": r.fga,
                    "made": r.made,
                    "made_pct": round(r.made / r.fga, 3) if r.fga else 0.0,
                    "actual_pps": r.actual_pps,
                    "expected_pps": r.expected_pps,
                    "points_added": r.points_added,
                    "points_added_per_100": r.points_added_per_100,
                    "diet": diet,
                }
            )
        return {"season": season, "players": players}
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
    # Fail closed: a missing/unloaded secret must reject, not run the job
    # unauthenticated. compare_digest avoids leaking the secret via timing.
    if not _TRIGGER_SECRET or not compare_digest(x_trigger_secret, _TRIGGER_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")

    from scripts.daily_update import main

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, main)
    if result != 0:
        raise HTTPException(status_code=500, detail="Daily update job failed")
    return {"status": "ok"}


def _run_thriller_poll() -> dict:
    """Self-gate on recent tipoffs (no ESPN if nothing's plausibly live), detect
    live shapes, and ping Telegram once per game that's Close/Thriller."""
    # Fail closed on missing Telegram config: a transient send failure stays soft
    # (retries next poll), but an unset token/chat_id is a permanent deploy/secret
    # error — surface it as a hard 500 so a misconfigured deploy can't masquerade
    # as healthy quiet runs. Mirrors the TRIGGER_SECRET fail-closed posture.
    if not telegram_configured():
        raise HTTPException(status_code=500, detail="Telegram not configured")

    now_utc = datetime.now(timezone.utc)
    session = get_session()
    try:
        candidates = get_games_by_date(session, today_et()) + get_games_by_date(
            session, yesterday_et()
        )
        date_by_id = {g.espn_id: g.date for g in candidates if g.espn_id}
    finally:
        session.close()

    # This gate is a COST gate, not an eligibility filter: it only decides whether
    # anything is plausibly live so we can skip the ESPN call on quiet nights. We
    # deliberately alert on ALL live Close/Thriller games below, not just the
    # recent-tipoff subset — intersecting with this 4h window would suppress a
    # legit long multi-OT thriller (tipoff >4h ago but still live).
    if not filter_recent_tipoffs(candidates, now_utc):
        return {"checked": 0, "alerted": 0}

    # A game just tipped (self-gate passed), so a today-scoreboard failure here is
    # a real failure: let _detect_live_shapes' 502 propagate → the poll run fails
    # loudly (Scheduler-retriable/observable) instead of a fake-quiet 200.
    games, _ = _detect_live_shapes()
    alerted = 0
    for g in games:
        # Gate the cumulative excitement label on the CURRENT win prob (last
        # curve point = home_pct) so a decided-but-formerly-wild game doesn't
        # ping. Missing/short curve → None → no suppression (never drop a real
        # alert on a data hiccup).
        curve = g.get("curve")
        current_wp = curve[-1][1] if curve else None
        label = live_excitement_label(g.get("excitement"), current_wp)
        if label is None:
            continue
        espn_id = g["espn_id"]
        # Lock the whole claim so overlapping polls can't slip between the
        # has_alerted check and record_alert and double-send. Use a short session
        # per DB op so no pooled connection is held across the blocking Telegram
        # POST (the repo's close-the-session-before-network-I/O pattern).
        with _thriller_alert_lock:
            session = get_session()
            try:
                already = has_alerted(session, espn_id)
            finally:
                session.close()
            if already or not send_telegram(compose_alert(g, label)):
                continue
            session = get_session()
            try:
                record_alert(
                    session, espn_id, date_by_id.get(espn_id, today_et()), label
                )
            finally:
                session.close()
            alerted += 1
    return {"checked": len(games), "alerted": alerted}


@app.post("/internal/thriller-poll")
async def trigger_thriller_poll(x_trigger_secret: str = Header(default="")):
    """Every-5-min Cloud Scheduler poll: ping me when a live game is a
    Close game / Thriller. Fail closed like /internal/daily-update."""
    if not _TRIGGER_SECRET or not compare_digest(x_trigger_secret, _TRIGGER_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_thriller_poll)
