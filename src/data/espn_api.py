"""Fetch data from ESPN APIs for WNBA teams and games."""

import functools
import logging
import math
import re
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from src.scoring.excitement import elapsed_seconds

logger = logging.getLogger(__name__)

# ESPN uses inconsistent capitalizations for some team names across endpoints.
_TEAM_NAME_ALIASES: dict[str, str] = {
    "Connecticut SUN": "Connecticut Sun",
}


def _canonical_name(name: str) -> str:
    return _TEAM_NAME_ALIASES.get(name, name)


SITE_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
CORE_API = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba"
# Team season aggregates (own + opponent splits) — a THIRD ESPN host beyond
# SITE_API / CORE_API. Feeds the /style fingerprints.
STATS_API = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/wnba/statistics/byteam"

ET = ZoneInfo("America/New_York")
# Schedule fetch horizon. Must extend through the Finals — WNBA playoffs run
# from late September into late October (Bo7 Finals can finish ~Oct 20-25).
# `_SEASON_END.year` is also used as the season identifier; keep it in the
# regular-season calendar year.
_SEASON_END = date(2026, 10, 31)


def today_et() -> str:
    """Return today's date in America/New_York as 'YYYY-MM-DD'.

    Why: Game.date is stored in ET (schedule fetcher parses ESPN times via ET).
    Cloud Run uses UTC, so datetime.now() rolls to tomorrow after 8 PM ET and
    filters out tonight's still-upcoming games.
    """
    return datetime.now(ET).strftime("%Y-%m-%d")


def yesterday_et() -> str:
    """ET date one day before today_et(), as 'YYYY-MM-DD'.

    Used to widen API windows (upcoming list + live-status) for non-ET
    viewers around the UTC midnight boundary. A late-ET game that's
    crossed into yesterday-ET is still in *today* locally for any
    viewer west of Eastern.
    """
    d = datetime.strptime(today_et(), "%Y-%m-%d").date()
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


class ESPNAPIError(Exception):
    pass


class ESPNNotFoundError(ESPNAPIError):
    pass


def _get(url: str, timeout: int = 10, **params) -> dict:
    """GET with standard error handling."""
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise ESPNNotFoundError(f"ESPN returned 404 for {url}") from e
        raise ESPNAPIError(f"ESPN API request failed: {e}") from e
    except requests.RequestException as e:
        raise ESPNAPIError(f"ESPN API request failed: {e}") from e


def _team_id_from_ref(ref: str) -> Optional[int]:
    """Parse team ID out of a $ref URL like .../teams/8?lang=..."""
    try:
        path = ref.split("?")[0]
        return int(path.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
        return None


@functools.lru_cache(maxsize=1)
def _fetch_teams_raw() -> list:
    """Fetch /teams once per process; cached so callers don't duplicate the request."""
    data = _get(f"{SITE_API}/teams")
    return data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])


def fetch_team_id_map() -> dict[int, str]:
    """Return {espn_team_id: display_name} for all WNBA teams."""
    return {
        int(t["team"]["id"]): _canonical_name(t["team"]["displayName"])
        for t in _fetch_teams_raw()
    }


def fetch_team_details() -> dict[str, dict]:
    """Return {display_name: {"abbreviation", "logo_url"}} for all WNBA teams."""
    out: dict[str, dict] = {}
    for t in _fetch_teams_raw():
        team = t["team"]
        # Prefer the default full-color logo; first "default" rel tag is consistent across teams.
        logo_url = ""
        for logo in team.get("logos", []):
            if "default" in logo.get("rel", []):
                logo_url = logo.get("href", "")
                break
        if not logo_url and team.get("logos"):
            logo_url = team["logos"][0].get("href", "")
        out[_canonical_name(team["displayName"])] = {
            "abbreviation": team.get("abbreviation", ""),
            "logo_url": logo_url,
        }
    return out


