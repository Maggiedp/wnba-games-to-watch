"""Tests for ESPN live win probability fetch functions."""

from unittest.mock import patch

import pytest

from src.data.espn_api import (
    ESPNAPIError,
    fetch_live_win_probability,
    fetch_today_game_statuses,
)


def _make_summary(
    status="STATUS_FINAL",
    home="Dallas Wings",
    away="Atlanta Dream",
    plays=None,
    wp=None,
):
    if plays is None:
        plays = [
            {
                "id": "4018569010",
                "period": {"number": 1},
                "clock": {"displayValue": "10:00"},
            },
            {
                "id": "4018569015",
                "period": {"number": 2},
                "clock": {"displayValue": "8:30"},
            },
        ]
    if wp is None:
        wp = [
            {"playId": "4018569010", "homeWinPercentage": 0.5, "tiePercentage": 0.0},
            {"playId": "4018569015", "homeWinPercentage": 0.6, "tiePercentage": 0.0},
        ]
    return {
        "header": {
            "competitions": [
                {
                    "status": {"type": {"name": status}},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": home}},
                        {"homeAway": "away", "team": {"displayName": away}},
                    ],
                }
            ]
        },
        "plays": plays,
        "winprobability": wp,
    }


def test_fetch_live_win_probability_parses_status_and_teams():
    with patch("src.data.espn_api._get", return_value=_make_summary()):
        result = fetch_live_win_probability("401856901")
    assert result["status"] == "STATUS_FINAL"
    assert result["home_team"] == "Dallas Wings"
    assert result["away_team"] == "Atlanta Dream"
    assert result["espn_id"] == "401856901"


def test_fetch_live_win_probability_builds_plays_list():
    with patch("src.data.espn_api._get", return_value=_make_summary()):
        result = fetch_live_win_probability("401856901")
    plays = result["plays"]
    assert len(plays) == 2
    assert plays[0] == {"seq": 0, "period": 1, "clock": "10:00", "home_pct": 0.5}
    assert plays[1] == {"seq": 1, "period": 2, "clock": "8:30", "home_pct": 0.6}


def test_fetch_live_win_probability_empty_wp_returns_empty_plays():
    summary = _make_summary(wp=[])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert result["plays"] == []


def test_fetch_live_win_probability_propagates_espn_error():
    with patch("src.data.espn_api._get", side_effect=ESPNAPIError("timeout")):
        with pytest.raises(ESPNAPIError):
            fetch_live_win_probability("401856901")


def test_fetch_live_win_probability_falls_back_when_play_id_missing():
    summary = _make_summary(
        plays=[
            {
                "id": "4018569010",
                "period": {"number": 1},
                "clock": {"displayValue": "10:00"},
            }
        ],
        wp=[{"playId": "UNKNOWN", "homeWinPercentage": 0.4, "tiePercentage": 0.0}],
    )
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert result["plays"][0]["period"] == 1
    assert result["plays"][0]["clock"] == ""
    assert result["plays"][0]["home_pct"] == 0.4


def _make_scoreboard(*games):
    """games: list of (event_id, status_name) tuples."""
    return {
        "events": [
            {
                "id": eid,
                "competitions": [{"status": {"type": {"name": status}}}],
            }
            for eid, status in games
        ]
    }


def test_fetch_today_game_statuses_returns_status_by_espn_id():
    sb = _make_scoreboard(
        ("401856901", "STATUS_FINAL"),
        ("401856902", "STATUS_IN_PROGRESS"),
        ("401856903", "STATUS_SCHEDULED"),
    )
    with patch("src.data.espn_api._get", return_value=sb):
        result = fetch_today_game_statuses("2026-05-13")
    assert result == {
        "401856901": "STATUS_FINAL",
        "401856902": "STATUS_IN_PROGRESS",
        "401856903": "STATUS_SCHEDULED",
    }


def test_fetch_today_game_statuses_returns_empty_on_error():
    with patch("src.data.espn_api._get", side_effect=ESPNAPIError("down")):
        result = fetch_today_game_statuses("2026-05-13")
    assert result == {}


def test_fetch_today_game_statuses_passes_correct_date_param():
    with patch("src.data.espn_api._get", return_value={"events": []}) as mock_get:
        fetch_today_game_statuses("2026-05-13")
    mock_get.assert_called_once_with(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
        dates="20260513",
    )
