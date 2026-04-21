"""Fetch data from ESPN APIs for WNBA teams and games."""

import requests
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

SITE_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
CORE_API = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba"


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


def fetch_schedule_and_results(days_ahead: int = 7) -> list[dict]:
    """Return upcoming + recent games from the ESPN scoreboard.

    Fetches today + days_ahead days of games.
    """
    today = datetime.now()
    all_games = []
    seen_ids = set()

    # Fetch a window: yesterday through days_ahead
    for offset in range(-1, days_ahead + 1):
        date = today + timedelta(days=offset)
        date_str = date.strftime("%Y%m%d")
        try:
            data = _get(f"{SITE_API}/scoreboard", dates=date_str)
        except ESPNAPIError as e:
            logger.warning(f"Failed to fetch scoreboard for {date_str}: {e}")
            continue

        for event in data.get("events", []):
            event_id = event.get("id")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            game = _parse_event(event)
            if game:
                all_games.append(game)

    logger.info(f"Fetched {len(all_games)} games from ESPN scoreboard")
    return all_games


def _parse_event(event: dict) -> Optional[dict]:
    """Parse a single scoreboard event into a flat game dict."""
    try:
        comp = event["competitions"][0]
        date_str = event.get("date", "")
        if not date_str:
            return None

        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        date = dt.strftime("%Y-%m-%d")
        time = dt.strftime("%H:%M")

        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return None

        # ESPN puts home team first in the competitors list
        team_a_info = next(
            (c for c in competitors if c.get("homeAway") == "home"), competitors[0]
        )
        team_b_info = next(
            (c for c in competitors if c.get("homeAway") == "away"), competitors[1]
        )

        team_a = team_a_info["team"]["displayName"]
        team_b = team_b_info["team"]["displayName"]

        status = comp["status"]["type"][
            "name"
        ]  # e.g. "STATUS_FINAL", "STATUS_SCHEDULED"
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
            "date": date,
            "time": time,
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
