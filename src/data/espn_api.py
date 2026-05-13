"""Fetch data from ESPN APIs for WNBA teams and games."""

import functools
import logging
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# ESPN uses inconsistent capitalizations for some team names across endpoints.
_TEAM_NAME_ALIASES: dict[str, str] = {
    "Connecticut SUN": "Connecticut Sun",
}


def _canonical_name(name: str) -> str:
    return _TEAM_NAME_ALIASES.get(name, name)


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


def fetch_games_for_range(start: date, end: date) -> list[dict]:
    """Return all parsed WNBA games between start and end dates (inclusive).

    Uses monthly batch requests. Filters out non-WNBA opponents.
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
        dt_et = dt_utc.astimezone(_ET)
        game_date = dt_et.strftime("%Y-%m-%d")

        time_valid = comp.get("timeValid", False)
        if not time_valid and dt_utc.hour == 0 and dt_utc.minute == 0:
            game_time = ""
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


def fetch_live_win_probability(espn_id: str) -> dict:
    """Fetch win probability data for a game from ESPN's summary endpoint.

    Returns dict with espn_id, status, home_team, away_team, and plays list.
    plays entries: {seq, period, clock, home_pct}. Empty list if no WP data.
    """
    data = _get(f"{SITE_API}/summary", event=espn_id)

    competitions = data.get("header", {}).get("competitions", [{}])
    comp = competitions[0] if competitions else {}
    status = comp.get("status", {}).get("type", {}).get("name", "STATUS_UNKNOWN")

    home_team = next(
        (
            _canonical_name(c.get("team", {}).get("displayName", ""))
            for c in comp.get("competitors", [])
            if c.get("homeAway") == "home"
        ),
        "",
    )
    away_team = next(
        (
            _canonical_name(c.get("team", {}).get("displayName", ""))
            for c in comp.get("competitors", [])
            if c.get("homeAway") == "away"
        ),
        "",
    )

    play_lookup: dict[str, dict] = {
        str(p.get("id", "")): {
            "period": p.get("period", {}).get("number", 1),
            "clock": p.get("clock", {}).get("displayValue", ""),
        }
        for p in data.get("plays", [])
    }

    plays = []
    for i, entry in enumerate(data.get("winprobability", [])):
        play_id = str(entry.get("playId", ""))
        info = play_lookup.get(play_id, {"period": 1, "clock": ""})
        plays.append(
            {
                "seq": i,
                "period": info["period"],
                "clock": info["clock"],
                "home_pct": entry.get("homeWinPercentage", 0.5),
            }
        )

    return {
        "espn_id": espn_id,
        "status": status,
        "home_team": home_team,
        "away_team": away_team,
        "plays": plays,
    }
