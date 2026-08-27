"""Tests for the importance/overall backfill (backfill_importance).

Uses the shared `env` fixture. `rebuild_date` takes its ESPN-fetched
game lists as explicit params (rather than fetching internally), so a
unit test can hand it a small fixed schedule with no network/monkeypatch
needed for the core recompute logic; `main()`'s own fetch + wiring is
covered separately by monkeypatching `fetch_games_for_range`.
"""

from scripts.backfill_importance import rebuild_date
from src.db.queries import upsert_daily_ranking, upsert_game, upsert_team


def _two_teams(session):
    a = upsert_team(
        session, name="Las Vegas Aces", abbreviation="LV", logo_url="", bpi_rating=0.0
    )
    b = upsert_team(
        session,
        name="New York Liberty",
        abbreviation="NY",
        logo_url="",
        bpi_rating=0.0,
    )
    return a.id, b.id


def test_rebuild_date_rewrites_importance_and_overall_consistently(env):
    """A regular-season row's importance_score moves off the stale value,
    and overall_score stays derived from the CURRENT quality + importance —
    the two must never be rewritten out of step."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-08-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        season_type=2,
        espn_id="G1",
    )
    stale_importance = 987.0  # obviously off the current 0-100 scale
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=stale_importance,
        overall_score=60.0 * 0.6 + stale_importance * 0.4,
        broadcaster="ESPN",
    )
    session.commit()
    session.close()

    session = env.get_session()
    regular_season_games = [
        {
            "team_a": "Las Vegas Aces",
            "team_b": "New York Liberty",
            "date": "2026-08-01",
            "event_id": "G1",
            "season_type": 2,
        }
    ]
    changed = rebuild_date(session, "2026-08-01", [], regular_season_games)
    assert changed == 1

    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-08-01", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score != stale_importance
    assert row.importance_score is not None
    assert 0.0 <= row.importance_score <= 100.0
    assert row.overall_score == row.quality_score * 0.6 + row.importance_score * 0.4
    session.close()


def test_rebuild_date_leaves_preseason_and_postseason_untouched(env):
    """Only the regular-season bubble-swing metric changed scale — a
    preseason or postseason row's importance/overall must be left exactly
    as stored, since compute_postseason_swing_from_matrix never consumed
    fate_levels and preseason is pinned at 0 either way."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-04-15",
        time="",
        broadcaster="",
        season_type=1,
        espn_id="PRE1",
    )
    upsert_daily_ranking(
        session,
        date="2026-04-15",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=55.0,
        importance_score=0.0,
        overall_score=55.0 * 0.6,
        broadcaster="",
    )
    session.commit()
    session.close()

    session = env.get_session()
    changed = rebuild_date(session, "2026-04-15", [], [])
    assert changed == 0

    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-04-15", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score == 0.0
    assert row.overall_score == 55.0 * 0.6
    session.close()


def test_rebuild_date_no_rankings_is_a_noop(env):
    """A date with no stored rankings returns 0 without error (main() will
    only ever call this for dates the DB says have rows, but the function
    should degrade gracefully on its own)."""
    session = env.get_session()
    assert rebuild_date(session, "2026-08-02", [], []) == 0
    session.close()


def test_main_recompute_fails_closed_when_a_date_errors(env, monkeypatch):
    """One date raising must not silently exit 0 — the operator needs to
    know to re-run (mirrors the other two backfills' fail-closed gate)."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-08-01",
        time="",
        broadcaster="",
        season_type=2,
        espn_id="G1",
    )
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=987.0,
        overall_score=60.0 * 0.6 + 987.0 * 0.4,
        broadcaster="",
    )
    session.commit()
    session.close()

    import scripts.backfill_importance as bf

    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda start, end, failed_windows=None: []
    )

    def fake_rebuild(session, date_str, elo_games, regular_season_games):
        raise RuntimeError("boom")

    monkeypatch.setattr(bf, "rebuild_date", fake_rebuild)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_importance", "--recompute"])

    assert bf.main() == 1

    # Stale value must survive the failed run untouched.
    session = env.get_session()
    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-08-01", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score == 987.0
    session.close()


def test_main_dry_run_does_not_call_rebuild_date(env, monkeypatch):
    """Without --recompute, main() only lists dates; it must not write."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=987.0,
        overall_score=60.0 * 0.6 + 987.0 * 0.4,
        broadcaster="",
    )
    session.commit()
    session.close()

    import scripts.backfill_importance as bf

    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda start, end, failed_windows=None: []
    )
    calls = {"n": 0}

    def fake_rebuild(*args, **kwargs):
        calls["n"] += 1
        return 0

    monkeypatch.setattr(bf, "rebuild_date", fake_rebuild)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_importance"])

    assert bf.main() == 0
    assert calls["n"] == 0