def fetch_bpi_ratings() -> dict[str, float]:
    """Return {team_display_name: bpi_value} for the current season."""
    team_names = fetch_team_id_map()
    season = _SEASON_END.year
    data = _get(f"{CORE_API}/seasons/{season}/powerindex", limit=50)
    ratings = {}
    for item in data.get("items", []):
        ref = item.get("team", {}).get("$ref", "")
        team_id = _team_id_from_ref(ref)
        if team_id is None:
            continue
        name = team_names.get(team_id)
        if not name:
            continue
        for stat in item.get("stats", []):
            if stat["name"] == "bpi":
                ratings[name] = stat["value"]
                break
    if not ratings:
        logger.error(f"Could not fetch BPI ratings for {season} season")
    else:
        logger.info(f"Fetched BPI for {len(ratings)} teams from {season} season")
    return ratings


def _index_maps(categories: list) -> dict[str, dict[str, int]]:
    """{category_name: {stat_name: value_index}} from the top-level label schema
    (only the entries that carry a `names` list)."""
    out: dict[str, dict[str, int]] = {}
    for c in categories:
        names = c.get("names")
        if names:
            out[c["name"]] = {n: i for i, n in enumerate(names)}
    return out


def _split_values(team: dict, cat_name: str, split_id: str) -> Optional[list]:
    for c in team.get("categories", []):
        if c.get("name") == cat_name and str(c.get("splitId")) == split_id:
            return c.get("values")
    return None


def _stat(values: Optional[list], idx: dict[str, int], name: str) -> Optional[float]:
    """A finite float from `values` at the label `name`, or None."""
    if values is None or name not in idx or idx[name] >= len(values):
        return None
    v = values[idx[name]]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def fetch_team_style_stats(season: int) -> list[dict]:
    """Return raw play-style metrics per team for `season` (regular season).

    One dict per team: {team, pace, three_pa_rate, ft_rate, oreb_pct,
    assist_rate, def_pressure, games_played}. Teams missing any required field
    are skipped (logged). Derived from ESPN's byteam own (splitId 0) + opponent
    (splitId 900) offensive splits; possessions = FGA + 0.44*FTA - OREB + TOV.
    """
    data = _get(
        STATS_API,
        region="us",
        lang="en",
        contentorigin="espn",
        season=season,
        seasontype=2,
    )
    idx = _index_maps(data.get("categories", []))
    off_idx = idx.get("offensive", {})
    gen_idx = idx.get("general", {})
    # Resolve each team by its stable ESPN id -> canonical /teams name (the same
    # source our teams table is built from), NOT byteam's displayName string,
    # which can vary in capitalization/spelling across ESPN endpoints.
    id_to_name = fetch_team_id_map()
    rows = []
    for team in data.get("teams", []):
        team_meta = team.get("team", {})
        try:
            espn_id = int(team_meta.get("id"))
        except (TypeError, ValueError):
            espn_id = None
        name = id_to_name.get(espn_id)
        if name is None:
            logger.warning(
                "Team-style: byteam id %r not in team map — skipping",
                team_meta.get("id"),
            )
            continue
        own = _split_values(team, "offensive", "0")
        opp = _split_values(team, "offensive", "900")
        gen = _split_values(team, "general", "0")
        fgm = _stat(own, off_idx, "avgFieldGoalsMade")
        fga = _stat(own, off_idx, "avgFieldGoalsAttempted")
        tpa = _stat(own, off_idx, "avgThreePointFieldGoalsAttempted")
        fta = _stat(own, off_idx, "avgFreeThrowsAttempted")
        tov = _stat(own, off_idx, "avgTurnovers")
        oreb = _stat(own, off_idx, "avgOffensiveRebounds")
        ast = _stat(own, off_idx, "avgAssists")
        oreb_pct = _stat(own, off_idx, "offensiveReboundPct")
        o_fga = _stat(opp, off_idx, "avgFieldGoalsAttempted")
        o_fta = _stat(opp, off_idx, "avgFreeThrowsAttempted")
        o_tov = _stat(opp, off_idx, "avgTurnovers")
        o_oreb = _stat(opp, off_idx, "avgOffensiveRebounds")
        o_3pa = _stat(opp, off_idx, "avgThreePointFieldGoalsAttempted")
        gp = _stat(gen, gen_idx, "gamesPlayed")
        required = [
            fgm,
            fga,
            tpa,
            fta,
            tov,
            oreb,
            ast,
            oreb_pct,
            o_fga,
            o_fta,
            o_tov,
            o_oreb,
            o_3pa,
            gp,
        ]
        if any(x is None for x in required) or fga <= 0 or fgm <= 0:
            logger.warning("Skipping team-style row (incomplete): %r", name)
            continue
        poss_own = fga + 0.44 * fta - oreb + tov
        poss_opp = o_fga + 0.44 * o_fta - o_oreb + o_tov
        if poss_own <= 0 or poss_opp <= 0:
            logger.warning("Skipping team-style row (bad possessions): %r", name)
            continue
        rows.append(
            {
                "team": name,
                "pace": (poss_own + poss_opp) / 2.0,
                "three_pa_rate": tpa / fga,
                "ft_rate": fta / fga,
                # ESPN's offensiveReboundPct arrives as a fraction (~0.20-0.30);
                # scale to a true percent to match the "OREB%" label. Used
                # directly (no OREB/(OREB+oppDREB) fallback) — ESPN reliably
                # provides it (verified live across all teams).
                "oreb_pct": oreb_pct * 100.0,
                "assist_rate": ast / fgm,
                "def_pressure": o_tov / poss_opp,
                # Opponent 3-point attempt rate — how 3-heavy opponents' shots
                # are against this team (perimeter-defense identity: concede vs
                # limit the arc). A defensive-STYLE axis, not quality.
                "opp_3pa_rate": o_3pa / o_fga,
                "games_played": int(gp),
            }
        )
    if not rows:
        logger.error("Could not fetch team-style stats for %s season", season)
    else:
        logger.info("Fetched team-style stats for %d teams (%s)", len(rows), season)
    return rows


