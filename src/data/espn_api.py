"""Fetch data from ESPN APIs for WNBA teams and games."""

import requests
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ESPNAPIError(Exception):
    """Exception for ESPN API errors."""

    pass


def fetch_bpi_ratings() -> dict[str, float]:
    """Fetch current BPI ratings for all WNBA teams.

    Returns a dict mapping team names to BPI scores.
    """
    url = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba/seasons/2025/powerindex"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        bpi_ratings = {}
        if "items" in data:
            for item in data["items"]:
                team_ref = item.get("team", {})
                team_id = team_ref.get("$ref", "").split("/")[-1]
                team_name = item.get("name", "")
                bpi = item.get("bpi", 0.0)

                if team_name:
                    bpi_ratings[team_name] = bpi
                    logger.info(f"BPI: {team_name} = {bpi}")

        return bpi_ratings
    except requests.RequestException as e:
        logger.error(f"Failed to fetch BPI ratings: {e}")
        raise ESPNAPIError(f"Failed to fetch BPI ratings: {e}")


def fetch_schedule_and_results(
    season: int = 2025,
) -> list[dict]:
    """Fetch schedule and game results from ESPN.

    Returns a list of games with team_a, team_b, date, time, winner_id, final_score_a, final_score_b.
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/schedule?season={season}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        games = []
        events = data.get("events", [])

        for event in events:
            game = parse_game_event(event)
            if game:
                games.append(game)

        logger.info(f"Fetched {len(games)} games from ESPN schedule")
        return games
    except requests.RequestException as e:
        logger.error(f"Failed to fetch schedule: {e}")
        raise ESPNAPIError(f"Failed to fetch schedule: {e}")


def parse_game_event(event: dict) -> Optional[dict]:
    """Parse a single game event from ESPN API."""
    try:
        event_id = event.get("id", "")
        date_str = event.get("date", "")
        status = event.get("status", {}).get("type", "")

        # Extract date and time
        if date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            date = dt.strftime("%Y-%m-%d")
            time = dt.strftime("%H:%M")
        else:
            return None

        # Get competitor info
        competitors = event.get("competitors", [])
        if len(competitors) < 2:
            return None

        team_a_info = competitors[0]
        team_b_info = competitors[1]

        team_a = team_a_info.get("team", {}).get("displayName", "")
        team_b = team_b_info.get("team", {}).get("displayName", "")

        # Get scores if game is completed
        winner_id = None
        final_score_a = None
        final_score_b = None

        if status == "final":
            score_a = int(team_a_info.get("score", 0))
            score_b = int(team_b_info.get("score", 0))
            final_score_a = score_a
            final_score_b = score_b

            if score_a > score_b:
                winner_team = team_a
            elif score_b > score_a:
                winner_team = team_b
            else:
                winner_team = None

        # Get broadcaster info
        broadcaster = get_broadcaster_from_links(event.get("links", []))

        return {
            "event_id": event_id,
            "team_a": team_a,
            "team_b": team_b,
            "date": date,
            "time": time,
            "winner_team": winner_team if status == "final" else None,
            "final_score_a": final_score_a,
            "final_score_b": final_score_b,
            "broadcaster": broadcaster,
            "status": status,
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse game event: {e}")
        return None


def get_broadcaster_from_links(links: list) -> str:
    """Extract broadcaster info from event links."""
    for link in links:
        text = link.get("text", "").lower()
        if "watch" in text or "live" in text:
            # Try to extract broadcaster name
            for broadcaster in [
                "ESPN",
                "NBC",
                "Prime Video",
                "CBS",
                "Paramount+",
                "ION",
                "USA",
                "League Pass",
            ]:
                if broadcaster.lower() in text:
                    return broadcaster
    return ""
