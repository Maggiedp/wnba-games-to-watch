"""Tests for the excitement batch recompute (backfill_excitement --recompute).

Query + refresh + script-level coverage lives together here, mirroring
tests/test_backfill_game_shapes.py. Uses the shared `env` fixture.
"""

from datetime import datetime, timedelta

from src.db.queries import get_games_for_excitement_refresh


def _seed_game(
    env,
    session,
    espn_id,
    excitement_index=None,
    excitement_computed_at=None,
    date="2026-05-10",
):
    """Completed 2026 game row with optional stored excitement state."""
    game = env.Game(
        team_a_id=1,
        team_b_id=2,
        date=date,
        time="",
        broadcaster="ION",
        winner_id=1,
        final_score_a=80,
        final_score_b=70,
        espn_id=espn_id,
        excitement_index=excitement_index,
        excitement_computed_at=excitement_computed_at,
    )
    session.add(game)
    return game


def test_refresh_query_cutoff_none_selects_all_stored_rows(env):
    """cutoff=None must reach every stored excitement value: legacy rows
    (excitement_computed_at NULL — the column shipped 2026-05-16 with no
    backfill) AND rows far older than any daily window. Rows with no
    stored excitement, or no espn_id, stay excluded."""
    session = env.get_session()
    try:
        now = datetime.now()
        _seed_game(env, session, "legacy", excitement_index=5.0)  # computed_at NULL
        _seed_game(
            env,
            session,
            "old",
            excitement_index=6.0,
            excitement_computed_at=now - timedelta(days=30),
        )
        _seed_game(
            env,
            session,
            "recent",
            excitement_index=7.0,
            excitement_computed_at=now - timedelta(days=1),
        )
        _seed_game(env, session, "unscored")  # excitement NULL -> never eligible
        _seed_game(
            env,
            session,
            None,
            excitement_index=4.0,
            excitement_computed_at=now - timedelta(days=1),
        )  # no espn_id -> can never be refreshed
        session.commit()

        got = get_games_for_excitement_refresh(session, cutoff=None)
        assert {g.espn_id for g in got} == {"legacy", "old", "recent"}
    finally:
        session.close()


def test_refresh_query_dated_cutoff_unchanged(env):
    """A dated cutoff keeps the existing bounded-window semantics: only rows
    computed inside the window, and legacy NULL-computed_at rows stay out."""
    session = env.get_session()
    try:
        now = datetime.now()
        _seed_game(env, session, "legacy", excitement_index=5.0)
        _seed_game(
            env,
            session,
            "old",
            excitement_index=6.0,
            excitement_computed_at=now - timedelta(days=30),
        )
        _seed_game(
            env,
            session,
            "recent",
            excitement_index=7.0,
            excitement_computed_at=now - timedelta(days=1),
        )
        _seed_game(
            env,
            session,
            None,
            excitement_index=4.0,
            excitement_computed_at=now - timedelta(days=1),
        )  # no espn_id -> excluded even inside the window
        session.commit()

        got = get_games_for_excitement_refresh(session, cutoff=now - timedelta(days=2))
        assert {g.espn_id for g in got} == {"recent"}
    finally:
        session.close()


GOOD_PLAYS = [
    {"period": 1, "clock": "10:00", "home_pct": 0.5},
    {"period": 4, "clock": "0:00", "home_pct": 0.9},
]


def test_refresh_unbounded_reaches_legacy_and_old_rows(env, monkeypatch):
    """window_days=None re-derives rows the daily window can never touch,
    overwrites on diff, returns [] on full convergence — and leaves
    excitement_computed_at untouched (legacy rows stay NULL, so they don't
    enter the daily 2-day refresh window afterward)."""
    session = env.get_session()
    try:
        now = datetime.now()
        _seed_game(env, session, "legacy", excitement_index=9.9)
        _seed_game(
            env,
            session,
            "old",
            excitement_index=9.9,
            excitement_computed_at=now - timedelta(days=30),
        )
        session.commit()

        import scripts.daily_update as du

        monkeypatch.setattr(
            du,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: {
                "status": "STATUS_FINAL",
                "plays": GOOD_PLAYS,
            },
        )
        failed = du.refresh_recent_excitement_scores(
            session, window_days=None, limit=None
        )
        assert failed == []
        session.expire_all()
        legacy = session.query(env.Game).filter_by(espn_id="legacy").one()
        old = session.query(env.Game).filter_by(espn_id="old").one()
        assert legacy.excitement_index != 9.9
        assert old.excitement_index != 9.9
        assert legacy.excitement_computed_at is None  # immutability held
        assert old.excitement_computed_at == now - timedelta(days=30)
    finally:
        session.close()


def test_refresh_returns_failed_espn_ids_and_keeps_stored_values(env, monkeypatch):
    """Each can't-refresh path — fetch raised, non-FINAL feed, insufficient
    plays — lands the espn_id in the returned list and keeps the stored
    (stale) value."""
    session = env.get_session()
    try:
        for espn_id in ("raises", "notfinal", "thin"):
            _seed_game(env, session, espn_id, excitement_index=9.9)
        session.commit()

        import scripts.daily_update as du

        def fake_wp(espn_id, timeout=10):
            if espn_id == "raises":
                raise RuntimeError("ESPN summary drift")
            if espn_id == "notfinal":
                return {"status": "STATUS_IN_PROGRESS", "plays": GOOD_PLAYS}
            return {"status": "STATUS_FINAL", "plays": GOOD_PLAYS[:1]}  # <2 plays

        monkeypatch.setattr(du, "fetch_live_win_probability", fake_wp)
        failed = du.refresh_recent_excitement_scores(
            session, window_days=None, limit=None
        )
        assert set(failed) == {"raises", "notfinal", "thin"}
        session.expire_all()
        for espn_id in ("raises", "notfinal", "thin"):
            game = session.query(env.Game).filter_by(espn_id=espn_id).one()
            assert game.excitement_index == 9.9  # stale value kept, surfaced
    finally:
        session.close()


def test_refresh_unchanged_score_is_success_not_failure(env, monkeypatch):
    """A recompute that agrees with the stored value is a successful
    recheck: no failure reported, value stable across runs."""
    session = env.get_session()
    try:
        _seed_game(env, session, "stable", excitement_index=9.9)
        session.commit()

        import scripts.daily_update as du

        monkeypatch.setattr(
            du,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: {
                "status": "STATUS_FINAL",
                "plays": GOOD_PLAYS,
            },
        )
        # First run converges the value; second run recomputes identically.
        assert (
            du.refresh_recent_excitement_scores(session, window_days=None, limit=None)
            == []
        )
        session.expire_all()
        converged = (
            session.query(env.Game).filter_by(espn_id="stable").one().excitement_index
        )
        assert (
            du.refresh_recent_excitement_scores(session, window_days=None, limit=None)
            == []
        )
        session.expire_all()
        assert (
            session.query(env.Game).filter_by(espn_id="stable").one().excitement_index
            == converged
        )
    finally:
        session.close()
