"""Tests for the /api/live-wp endpoint."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.api.app import app

client = TestClient(app)


class _AcceptAllIds:
    """frozenset stand-in that says yes to any membership check."""

    def __contains__(self, _):
        return True


@pytest.fixture(autouse=True)
def _clear_live_wp_cache(monkeypatch):
    """Reset the /api/live-wp TTL cache so each test sees the patched fetcher.

    Also patches the known-id gate open by default — these tests are about
    cache + error behavior, not the gate. Tests that exercise the gate
    override `_get_known_espn_ids` explicitly.
    """
    app_module._live_wp_cache.clear()
    app_module._live_wp_fetch_locks.clear()
    monkeypatch.setattr(app_module, "_get_known_espn_ids", lambda: _AcceptAllIds())
    yield
    app_module._live_wp_cache.clear()
    app_module._live_wp_fetch_locks.clear()


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


def test_live_wp_returns_404_when_espn_event_not_found():
    from src.data.espn_api import ESPNNotFoundError

    with patch(
        "src.api.app.fetch_live_win_probability",
        side_effect=ESPNNotFoundError("ESPN returned 404 for ..."),
    ):
        resp = client.get("/api/live-wp?espn_id=999999")
    assert resp.status_code == 404


def test_today_games_graceful_when_status_fetch_raises():
    """If fetch_today_game_statuses raises, /api/games/today still returns 200."""
    with patch(
        "src.api.app.fetch_today_game_statuses",
        side_effect=Exception("network error"),
    ):
        resp = client.get("/api/games/today")
    assert resp.status_code == 200


def test_live_wp_cache_coalesces_repeat_requests():
    """Repeat requests within the TTL window hit ESPN once."""
    with patch(
        "src.api.app.fetch_live_win_probability", return_value=_make_wp_result()
    ) as mock_fetch:
        for _ in range(5):
            resp = client.get("/api/live-wp?espn_id=401856901")
            assert resp.status_code == 200
    assert mock_fetch.call_count == 1


def test_live_wp_negative_cache_coalesces_espn_errors():
    """Repeated ESPN failures within the negative-TTL window hit ESPN once."""
    from src.data.espn_api import ESPNAPIError

    with patch(
        "src.api.app.fetch_live_win_probability",
        side_effect=ESPNAPIError("timeout"),
    ) as mock_fetch:
        for _ in range(5):
            resp = client.get("/api/live-wp?espn_id=401856901")
            assert resp.status_code == 502
    assert mock_fetch.call_count == 1


def test_live_wp_negative_cache_coalesces_not_found():
    """Repeated 404s within the not-found TTL window hit ESPN once."""
    from src.data.espn_api import ESPNNotFoundError

    with patch(
        "src.api.app.fetch_live_win_probability",
        side_effect=ESPNNotFoundError("404"),
    ) as mock_fetch:
        for _ in range(5):
            resp = client.get("/api/live-wp?espn_id=999999")
            assert resp.status_code == 404
    assert mock_fetch.call_count == 1


def test_live_wp_rejects_non_numeric_espn_id():
    """Non-numeric espn_id is rejected before reaching the cache."""
    resp = client.get("/api/live-wp?espn_id=abc")
    assert resp.status_code == 422
    resp = client.get("/api/live-wp?espn_id=4018569" + "9" * 20)  # too long
    assert resp.status_code == 422


def test_live_wp_cache_evicts_oldest_when_over_cap():
    """Cache and fetch-lock maps stay bounded under many unique ids."""
    n_requests = app_module._LIVE_WP_CACHE_MAX_ENTRIES + 32
    with patch(
        "src.api.app.fetch_live_win_probability", return_value=_make_wp_result()
    ):
        for i in range(n_requests):
            resp = client.get(f"/api/live-wp?espn_id={1000000 + i}")
            assert resp.status_code == 200
    assert len(app_module._live_wp_cache) == app_module._LIVE_WP_CACHE_MAX_ENTRIES
    assert len(app_module._live_wp_fetch_locks) == app_module._LIVE_WP_CACHE_MAX_ENTRIES


def test_live_wp_rejects_unknown_espn_id_without_calling_espn(monkeypatch):
    """The known-id gate blocks arbitrary ids from reaching ESPN."""
    monkeypatch.setattr(app_module, "_get_known_espn_ids", lambda: frozenset())
    with patch("src.api.app.fetch_live_win_probability") as mock_fetch:
        resp = client.get("/api/live-wp?espn_id=401856901")
    assert resp.status_code == 404
    mock_fetch.assert_not_called()


def test_live_status_returns_502_on_espn_failure():
    """An ESPN scoreboard error surfaces as 502 so the frontend can retry."""
    from src.data.espn_api import ESPNAPIError

    with patch(
        "src.api.app.fetch_today_game_statuses",
        side_effect=ESPNAPIError("scoreboard timeout"),
    ):
        resp = client.get("/api/games/live-status")
    assert resp.status_code == 502


def test_live_status_returns_empty_when_no_games_today():
    """Empty slate is a 200 with {} — must stay distinguishable from failure."""
    with patch("src.api.app.fetch_today_game_statuses", return_value={}):
        resp = client.get("/api/games/live-status")
    assert resp.status_code == 200
    assert resp.json() == {}
