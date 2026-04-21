"""Fetch data from ESPN APIs for WNBA teams and games."""

import logging
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

SITE_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
CORE_API = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba"

_ET = ZoneInfo("America/New_York")
_SEASON_END = date(2026, 9, 30)


class ESPNAPIError(Exception):
    pass


def _get(url: str, **params) -> dict:
    """GET with standard error handling."""
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise ESPNAPIError(f"ESPN API request failed: {e}") from e


def _team_id_from_ref(ref: str) -> Optional[int]:
    """Parse team ID out of a $ref URL like .../teams/8?lang=..."""
    try:
        path = ref.split("?")[0]
        return int(path.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
        return None


def fetch_team_id_map() -> dict[int, str]:
    """Return {espn_team_id: display_name} for all WNBA teams."""
    data = _get(f"{SITE_API}/teams")
    teams = data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
    return {int(t["team"]["id"]): t["team"]["displayName"] for t in teams}


def fetch_team_details() -> dict[str, dict]:
    """Return {display_name: {"abbreviation", "logo_url"}} for all WNBA teams."""
    data = _get(f"{SITE_API}/teams")
    teams = data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
    out: dict[str, dict] = {}
    for t in teams:
        team = t["team"]
        # Prefer the default full-color logo; first "default" rel tag is consistent across teams.
        logo_url = ""
        for logo in team.get("logos", []):
            if "default" in logo.get("rel", []):
                logo_url = logo.get("href", "")
                break
        if not logo_url and team.get("logos"):
            logo_url = team["logos"][0].get("href", "")
        out[team["displayName"]] = {
            "abbreviation": team.get("abbreviation", ""),
            "logo_url": logo_url,
        }
    return out


def fetch_bpi_ratings() -> dict[str, float]:
    """Return {team_display_name: bpi_value} using the most recent season with data."""
    team_names = fetch_team_id_map()

    for season in (2026, 2025):
        data = _get(f"{CORE_API}/seasons/{season}/powerindex", limit=50)
        items = data.get("items", [])
        if not items:
            logger.info(f"BPI season {season} has no data, trying previous season")
            continue

        ratings = {}
        for item in items:
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

        if ratings:
            logger.info(f"Fetched BPI for {len(ratings)} teams from {season} season")
            return ratings

    logger.error("Could not fetch BPI ratings from any season")
    return {}


def fetch_schedule_and_results() -> list[dict]:
    """Return all games from today through end of season, WNBA teams only.

    Uses monthly batch requests instead of day-by-day to minimize API calls.
    Filters out exhibition games against non-WNBA opponents.
    """
    wnba_teams = set(fetch_team_id_map().values())

    today = date.today()
    all_games: list[dict] = []
    seen_ids: set[str] = set()

    # Walk month by month from today through season end
    cursor = today.replace(day=1)
    while cursor <= _SEASON_END:
        # Include yesterday to catch any games that just finished
        range_start = max(today - timedelta(days=1), cursor)
        # Last day of this month (or season end, whichever is sooner)
        if cursor.month == 12:
            next_month = cursor.replace(year=cursor.year + 1, month=1)
        else:
            next_month = cursor.replace(month=cursor.month + 1)
        range_end = min(next_month - timedelta(days=1), _SEASON_END)

        date_param = f"{range_start.strftime('%Y%m%d')}-{range_end.strftime('%Y%m%d')}"
        try:
            data = _get(f"{SITE_API}/scoreboard", dates=date_param)
        except ESPNAPIError as e:
            logger.warning(f"Failed to fetch scoreboard for {date_param}: {e}")
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

    logger.info(f"Fetched {len(all_games)} WNBA games through end of season")
    return all_games


def _parse_event(event: dict) -> Optional[dict]:
    """Parse a single scoreboard event into a flat game dict."""
    try:
        comp = event["competitions"][0]
        date_str = event.get("date", "")
        if not date_str:
            return None

        dt_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        dt_et = dt_utc.astimezone(_ET)
        game_date = dt_et.strftime("%Y-%m-%d")

        # 00:00 UTC typically means time TBD
        if dt_utc.hour == 0 and dt_utc.minute == 0:
            game_time = "TBD"
        else:
            game_time = dt_et.strftime("%-I:%M %p ET")

        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return None

        team_a_info = next(
            (c for c in competitors if c.get("homeAway") == "home"), competitors[0]
        )
        team_b_info = next(
            (c for c in competitors if c.get("homeAway") == "away"), competitors[1]
        )

        team_a = team_a_info["team"]["displayName"]
        team_b = team_b_info["team"]["displayName"]

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
            "winner_team": winner_team,
            "final_score_a": final_score_a,
            "final_score_b": final_score_b,
            "broadcaster": broadcaster,
            "status": status,
        }
    except (KeyError, ValueError, TypeError, IndexError) as e:
        logger.warning(f"Failed to parse event {event.get('id')}: {e}")
        return None


def _parse_broadcaster(comp: dict) -> str:
    """Extract broadcaster name from competition data."""
    from src.constants import BROADCASTER_NORMALIZE

    for broadcast in comp.get("geoBroadcasts", []) + comp.get("broadcasts", []):
        names = broadcast.get("names", [])
        media = broadcast.get("media", {}).get("shortName", "")
        for candidate in names + ([media] if media else []):
            normalized = BROADCASTER_NORMALIZE.get(candidate.upper())
            if normalized:
                return normalized

    return ""