def fetch_games_for_range(
    start: date, end: date, failed_windows: list[str] | None = None
) -> list[dict]:
    """Return all parsed WNBA games between start and end dates (inclusive).

    Uses monthly batch requests. Filters out non-WNBA opponents. A failed
    monthly fetch is logged and skipped (the daily Elo path must degrade, not
    crash); pass `failed_windows` to have those skipped `YYYYMMDD-YYYYMMDD`
    windows recorded, so a caller needing completeness (the one-shot backfill)
    can detect the gap and fail closed instead of reporting a partial run.
    """
    wnba_teams = set(fetch_team_id_map().values())
    all_games: list[dict] = []
    seen_ids: set[str] = set()

    cursor = start.replace(day=1)
    while cursor <= end:
        next_month = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12
            else cursor.replace(month=cursor.month + 1)
        )
        range_start = max(start, cursor)
        range_end = min(next_month - timedelta(days=1), end)
        date_param = f"{range_start.strftime('%Y%m%d')}-{range_end.strftime('%Y%m%d')}"

        try:
            data = _get(f"{SITE_API}/scoreboard", dates=date_param)
        except ESPNAPIError as e:
            logger.warning(f"Failed to fetch scoreboard for {date_param}: {e}")
            if failed_windows is not None:
                failed_windows.append(date_param)
            cursor = next_month
            continue

        for event in data.get("events", []):
            event_id = event.get("id")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            game = _parse_event(event)
            if game and game["team_a"] in wnba_teams and game["team_b"] in wnba_teams:
                all_games.append(game)

        cursor = next_month

    return all_games


def fetch_schedule_and_results() -> list[dict]:
    """Return all games from yesterday through end of season, WNBA teams only."""
    today = date.today()
    # Include yesterday to catch games that just finished
    games = fetch_games_for_range(today - timedelta(days=1), _SEASON_END)
    logger.info(f"Fetched {len(games)} WNBA games through end of season")
    return games


