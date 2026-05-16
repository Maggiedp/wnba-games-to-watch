"""Tests for src/db/queries.py — focused on upsert correctness."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.queries import (
    get_playoff_probabilities,
    upsert_daily_ranking,
    upsert_game,
    upsert_playoff_probability,
    upsert_team,
)
from src.db.schema import Base, DailyRanking, PlayoffProbability


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def team_ids(session):
    a = upsert_team(
        session, name="Team A", abbreviation="TMA", logo_url="", bpi_rating=0.0
    )
    b = upsert_team(
        session, name="Team B", abbreviation="TMB", logo_url="", bpi_rating=0.0
    )
    return a.id, b.id


def test_upsert_game_updates_time_when_backfilled(session, team_ids):
    """Re-upserting an existing game with a newly-known time updates the stored time.

    ESPN inserts games into our DB weeks before tip-off, often with an empty time
    (`T00:00Z` placeholder). Once ESPN backfills the real time, the next daily
    update must propagate that into our DB — otherwise our site shows TBD forever.
    """
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="",
        broadcaster="",
    )
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="",
    )

    from src.db.schema import Game

    game = session.query(Game).filter_by(date="2026-06-01").one()
    assert game.time == "7:00 PM ET"


def test_upsert_game_preserves_time_when_incoming_empty(session, team_ids):
    """An incoming empty time must not clobber a previously-known real time.

    ESPN occasionally returns a placeholder `T00:00Z` for a game that previously
    had a real time. We treat that as "no new info" — keep what we have rather
    than blanking the field.
    """
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="",
    )
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="",
        broadcaster="",
    )

    from src.db.schema import Game

    game = session.query(Game).filter_by(date="2026-06-01").one()
    assert game.time == "7:00 PM ET"


def test_upsert_playoff_probability_inserts(session, team_ids):
    a_id, _ = team_ids
    upsert_playoff_probability(
        session, date="2026-06-01", team_id=a_id, probability=0.72
    )
    record = (
        session.query(PlayoffProbability)
        .filter_by(date="2026-06-01", team_id=a_id)
        .one()
    )
    assert abs(record.probability - 0.72) < 1e-6


def test_upsert_playoff_probability_updates(session, team_ids):
    a_id, _ = team_ids
    upsert_playoff_probability(
        session, date="2026-06-01", team_id=a_id, probability=0.72
    )
    upsert_playoff_probability(
        session, date="2026-06-01", team_id=a_id, probability=0.85
    )
    records = (
        session.query(PlayoffProbability)
        .filter_by(date="2026-06-01", team_id=a_id)
        .all()
    )
    assert len(records) == 1
    assert abs(records[0].probability - 0.85) < 1e-6


def test_get_playoff_probabilities_returns_dict(session, team_ids):
    a_id, b_id = team_ids
    upsert_playoff_probability(
        session, date="2026-06-01", team_id=a_id, probability=0.72
    )
    upsert_playoff_probability(
        session, date="2026-06-01", team_id=b_id, probability=0.33
    )
    result = get_playoff_probabilities(session, "2026-06-01")
    assert result[a_id] == pytest.approx(0.72)
    assert result[b_id] == pytest.approx(0.33)


def test_get_playoff_probabilities_empty_for_missing_date(session, team_ids):
    result = get_playoff_probabilities(session, "2099-01-01")
    assert result == {}


def test_upsert_daily_ranking_stores_win_prob(session, team_ids):
    """win_prob_a is stored and retrieved when provided."""
    a_id, b_id = team_ids
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.4,
        overall_score=46.0,
        broadcaster="ESPN",
        win_prob_a=0.62,
    )
    fetched = session.query(DailyRanking).filter_by(id=ranking.id).one()
    assert fetched.win_prob_a == pytest.approx(0.62)


def test_upsert_daily_ranking_win_prob_defaults_to_none(session, team_ids):
    """win_prob_a is NULL when not provided (backward-compatible default)."""
    a_id, b_id = team_ids
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-02",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=None,
        overall_score=30.0,
        broadcaster="",
    )
    fetched = session.query(DailyRanking).filter_by(id=ranking.id).one()
    assert fetched.win_prob_a is None


def test_upsert_daily_ranking_updates_win_prob(session, team_ids):
    """Re-upserting an existing ranking updates win_prob_a."""
    a_id, b_id = team_ids
    upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.4,
        overall_score=46.0,
        broadcaster="ESPN",
        win_prob_a=0.55,
    )
    upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.4,
        overall_score=46.0,
        broadcaster="ESPN",
        win_prob_a=0.62,
    )
    records = session.query(DailyRanking).filter_by(date="2026-06-01").all()
    assert len(records) == 1
    assert records[0].win_prob_a == pytest.approx(0.62)


def test_upsert_game_stores_espn_id(session, team_ids):
    a_id, b_id = team_ids
    game = upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-15",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    assert game.espn_id == "401856901"


def test_upsert_game_updates_espn_id_on_second_call(session, team_ids):
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-15",
        time="7:00 PM ET",
        broadcaster="ESPN",
    )
    game = upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-15",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    assert game.espn_id == "401856901"


def test_get_game_fields_returns_time_and_espn_id(session, team_ids):
    from src.db.queries import get_game_fields

    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-15",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    result = get_game_fields(session, [("2026-06-15", a_id, b_id)])
    gf = result[("2026-06-15", a_id, b_id)]
    assert gf.time == "7:00 PM ET"
    assert gf.espn_id == "401856901"


def test_get_game_fields_returns_none_espn_id_for_missing(session, team_ids):
    from src.db.queries import get_game_fields

    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-15",
        time="7:00 PM ET",
        broadcaster="ESPN",
    )
    result = get_game_fields(session, [("2026-06-15", a_id, b_id)])
    gf = result[("2026-06-15", a_id, b_id)]
    assert gf.time == "7:00 PM ET"
    assert gf.espn_id is None


def test_upsert_game_writes_excitement_index(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-06-01",
            time="7:00 PM ET",
            broadcaster="ION",
            final_score_a=90,
            final_score_b=85,
            winner_id=1,
            excitement_index=5.5,
        )
        g = session.query(Game).filter(Game.date == "2026-06-01").first()
        assert g.excitement_index == 5.5

        # Upserting again without excitement_index does NOT clobber the existing value.
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-06-01",
            time="7:00 PM ET",
            broadcaster="ION",
        )
        g = session.query(Game).filter(Game.date == "2026-06-01").first()
        assert g.excitement_index == 5.5
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_get_completed_games_missing_excitement(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_completed_games_missing_excitement
        from src.db.schema import Game

        # Completed, no excitement → returned.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="",
                broadcaster="",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="111",
            )
        )
        # Completed, has excitement → not returned.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-21",
                time="",
                broadcaster="",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="222",
                excitement_index=4.2,
            )
        )
        # Not completed (no final score) → not returned.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-22",
                time="",
                broadcaster="",
                espn_id="333",
            )
        )
        # Completed but no espn_id → not returned (can't fetch PBP).
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-23",
                time="",
                broadcaster="",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
            )
        )
        # Different year → not returned.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2025-09-15",
                time="",
                broadcaster="",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="444",
            )
        )
        session.commit()

        games = get_completed_games_missing_excitement(session, season_year=2026)
        espn_ids = {g.espn_id for g in games}
        assert espn_ids == {"111"}
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_game_has_excitement_index_column(tmp_path, monkeypatch):
    """init_db creates Game with excitement_index column (NULL-able FLOAT)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    # Force fresh engine
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        # Insert a game with explicit excitement_index, then read it back.
        g = Game(
            team_a_id=1,
            team_b_id=2,
            date="2026-06-01",
            time="7:00 PM ET",
            broadcaster="ION",
            final_score_a=85,
            final_score_b=80,
            excitement_index=5.4,
        )
        session.add(g)
        session.commit()
        fetched = session.query(Game).filter(Game.date == "2026-06-01").first()
        assert fetched.excitement_index == 5.4
        # New rows default to NULL.
        g2 = Game(team_a_id=1, team_b_id=2, date="2026-06-02", time="", broadcaster="")
        session.add(g2)
        session.commit()
        assert (
            session.query(Game)
            .filter(Game.date == "2026-06-02")
            .first()
            .excitement_index
            is None
        )
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_get_game_fields_returns_final_scores_and_excitement(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_game_fields
        from src.db.schema import Game

        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-06-01",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=88,
                final_score_b=82,
                espn_id="42",
                excitement_index=5.5,
            )
        )
        session.commit()
        fields = get_game_fields(session, [("2026-06-01", 1, 2)])
        gf = fields[("2026-06-01", 1, 2)]
        assert gf.time == "7:00 PM ET"
        assert gf.espn_id == "42"
        assert gf.final_score_a == 88
        assert gf.final_score_b == 82
        assert gf.excitement_index == 5.5
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_get_completed_rankings_sorts_by_excitement(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_completed_rankings, upsert_daily_ranking
        from src.db.schema import Game

        # Three completed 2026 games with different excitement values.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=75,
                espn_id="A",
                excitement_index=2.0,
            )
        )
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-21",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=78,
                espn_id="B",
                excitement_index=6.5,
            )
        )
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-22",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=79,
                espn_id="C",
                excitement_index=4.2,
            )
        )
        # One missing-excitement game — must be INCLUDED, sorted last.
        # An ESPN outage shouldn't silently delete a completed game from
        # the archive; NULL is the retry signal, not the exclude signal.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-23",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="D",
            )
        )
        session.commit()
        # Matching DailyRanking rows.
        for date in ("2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23"):
            upsert_daily_ranking(
                session,
                date=date,
                team_a_id=1,
                team_b_id=2,
                quality_score=50.0,
                importance_score=None,
                overall_score=50.0,
                broadcaster="ION",
            )
        rankings = get_completed_rankings(session, season_year=2026)
        # Excitement desc with NULLs last (then date desc inside each bucket).
        dates_in_order = [r.date for r in rankings]
        assert dates_in_order == [
            "2026-05-21",  # 6.5
            "2026-05-22",  # 4.2
            "2026-05-20",  # 2.0
            "2026-05-23",  # NULL — surfaces last, not omitted
        ]
        # The NULL-excitement row appears with excitement_index unset, ready
        # for the next retry. The other scored fields come from DailyRanking.
        null_row = next(r for r in rankings if r.date == "2026-05-23")
        # Mirror what the API would see: real ranking values present, NULL
        # excitement is on the joined Game row, not the ranking itself.
        assert null_row.quality_score == 50.0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_populate_excitement_leaves_null_on_empty_plays(tmp_path, monkeypatch):
    """An ESPN response with an empty/insufficient plays array must NOT
    persist as 0.0 — it must stay NULL so the next run retries. Otherwise
    a transient empty payload becomes a permanent fake-blowout score."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="EMPTY",
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        monkeypatch.setattr(
            daily_update,
            "fetch_live_win_probability",
            lambda espn_id: {"plays": []},
        )
        daily_update.populate_excitement_for_recent_completions(session)

        game = session.query(Game).filter(Game.espn_id == "EMPTY").one()
        assert game.excitement_index is None
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_populate_excitement_leaves_null_on_espn_failure(tmp_path, monkeypatch):
    """ESPN/parse failures must leave excitement_index NULL so the next run
    retries — never persist a sentinel 0.0 (indistinguishable from a true
    blowout score, and excluded from retry by the NULL filter)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.data.espn_api import ESPNAPIError
        from src.db.schema import Game

        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="FAIL",
            )
        )
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-21",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=90,
                final_score_b=80,
                espn_id="OK",
            )
        )
        session.commit()

        def fake_fetch(espn_id):
            if espn_id == "FAIL":
                raise ESPNAPIError("simulated outage")
            return {
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "0:00", "home_pct": 0.7},
                ]
            }

        import scripts.daily_update as daily_update

        monkeypatch.setattr(daily_update, "fetch_live_win_probability", fake_fetch)
        daily_update.populate_excitement_for_recent_completions(session)

        fail_game = session.query(Game).filter(Game.espn_id == "FAIL").one()
        ok_game = session.query(Game).filter(Game.espn_id == "OK").one()
        assert fail_game.excitement_index is None  # retryable next run
        assert ok_game.excitement_index is not None and ok_game.excitement_index > 0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_get_completed_rankings_includes_games_without_daily_ranking(
    tmp_path, monkeypatch
):
    """Completed games without a matching DailyRanking row still appear,
    with None scores. Guards against silent omission when daily_update
    misses a day."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_completed_rankings, upsert_daily_ranking
        from src.db.schema import Game

        # Game WITH ranking.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=75,
                espn_id="X",
                excitement_index=3.0,
            )
        )
        upsert_daily_ranking(
            session,
            date="2026-05-20",
            team_a_id=1,
            team_b_id=2,
            quality_score=50.0,
            importance_score=None,
            overall_score=50.0,
            broadcaster="ION",
        )
        # Game WITHOUT ranking (simulates a missed daily-update day).
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-21",
                time="",
                broadcaster="ESPN",
                winner_id=1,
                final_score_a=90,
                final_score_b=85,
                espn_id="Y",
                excitement_index=7.0,
            )
        )
        session.commit()

        rankings = get_completed_rankings(session, season_year=2026)
        # Both games appear, sorted by excitement desc.
        dates_in_order = [r.date for r in rankings]
        assert dates_in_order == ["2026-05-21", "2026-05-20"]
        # The synthesized ranking has None scores.
        orphan = next(r for r in rankings if r.date == "2026-05-21")
        assert orphan.quality_score is None
        assert orphan.overall_score is None
        assert orphan.broadcaster == "ESPN"
        # The real ranking has real scores.
        real = next(r for r in rankings if r.date == "2026-05-20")
        assert real.quality_score == 50.0
        assert real.overall_score == 50.0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None
