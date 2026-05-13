"""Tests for the /api/live-wp endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.app import app

client = TestClient(app)


_DEFAULT_PLAYS = [
    {"seq": 0, "period": 1, "clock": "10:00", "home_pct": 0.5},
    {"seq": 1, "period": 2, "clock": "8:30", "home_pct": 0.6},
]


def _make_wp_result(status="STATUS_FINAL", plays=None):
    return {
        "espn_id": "401856901",
        "status": status,
        "home_team": "Dallas Wings",
        "away_team": "Atlanta Dream",
        "plays": _DEFAULT_PLAYS if plays is None else plays,
    }


def test_live_wp_returns_200_with_plays():
    with patch(
        "src.api.app.fetch_live_win_probability", return_value=_make_wp_result()
    ):
        resp = client.get("/api/live-wp?espn_id=401856901")
    assert resp.status_code == 200
    data = resp.json()
    assert data["espn_id"] == "401856901"
    assert data["status"] == "STATUS_FINAL"
    assert len(data["plays"]) == 2


def test_live_wp_returns_200_with_empty_plays():
    with patch(
        "src.api.app.fetch_live_win_probability",
        return_value=_make_wp_result(plays=[]),
    ):
        resp = client.get("/api/live-wp?espn_id=401856901")
    assert resp.status_code == 200
    assert resp.json()["plays"] == []


def test_live_wp_returns_502_on_espn_error():
    from src.data.espn_api import ESPNAPIError

    with patch(
        "src.api.app.fetch_live_win_probability",
        side_effect=ESPNAPIError("timeout"),
    ):
        resp = client.get("/api/live-wp?espn_id=401856901")
    assert resp.status_code == 502


def test_live_wp_requires_espn_id_param():
    resp = client.get("/api/live-wp")
    assert resp.status_code == 422


def test_live_wp_endpoint_returns_502_detail():
    from src.data.espn_api import ESPNAPIError

    with patch(
        "src.api.app.fetch_live_win_probability",
        side_effect=ESPNAPIError("connection refused"),
    ):
        resp = client.get("/api/live-wp?espn_id=401856901")
    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]


def test_today_games_calls_fetch_today_game_statuses():
    """The /api/games/today route calls fetch_today_game_statuses and passes result to format_games_response."""
    with patch(
        "src.api.app.fetch_today_game_statuses", return_value={}
    ) as mock_statuses:
        resp = client.get("/api/games/today")
    assert resp.status_code == 200
    mock_statuses.assert_called_once()