def _parse_event(event: dict) -> Optional[dict]:
    """Parse a single scoreboard event into a flat game dict."""
    try:
        comp = event["competitions"][0]
        date_str = event.get("date", "")
        if not date_str:
            return None

        # ESPN season type: 1=preseason, 2=regular, 3=postseason
        season_type = event.get("season", {}).get("type", 2)

        dt_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

        # ESPN's `timeValid` flag is authoritative when explicitly
        # present. Distinguish three states:
        #   - explicit False → ESPN says the time is a placeholder; clear.
        #   - missing → field absent. Fall back to the legacy midnight-UTC
        #     sentinel (don't assume TBD on every payload that omits
        #     the optional flag — that would silently erase live and
        #     completed game times if ESPN ever stops sending it).
        #   - explicit True → trust the timestamp.
        # For the TBD branch, don't TZ-shift the placeholder time-of-
        # day when deriving game_date: ESPN's canonical sentinel
        # `YYYY-MM-DDT00:00:00Z` carries the intended ET game date in
        # its UTC calendar component; shifting to ET first would move
        # the row to the previous day.
        time_valid_raw = comp.get("timeValid")
        explicit_tbd = time_valid_raw is False
        implicit_tbd = (
            time_valid_raw is None and dt_utc.hour == 0 and dt_utc.minute == 0
        )
        if explicit_tbd or implicit_tbd:
            game_date = dt_utc.strftime("%Y-%m-%d")
            game_time = ""
            game_time_utc = None
        else:
            dt_et = dt_utc.astimezone(ET)
            game_date = dt_et.strftime("%Y-%m-%d")
            game_time = dt_et.strftime("%-I:%M %p ET")
            game_time_utc = dt_utc.isoformat()

        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return None

        team_a_info = next(
            (c for c in competitors if c.get("homeAway") == "home"), competitors[0]
        )
        team_b_info = next(
            (c for c in competitors if c.get("homeAway") == "away"), competitors[1]
        )

        team_a = _canonical_name(team_a_info["team"]["displayName"])
        team_b = _canonical_name(team_b_info["team"]["displayName"])

        status = comp["status"]["type"]["name"]
        is_final = status == "STATUS_FINAL"

        winner_team = None
        final_score_a = None
        final_score_b = None

        if is_final:
            final_score_a = int(team_a_info.get("score", 0))
            final_score_b = int(team_b_info.get("score", 0))
            if final_score_a > final_score_b:
                winner_team = team_a
            elif final_score_b > final_score_a:
                winner_team = team_b

        broadcaster = _parse_broadcaster(comp)

        return {
            "event_id": event["id"],
            "team_a": team_a,
            "team_b": team_b,
            "date": game_date,
            "time": game_time,
            "time_utc": game_time_utc,
            "winner_team": winner_team,
            "final_score_a": final_score_a,
            "final_score_b": final_score_b,
            "broadcaster": broadcaster,
            "status": status,
            "season_type": season_type,
        }
    except (KeyError, ValueError, TypeError, IndexError) as e:
        logger.warning(f"Failed to parse event {event.get('id')}: {e}")
        return None


def _parse_broadcaster(comp: dict) -> str:
    """Extract broadcaster name from competition data."""
    from src.constants import BROADCASTER_NORMALIZE, Broadcasters

    for broadcast in comp.get("geoBroadcasts", []) + comp.get("broadcasts", []):
        names = broadcast.get("names", [])
        media = broadcast.get("media", {}).get("shortName", "")
        for candidate in names + ([media] if media else []):
            normalized = BROADCASTER_NORMALIZE.get(candidate.upper())
            if normalized:
                return normalized

    # No national broadcaster found — game is on League Pass (possibly blacked out locally)
    return Broadcasters.LEAGUE_PASS


