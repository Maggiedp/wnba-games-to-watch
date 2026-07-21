"""Tests for the /replay live-strip predicate + endpoint (Plan 3d)."""

import pytest

import src.api.app as app
from src.api.routes import is_live_status
from src.data.espn_api import ESPNAPIError, today_et


@pytest.fixture(autouse=True)
def _clear_replay_live_cache():
    # The /api/replay-live response cache would otherwise leak between tests
    # (TTL 15s >> test runtime), so reset it around every test.
    app._replay_live_cache = None
    yield
    app._replay_live_cache = None


def test_is_live_status_true_for_the_three_live_states():
    assert is_live_status("STATUS_IN_PROGRESS")
    assert is_live_status("STATUS_HALFTIME")
    assert is_live_status("STATUS_END_PERIOD")


def test_is_live_status_false_otherwise():
    assert not is_live_status("STATUS_SCHEDULED")
    assert not is_live_status("STATUS_FINAL")
    assert not is_live_status("STATUS_POSTPONED")
    assert not is_live_status(None)
    assert not is_live_status("")


def _play(period, clock, home_pct):
    return {"seq": 0, "period": period, "clock": clock, "home_pct": home_pct}


_LIVE_PLAYS = [
    _play(1, "10:00", 0.50),
    _play(2, "5:00", 0.70),
    _play(3, "5:00", 0.35),
    _play(4, "0:00", 0.55),
]


def _patch(monkeypatch, *, statuses_today, known, abbrs, wp):
    monkeypatch.setattr(
        app,
        "fetch_today_game_statuses",
        lambda d: statuses_today if d == today_et() else {},
    )
    monkeypatch.setattr(app, "_get_known_espn_ids", lambda: frozenset(known))
    monkeypatch.setattr(app, "get_team_abbrev_map", lambda s: abbrs)
    monkeypatch.setattr(app, "_fetch_live_wp_cached", wp)


def test_replay_live_returns_only_live_games_with_shape(client, monkeypatch):
    _patch(
        monkeypatch,
        statuses_today={
            "111": "STATUS_IN_PROGRESS",
            "222": "STATUS_FINAL",
            "333": "STATUS_SCHEDULED",
        },
        known={"111", "222", "333"},
        abbrs={"Home Team": "HOM", "Away Team": "AWY"},
        wp=lambda eid, timeout=None: {
            "espn_id": eid,
            "status": "STATUS_IN_PROGRESS",
            "home_team": "Home Team",
            "away_team": "Away Team",
            "home_score": "54",
            "away_score": "61",
            "plays": _LIVE_PLAYS,
        },
    )
    data = client.get("/api/replay-live").json()
    assert data["has_pending"] is True  # a scheduled game is still pending
    assert [g["espn_id"] for g in data["games"]] == ["111"]  # only the live one
    g = data["games"][0]
    assert (g["home_abbr"], g["away_abbr"]) == ("HOM", "AWY")
    assert g["live"] is True
    assert "comeback" not in g and "winner" not in g
    assert isinstance(g["tension"], float) and isinstance(g["lead_changes"], int)
    assert len(g["curve"]) >= 2


def test_replay_live_empty_slate(client, monkeypatch):
    _patch(
        monkeypatch,
        statuses_today={},
        known=set(),
        abbrs={},
        wp=lambda eid, timeout=None: {},
    )
    assert client.get("/api/replay-live").json() == {"games": [], "has_pending": False}


def test_replay_live_skips_games_with_insufficient_plays(client, monkeypatch):
    _patch(
        monkeypatch,
        statuses_today={"111": "STATUS_IN_PROGRESS"},
        known={"111"},
        abbrs={},
        wp=lambda eid, timeout=None: {
            "home_team": "H",
            "away_team": "A",
            "home_score": "1",
            "away_score": "0",
            "plays": [_play(1, "10:00", 0.5)],  # only 1 play -> shape is None
        },
    )
    data = client.get("/api/replay-live").json()
    assert data["games"] == []
    assert data["has_pending"] is True  # the live game still gates polling


def test_replay_live_falls_back_to_full_name_when_abbr_missing(client, monkeypatch):
    _patch(
        monkeypatch,
        statuses_today={"111": "STATUS_IN_PROGRESS"},
        known={"111"},
        abbrs={},  # empty map -> .get(name, name) fallback
        wp=lambda eid, timeout=None: {
            "home_team": "Golden State Valkyries",
            "away_team": "Las Vegas Aces",
            "home_score": "40",
            "away_score": "38",
            "plays": _LIVE_PLAYS,
        },
    )
    g = client.get("/api/replay-live").json()["games"][0]
    assert g["home_abbr"] == "Golden State Valkyries"
    assert g["away_abbr"] == "Las Vegas Aces"


def test_replay_live_502_when_today_statuses_fail(client, monkeypatch):
    def boom(d):
        raise ESPNAPIError("scoreboard down")

    monkeypatch.setattr(app, "fetch_today_game_statuses", boom)
    assert client.get("/api/replay-live").status_code == 502


def test_replay_live_unknown_live_id_not_rendered_or_pending(client, monkeypatch):
    # A live scoreboard id absent from the DB allowlist: no card AND has_pending
    # false, so the client stops polling instead of spinning forever on a card
    # that can never render. Polling is gated on the same known set we render.
    _patch(
        monkeypatch,
        statuses_today={"999": "STATUS_IN_PROGRESS"},  # live but not in the DB
        known=set(),
        abbrs={},
        wp=lambda eid, timeout=None: {},  # never called — id is gated out
    )
    data = client.get("/api/replay-live").json()
    assert data["games"] == []
    assert data["has_pending"] is False


def test_replay_live_response_is_cached_within_ttl(client, monkeypatch):
    # A second call within the TTL is served from the response cache without
    # re-hitting ESPN, so concurrent viewers share one slate fetch (finding 1).
    calls = {"n": 0}

    def counting_statuses(d):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(app, "fetch_today_game_statuses", counting_statuses)
    monkeypatch.setattr(app, "_get_known_espn_ids", lambda: frozenset())
    client.get("/api/replay-live")
    after_first = calls["n"]
    assert after_first > 0  # first call computed (hit ESPN)
    client.get("/api/replay-live")
    assert calls["n"] == after_first  # second call served from cache


def test_replay_live_single_flights_concurrent_cold_builds(client, monkeypatch):
    # Concurrent cold requests must collapse into ONE slate build (single-flight),
    # so viewers share one ESPN fetch instead of each fanning out. Without the
    # build lock, all five threads would run _build_replay_live and count > 1.
    import threading
    import time as _time

    builds = {"n": 0}
    counter_lock = threading.Lock()

    def slow_statuses(d):
        if d == today_et():  # count once per build; hold it so threads overlap
            with counter_lock:
                builds["n"] += 1
            _time.sleep(0.1)
        return {}

    monkeypatch.setattr(app, "fetch_today_game_statuses", slow_statuses)
    monkeypatch.setattr(app, "_get_known_espn_ids", lambda: frozenset())

    threads = [threading.Thread(target=app.get_replay_live) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert builds["n"] == 1  # single-flight collapsed 5 cold calls into one build


def test_detect_live_shapes_raises_502_on_today_failure(monkeypatch):
    from fastapi import HTTPException

    def boom(_date):
        raise ESPNAPIError("down")

    monkeypatch.setattr(app, "fetch_today_game_statuses", boom)
    with pytest.raises(HTTPException) as exc:
        app._detect_live_shapes()
    assert exc.value.status_code == 502
