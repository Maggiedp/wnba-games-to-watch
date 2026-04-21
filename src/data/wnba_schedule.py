"""Fetch WNBA schedule and broadcaster information."""

import requests
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Broadcaster mapping for common variations
BROADCASTER_MAP = {
    "espn": "ESPN",
    "abc": "ESPN",
    "nbc": "NBC",
    "peacock": "NBC",
    "prime": "Prime Video",
    "amazon": "Prime Video",
    "cbs": "CBS",
    "paramount": "Paramount+",
    "ion": "ION",
    "usa": "USA Network",
    "league pass": "League Pass",
    "nba tv": "NBA TV",
}


def fetch_wnba_schedule_broadcasters(date: str) -> dict[tuple[str, str], str]:
    """Fetch broadcaster info from WNBA.com for games on a specific date.

    Args:
        date: Date in YYYY-MM-DD format

    Returns:
        Dict mapping (team_a, team_b) tuples to broadcaster names
    """
    url = "https://www.wnba.com/schedule/"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        broadcasters = {}

        # Find all game cards on the schedule page
        game_cards = soup.find_all("div", class_="GameCard")

        for card in game_cards:
            try:
                # Extract teams
                teams = card.find_all("span", class_="TeamName")
                if len(teams) < 2:
                    continue

                team_a = teams[0].get_text(strip=True)
                team_b = teams[1].get_text(strip=True)

                # Extract broadcaster
                broadcaster_elem = card.find("span", class_="Broadcaster")
                if broadcaster_elem:
                    broadcaster_text = broadcaster_elem.get_text(strip=True).lower()
                    broadcaster = normalize_broadcaster(broadcaster_text)
                    if broadcaster:
                        broadcasters[(team_a, team_b)] = broadcaster
                        logger.info(f"{team_a} vs {team_b}: {broadcaster}")

            except (AttributeError, IndexError) as e:
                logger.warning(f"Failed to parse game card: {e}")
                continue

        return broadcasters

    except requests.RequestException as e:
        logger.error(f"Failed to fetch WNBA schedule: {e}")
        return {}


def normalize_broadcaster(broadcaster_str: str) -> str:
    """Normalize broadcaster name from WNBA.com."""
    broadcaster_lower = broadcaster_str.lower()

    for key, value in BROADCASTER_MAP.items():
        if key in broadcaster_lower:
            return value

    return broadcaster_str


def enhance_games_with_broadcasters(
    games: list[dict], override_broadcasters: dict
) -> list[dict]:
    """Enhance game data with broadcaster info from WNBA.com.

    Args:
        games: List of game dicts from ESPN API
        override_broadcasters: Dict of broadcaster overrides from WNBA.com

    Returns:
        Enhanced games list with broadcaster field filled in
    """
    enhanced = []

    for game in games:
        team_a = game.get("team_a", "")
        team_b = game.get("team_b", "")

        # Try to find broadcaster from WNBA.com data
        if (team_a, team_b) in override_broadcasters:
            game["broadcaster"] = override_broadcasters[(team_a, team_b)]
        elif (team_b, team_a) in override_broadcasters:
            game["broadcaster"] = override_broadcasters[(team_b, team_a)]

        enhanced.append(game)

    return enhanced
