import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import src.api.app as app_module
from src.data.espn_api import today_et
from src.db.queries import has_alerted
from src.db.schema import Game


@pytest.fixture(autouse=True)
def _poll_env(monkeypatch):
    """Shared poll setup: a known trigger secret + configured Telegram. Tests that
    need the negative cases (bad secret, unconfigured Telegram) override in-body."""
    monkeypatch.setattr(app_module, "_TRIGGER_SECRET", "s3cret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")


def _game(espn_id, excitement, **over):
    """A live-game dict as _detect_live_shapes yields; override any field."""
    g = {
        "espn_id": espn_id,
        "home_team": "Los Angeles Sparks",
        "away_team": "Las Vegas Aces",
        "home_score": "74",
        "away_score": "78",
        "excitement": excitement,
    }
    g.update(over)
    return g


def _seed_live_game(env, espn_id="401700020"):
    """A game tipped ~1h ago (so the recent-tipoff self-gate passes), keyed on
    today's ET date so get_games_by_date finds it. Only time_utc/espn_id/date
    are read on this path, so team rows aren't needed."""
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    session = env.get_session()
    try:
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date=today_et(),
                time="",
                time_utc=recent,
                espn_id=espn_id,
            )
        )
        session.commit()
    finally:
        session.close()


def test_requires_secret(client):
    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "wrong"})
    assert r.status_code == 403


def test_noop_when_nothing_recent(client, monkeypatch):
    called = {"detect": 0, "send": 0}
    monkeypatch.setattr(
        app_module,
        "_detect_live_shapes",
        lambda **k: called.__setitem__("detect", called["detect"] + 1) or ([], False),
    )
    monkeypatch.setattr(
        app_module,
        "send_telegram",
        lambda *a, **k: called.__setitem__("send", called["send"] + 1) or True,
    )
    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r.status_code == 200
    assert r.json() == {"checked": 0, "alerted": 0}
    assert called == {"detect": 0, "send": 0}


def test_sends_and_dedups_qualifying_game(env, client, monkeypatch):
    _seed_live_game(env, "401700020")
    game = _game("401700020", 8.1)
    monkeypatch.setattr(app_module, "_detect_live_shapes", lambda **k: ([game], True))
    sends = []
    monkeypatch.setattr(
        app_module, "send_telegram", lambda text, **k: sends.append(text) or True
    )

    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r.status_code == 200
    assert r.json()["alerted"] == 1
    assert len(sends) == 1 and "Thriller" in sends[0]
    session = env.get_session()
    try:
        assert has_alerted(session, "401700020") is True
    finally:
        session.close()

    # Second poll: already alerted → no re-send.
    r2 = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r2.json()["alerted"] == 0
    assert len(sends) == 1


def test_no_dedup_row_when_send_fails(env, client, monkeypatch):
    _seed_live_game(env, "401700021")
    game = _game("401700021", 8.0)
    monkeypatch.setattr(app_module, "_detect_live_shapes", lambda **k: ([game], True))
    monkeypatch.setattr(app_module, "send_telegram", lambda *a, **k: False)

    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r.json()["alerted"] == 0
    session = env.get_session()
    try:
        assert has_alerted(session, "401700021") is False  # retries next poll
    finally:
        session.close()


def test_below_close_threshold_no_send(env, client, monkeypatch):
    _seed_live_game(env, "401700022")
    game = _game("401700022", 3.0)
    monkeypatch.setattr(app_module, "_detect_live_shapes", lambda **k: ([game], True))
    sends = []
    monkeypatch.setattr(
        app_module, "send_telegram", lambda t, **k: sends.append(t) or True
    )
    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r.json()["alerted"] == 0 and sends == []


def test_currently_lopsided_game_no_send(env, client, monkeypatch):
    """A game that banked Thriller-level cumulative excitement but is now a
    blowout (current home WP outside [0.15, 0.85]) must NOT alert — the live
    label reflects the current state, not the game's history."""
    _seed_live_game(env, "401700023")
    # Thriller-level excitement, but the current WP (last curve point) is 0.95.
    game = _game("401700023", 8.5, curve=[[600.0, 0.5], [2400.0, 0.95]])
    monkeypatch.setattr(app_module, "_detect_live_shapes", lambda **k: ([game], True))
    sends = []
    monkeypatch.setattr(
        app_module, "send_telegram", lambda t, **k: sends.append(t) or True
    )
    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r.json()["alerted"] == 0 and sends == []


def test_concurrent_polls_send_once(env, client, monkeypatch):
    """Two overlapping polls on the same live game must send exactly once and
    neither must 500 on the unique-constraint race (Finding 1)."""
    _seed_live_game(env, "401700030")
    game = _game("401700030", 8.5)
    monkeypatch.setattr(app_module, "_detect_live_shapes", lambda **k: ([game], True))

    sends = []
    sends_lock = threading.Lock()

    def slow_send(text, **k):
        time.sleep(0.05)  # widen the race window so an unlocked impl double-sends
        with sends_lock:
            sends.append(text)
        return True

    monkeypatch.setattr(app_module, "send_telegram", slow_send)

    results = []

    def run():
        results.append(app_module._run_thriller_poll())

    t1, t2 = threading.Thread(target=run), threading.Thread(target=run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(sends) == 1
    assert sum(r["alerted"] for r in results) == 1
    session = env.get_session()
    try:
        assert has_alerted(session, "401700030") is True
    finally:
        session.close()


def test_poll_502s_when_scoreboard_fails_during_a_game(env, client, monkeypatch):
    """Self-gate passed (a game just tipped), but today's scoreboard fetch fails:
    the poll must 502 (observable/retriable), not fake-quiet 200 (Finding 1)."""
    from src.data.espn_api import ESPNAPIError

    _seed_live_game(env, "401700040")

    def boom(_date):
        raise ESPNAPIError("down")

    monkeypatch.setattr(app_module, "fetch_today_game_statuses", boom)
    sends = []
    monkeypatch.setattr(
        app_module, "send_telegram", lambda t, **k: sends.append(t) or True
    )

    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r.status_code == 502
    assert sends == []  # no alerts sent during an outage


def test_poll_500s_when_telegram_unconfigured(client, monkeypatch):
    """Missing Telegram secrets are a permanent deploy error, not a transient
    send failure: the poll must fail closed (500), not fake-quiet 200 (Finding 2)."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    r = client.post("/internal/thriller-poll", headers={"X-Trigger-Secret": "s3cret"})
    assert r.status_code == 500