def _valid_home_pct(value) -> float | None:
    """Return `value` as a float win-probability in [0, 1], or None if it
    isn't a trustworthy ESPN sample.

    ESPN's contract is a JSON number in [0, 1]; anything else (explicit
    null, a string, NaN/inf, or out-of-range) is schema drift we must not
    trust. bool is rejected explicitly (it's an int subclass in Python).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not (0.0 <= value <= 1.0):
        return None
    return float(value)


def fetch_live_win_probability(espn_id: str, timeout: int = 10) -> dict:
    """Fetch win probability data for a game from ESPN's summary endpoint.

    Returns dict with espn_id, status, home_team, away_team, and plays list.
    plays entries: {seq, period, clock, home_pct}. Empty list if no WP data.

    `timeout` lets backfill callers use a shorter window than the
    live-WP-panel default; one slow game shouldn't stall the daily job.
    """
    data = _get(f"{SITE_API}/summary", timeout=timeout, event=espn_id)

    competitions = data.get("header", {}).get("competitions", [{}])
    comp = competitions[0] if competitions else {}
    status = comp.get("status", {}).get("type", {}).get("name", "STATUS_UNKNOWN")

    competitors = comp.get("competitors", [])
    home_competitor = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away_competitor = next((c for c in competitors if c.get("homeAway") == "away"), {})

    home_team = _canonical_name(home_competitor.get("team", {}).get("displayName", ""))
    away_team = _canonical_name(away_competitor.get("team", {}).get("displayName", ""))
    home_score = home_competitor.get("score", "")
    away_score = away_competitor.get("score", "")

    # Index only plays that carry a real period + clock. A play missing either
    # has no trustworthy game-time, so its WP sample is dropped below (same path
    # as an unmatched playId) rather than kept with a fabricated default that the
    # time sort + time-weighted metrics would then trust.
    play_lookup: dict[str, dict] = {}
    for p in data.get("plays", []):
        pid = str(p.get("id", ""))
        period = p.get("period", {}).get("number")
        clock = p.get("clock", {}).get("displayValue", "")
        if pid and isinstance(period, int) and clock:
            play_lookup[pid] = {"period": period, "clock": clock}

    # Sanitize homeWinPercentage at the boundary so every downstream consumer
    # (excitement scorer, WP chart, homepage WP text) inherits clean data.
    # ESPN can emit an explicit null/string/NaN/out-of-range value; trusting
    # those produces NaN math and broken renders, and synthesizing a default
    # (e.g. the old 0.5) fabricates a confident coin-flip the model never
    # emitted. Drop untrustworthy samples entirely: kept play count then
    # equals the real-sample count, so the stored-excitement path's
    # len(plays) >= 2 guard correctly leaves insufficient feeds NULL for
    # retry rather than archiving a fabricated score (Codex review).
    plays = []
    dropped_untimed = 0
    for entry in data.get("winprobability", []):
        pct = _valid_home_pct(entry.get("homeWinPercentage"))
        if pct is None:
            continue
        play_id = str(entry.get("playId", ""))
        info = play_lookup.get(play_id)
        if info is None:
            # No trustworthy game-time for this sample — its playId is unmatched
            # OR the matched play was missing a period/clock (so it's absent from
            # play_lookup). Either way it can't be placed on the time axis the
            # curve + excitement / tension / lead-change metrics all assume, so
            # drop it (like an invalid home_pct above) rather than fabricate a
            # clock the sort would then trust. Rare; a feed where most samples
            # are untimed falls below the len>=2 guard and is left NULL-for-retry.
            dropped_untimed += 1
            continue
        plays.append(
            {
                "seq": len(plays),
                "period": info["period"],
                "clock": info["clock"],
                "home_pct": pct,
            }
        )
    if dropped_untimed:
        logger.warning(
            "Dropped %d WP sample(s) with no resolvable game-time for event=%s",
            dropped_untimed,
            espn_id,
        )

    # ESPN's winprobability array isn't strictly game-time ordered — a handful
    # of plays per game land out of sequence. The curve plots x by elapsed game
    # time and the excitement / lead-change metrics assume time order, so sort
    # here. Every kept sample now has a real period+clock (untimed ones dropped
    # above), so the sort is always sound. Stable sort preserves equal-time
    # order; resequence seq over the result.
    plays.sort(key=elapsed_seconds)
    for i, play in enumerate(plays):
        play["seq"] = i

    return {
        "espn_id": espn_id,
        "status": status,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "plays": plays,
    }


def _parse_distance_ft(text: str) -> Optional[float]:
    """Shot distance in feet parsed from ESPN's play text ("23-foot ..."). None
    when the text carries no distance (e.g. some putbacks) — the shot still
    buckets by family × point value."""
    m = re.search(r"(\d+)-foot", text or "")
    return float(m.group(1)) if m else None


def _shot_point_value(text: str, score_value) -> int:
    """2 or 3 for a field-goal attempt. Made shots carry scoreValue (2/3);
    misses carry 0, so fall back to the "three point" phrase ESPN emits on
    missed threes."""
    if "three point" in (text or "").lower():
        return 3
    if score_value in (2, 3):
        return int(score_value)
    return 2


def _boxscore_athlete_map(data: dict) -> dict:
    """athlete_id -> (displayName, team_id, team_abbr) from the summary
    boxscore — the authoritative name/team source (participants carry only
    ids)."""
    out = {}
    for team in data.get("boxscore", {}).get("players", []):
        team_id = str(team.get("team", {}).get("id", ""))
        team_abbr = team.get("team", {}).get("abbreviation", "")
        for stat in team.get("statistics", []):
            for a in stat.get("athletes", []):
                ath = a.get("athlete", {})
                aid = str(ath.get("id", ""))
                if aid:
                    out[aid] = (ath.get("displayName", ""), team_id, team_abbr)
    return out


def fetch_shots(espn_id: str, timeout: int = 10) -> list[dict]:
    """Field-goal attempts for a game from ESPN's summary play-by-play.

    Returns one dict per FGA (free throws excluded). Shooter is participants[0]
    (verified: the shooter even on a blocked shot); name/team resolved via the
    boxscore. A play with no resolvable shooter is skipped (logged) rather than
    misattributed — the daily job must degrade, not crash.
    """
    data = _get(f"{SITE_API}/summary", timeout=timeout, event=espn_id)
    names = _boxscore_athlete_map(data)
    shots: list[dict] = []
    skipped = 0
    for p in data.get("plays", []):
        if not p.get("shootingPlay"):
            continue
        shot_type = p.get("type", {}).get("text", "") or ""
        if shot_type.startswith("Free Throw"):
            continue
        participants = p.get("participants", []) or []
        shooter_id = str(
            (participants[0].get("athlete", {}) if participants else {}).get("id", "")
        )
        info = names.get(shooter_id)
        play_id = str(p.get("id", ""))
        if not shooter_id or info is None or not play_id:
            skipped += 1
            continue
        name, team_id, team_abbr = info
        text = p.get("text", "") or ""
        coord = p.get("coordinate") or {}
        cx, cy = coord.get("x"), coord.get("y")
        shots.append(
            {
                "play_id": play_id,
                "athlete_id": shooter_id,
                "athlete_name": name,
                "team_id": team_id,
                "team_abbr": team_abbr,
                "shot_type": shot_type,
                "distance_ft": _parse_distance_ft(text),
                "coord_x": int(cx) if isinstance(cx, (int, float)) else None,
                "coord_y": int(cy) if isinstance(cy, (int, float)) else None,
                "points": int(p.get("scoreValue") or 0) if p.get("scoringPlay") else 0,
                "point_value": _shot_point_value(text, p.get("scoreValue")),
                "made": bool(p.get("scoringPlay")),
            }
        )
    if skipped:
        logger.warning(
            "fetch_shots(%s): skipped %d unresolvable shooting plays", espn_id, skipped
        )
    return shots


def fetch_today_game_statuses(game_date: str) -> dict[str, str]:
    """Return {espn_id: status_name} for all games on game_date (YYYY-MM-DD).

    Raises ESPNAPIError on failure — callers decide whether to surface or
    swallow. (A previous version silently returned {} on error, which made
    the response indistinguishable from "no games today" and broke retries.)
    """
    data = _get(f"{SITE_API}/scoreboard", timeout=5, dates=game_date.replace("-", ""))
    result: dict[str, str] = {}
    for event in data.get("events", []):
        event_id = event.get("id", "")
        if not event_id:
            continue
        status = (
            event.get("competitions", [{}])[0]
            .get("status", {})
            .get("type", {})
            .get("name", "")
        )
        result[event_id] = status
    return result
