"""Tests for src/db/queries.py — focused on upsert correctness."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.queries import (
    get_completed_postseason_games,
    get_playoff_probabilities,
    upsert_daily_ranking,
    upsert_game,
    upsert_playoff_probability,
    upsert_team,
)
from src.db.schema import Base, DailyRanking, Game, PlayoffProbability


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

    game = session.query(Game).filter_by(date="2026-06-01").one()
    assert game.time == "7:00 PM ET"


def test_upsert_playoff_probability_inserts_with_all_columns(session, team_ids):
    a_id, _ = team_ids
    upsert_playoff_probability(
        session,
        date="2026-06-01",
        team_id=a_id,
        probability=0.72,
        reach_semis_prob=0.55,
        reach_finals_prob=0.30,
        win_championship_prob=0.15,
    )
    record = (
        session.query(PlayoffProbability)
        .filter_by(date="2026-06-01", team_id=a_id)
        .one()
    )
    assert abs(record.probability - 0.72) < 1e-6
    assert abs(record.reach_semis_prob - 0.55) < 1e-6
    assert abs(record.reach_finals_prob - 0.30) < 1e-6
    assert abs(record.win_championship_prob - 0.15) < 1e-6


def test_upsert_playoff_probability_inserts_legacy_signature(session, team_ids):
    """Backwards-compat: positional/old-keyword call still inserts a row."""
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
    assert record.reach_semis_prob is None
    assert record.win_championship_prob is None


def test_upsert_playoff_probability_updates_all_columns(session, team_ids):
    a_id, _ = team_ids
    upsert_playoff_probability(
        session,
        date="2026-06-01",
        team_id=a_id,
        probability=0.72,
        reach_semis_prob=0.55,
        reach_finals_prob=0.30,
        win_championship_prob=0.15,
    )
    upsert_playoff_probability(
        session,
        date="2026-06-01",
        team_id=a_id,
        probability=0.85,
        reach_semis_prob=0.60,
        reach_finals_prob=0.32,
        win_championship_prob=0.18,
    )
    records = (
        session.query(PlayoffProbability)
        .filter_by(date="2026-06-01", team_id=a_id)
        .all()
    )
    assert len(records) == 1
    assert abs(records[0].probability - 0.85) < 1e-6
    assert abs(records[0].reach_semis_prob - 0.60) < 1e-6
    assert abs(records[0].reach_finals_prob - 0.32) < 1e-6
    assert abs(records[0].win_championship_prob - 0.18) < 1e-6


def test_get_playoff_probabilities_returns_records(session, team_ids):
    a_id, b_id = team_ids
    upsert_playoff_probability(
        session,
        date="2026-06-01",
        team_id=a_id,
        probability=0.72,
        reach_semis_prob=0.55,
        reach_finals_prob=0.30,
        win_championship_prob=0.15,
    )
    upsert_playoff_probability(
        session, date="2026-06-01", team_id=b_id, probability=0.33
    )
    result = get_playoff_probabilities(session, "2026-06-01")
    assert result[a_id].make_playoffs_prob == pytest.approx(0.72)
    assert result[a_id].reach_semis_prob == pytest.approx(0.55)
    assert result[a_id].reach_finals_prob == pytest.approx(0.30)
    assert result[a_id].win_championship_prob == pytest.approx(0.15)
    # Legacy row (no round columns): NULLs become None.
    assert result[b_id].make_playoffs_prob == pytest.approx(0.33)
    assert result[b_id].reach_semis_prob is None
    assert result[b_id].win_championship_prob is None


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


def test_init_db_dedupe_un_finalize_reschedule_keeps_newer_state(tmp_path, monkeypatch):
    """The realistic duplicate scenario: ESPN un-finalized + rescheduled
    a game. The older row reflects the original (now-stale) final at the
    original date; the newer row reflects the corrected schedule with
    no completion data (game hasn't been replayed yet). The dedupe must
    keep the newer row's schedule identity AND cleared completion —
    keeping the older row would leave the archive sorting a non-game on
    the wrong date as if it were a real result."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from sqlalchemy import text

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    engine = schema.get_engine()
    with engine.connect() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_game_espn_id"))
        # Row 101 — older, stale completed result at the original date.
        now = datetime.now().isoformat()
        conn.execute(
            text(
                "INSERT INTO games (id, team_a_id, team_b_id, date, time, "
                "broadcaster, espn_id, winner_id, final_score_a, "
                "final_score_b, excitement_index, excitement_computed_at) "
                "VALUES (101, 1, 2, '2026-05-20', '7:00 PM ET', 'ION', "
                "'evt', 1, 88, 82, 5.4, :now)"
            ),
            {"now": now},
        )
        # Row 102 — newer, the corrected reschedule. Empty because the
        # game hasn't been replayed at the new date yet.
        conn.execute(
            text(
                "INSERT INTO games (id, team_a_id, team_b_id, date, time, "
                "broadcaster, espn_id) VALUES "
                "(102, 1, 2, '2026-05-25', '8:00 PM ET', 'ION', 'evt')"
            )
        )
        conn.commit()

    schema._engine = None
    schema._session_factory = None
    schema.init_db()

    session = schema.get_session()
    try:
        from src.db.schema import Game

        rows = session.query(Game).filter(Game.espn_id == "evt").all()
        assert len(rows) == 1
        g = rows[0]
        # Survivor is row 102 (MAX(id), corrected schedule identity).
        assert g.id == 102
        assert g.date == "2026-05-25"
        # Stale completion is dropped — survivor was non-final.
        assert g.winner_id is None
        assert g.final_score_a is None
        assert g.final_score_b is None
        assert g.excitement_index is None
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_init_db_dedupe_preserves_excitement_when_survivor_is_final(
    tmp_path, monkeypatch
):
    """When duplicates exist and the SURVIVOR (max id) is already final,
    nullable completion-cache fields (excitement_index, computed_at,
    last_attempt_at) get merged in from older rows where the survivor
    has NULL. This avoids losing an excitement_index that was computed
    against the older row before the duplicate was created."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from sqlalchemy import text

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    engine = schema.get_engine()
    with engine.connect() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_game_espn_id"))
        now = datetime.now().isoformat()
        # Older row has excitement_index computed.
        conn.execute(
            text(
                "INSERT INTO games (id, team_a_id, team_b_id, date, time, "
                "broadcaster, espn_id, winner_id, final_score_a, "
                "final_score_b, excitement_index, excitement_computed_at) "
                "VALUES (101, 1, 2, '2026-05-20', '7:00 PM ET', 'ION', "
                "'evt', 1, 88, 82, 5.4, :now)"
            ),
            {"now": now},
        )
        # Newer row is also final but missing excitement (recomputed
        # fresh by a re-ingest path that didn't carry the cache).
        conn.execute(
            text(
                "INSERT INTO games (id, team_a_id, team_b_id, date, time, "
                "broadcaster, espn_id, winner_id, final_score_a, "
                "final_score_b) VALUES "
                "(102, 1, 2, '2026-05-20', '7:00 PM ET', 'ION', "
                "'evt', 1, 88, 82)"
            )
        )
        conn.commit()

    schema._engine = None
    schema._session_factory = None
    schema.init_db()

    session = schema.get_session()
    try:
        from src.db.schema import Game

        g = session.query(Game).filter(Game.espn_id == "evt").one()
        assert g.id == 102  # newer row wins
        assert g.winner_id == 1  # survivor's own completion preserved
        # Excitement merged in from row 101 since survivor was final
        # and had NULL excitement.
        assert g.excitement_index == 5.4
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_init_db_dedupes_existing_duplicate_espn_ids(tmp_path, monkeypatch):
    """A pre-existing duplicate espn_id (from older overlapping inserts
    before the unique index existed) must be deduped by init_db before
    the index creation runs. Otherwise CREATE UNIQUE INDEX would fail at
    deploy and the app would refuse to start."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from sqlalchemy import text

    from src.db import schema

    # First init creates the schema and the index. Reset state.
    schema._engine = None
    schema._session_factory = None
    schema.init_db()

    # Bypass the unique index to simulate the pre-migration state:
    # delete it, then seed two rows with the same espn_id.
    engine = schema.get_engine()
    with engine.connect() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_game_espn_id"))
        conn.execute(
            text(
                "INSERT INTO games (id, team_a_id, team_b_id, date, time, "
                "broadcaster, espn_id) VALUES "
                "(101, 1, 2, '2026-05-20', '', '', 'dup')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO games (id, team_a_id, team_b_id, date, time, "
                "broadcaster, espn_id, winner_id, final_score_a, final_score_b) "
                "VALUES (102, 1, 2, '2026-05-21', '', '', 'dup', 1, 88, 82)"
            )
        )
        conn.commit()

    # Re-run init_db — must dedupe and successfully re-create the index.
    schema._engine = None
    schema._session_factory = None
    schema.init_db()

    session = schema.get_session()
    try:
        from src.db.schema import Game

        rows = session.query(Game).filter(Game.espn_id == "dup").all()
        # Survivor is the MAX(id) row (more recent insert, has completion).
        assert len(rows) == 1
        assert rows[0].id == 102
        assert rows[0].winner_id == 1

        # Index now exists; another duplicate INSERT must fail.
        from sqlalchemy.exc import IntegrityError

        session.add(
            Game(
                team_a_id=3,
                team_b_id=4,
                date="2026-06-01",
                time="",
                broadcaster="",
                espn_id="dup",
            )
        )
        try:
            session.commit()
            assert False, "expected IntegrityError"
        except IntegrityError:
            session.rollback()
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_upsert_game_handles_duplicate_espn_id_race(tmp_path, monkeypatch):
    """Concurrent daily-update runs could both miss an existing row and
    both INSERT for the same espn_id. The unique index on espn_id makes
    the second INSERT fail with IntegrityError; upsert_game must catch
    that, rollback, re-query by espn_id, and take the update path so the
    final state is a single correctly-updated row (not a duplicate + a
    raised exception)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        # Simulate "another writer already inserted" by writing a row directly.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                espn_id="raced",
            )
        )
        session.commit()

        # Now call upsert_game with the SAME espn_id but a different date —
        # without the race-retry logic this would either insert a duplicate
        # or fail loudly. The retry path matches by espn_id and takes the
        # update branch, moving the row to the new date.
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-21",
            time="8:00 PM ET",
            broadcaster="ION",
            winner_id=1,
            final_score_a=80,
            final_score_b=70,
            espn_id="raced",
            is_complete=True,
        )
        rows = session.query(Game).filter(Game.espn_id == "raced").all()
        assert len(rows) == 1
        assert rows[0].date == "2026-05-21"
        assert rows[0].winner_id == 1
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_unique_index_blocks_duplicate_espn_id_inserts(tmp_path, monkeypatch):
    """A direct INSERT of two rows with the same espn_id must raise
    IntegrityError — that's the DB guarantee the race-retry path relies on."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from sqlalchemy.exc import IntegrityError

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
                broadcaster="",
                espn_id="dup",
            )
        )
        session.add(
            Game(
                team_a_id=3,
                team_b_id=4,
                date="2026-05-21",
                time="",
                broadcaster="",
                espn_id="dup",
            )
        )
        try:
            session.commit()
            assert False, "expected IntegrityError on duplicate espn_id"
        except IntegrityError:
            session.rollback()

        # Multiple NULL espn_ids are allowed (distinct under SQL UNIQUE).
        session.add(
            Game(team_a_id=1, team_b_id=2, date="2026-06-01", time="", broadcaster="")
        )
        session.add(
            Game(team_a_id=3, team_b_id=4, date="2026-06-02", time="", broadcaster="")
        )
        session.commit()
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_completed_archive_keeps_scores_after_rekey(tmp_path, monkeypatch):
    """A completed game corrected after final (moved by espn_id) must
    keep its pre-game quality / importance / overall scores in the
    archive. Without the DailyRanking re-key, the completed-archive
    endpoint would synthesize a None-scored ranking — permanent
    degradation with no automatic rebuild path."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    from src.db.queries import upsert_daily_ranking, upsert_game, upsert_team

    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(schema.Team).filter_by(name="Aces").one().id
    b = session.query(schema.Team).filter_by(name="Liberty").one().id

    # Final game with a fully-stored ranking.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=a,
            final_score_a=80,
            final_score_b=70,
            espn_id="evt",
            excitement_index=5.0,
        )
    )
    upsert_daily_ranking(
        session,
        date="2026-05-20",
        team_a_id=a,
        team_b_id=b,
        quality_score=72.0,
        importance_score=45.0,
        overall_score=61.2,
        broadcaster="ION",
    )
    session.commit()

    # ESPN corrects the date for the same event_id (a late reschedule
    # in the data feed after the game was already played).
    upsert_game(
        session,
        team_a_id=a,
        team_b_id=b,
        date="2026-05-21",
        time="7:00 PM ET",
        broadcaster="ION",
        winner_id=a,
        final_score_a=80,
        final_score_b=70,
        espn_id="evt",
        is_complete=True,
    )
    session.commit()
    session.close()

    from fastapi.testclient import TestClient

    from src.api.app import app

    client = TestClient(app)
    resp = client.get("/api/games/completed")
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2026-05-21"
    # The pre-game ranking scores survived the re-key — not None.
    assert row["quality_score"] == 72.0
    assert row["importance_score"] == 45.0
    assert row["overall_score"] == 61.2
    assert row["excitement_index"] == 5.0
    schema._engine = None
    schema._session_factory = None


def test_upsert_game_rekeys_daily_ranking_on_reschedule(tmp_path, monkeypatch):
    """When upsert_game moves an existing row to a new (date, teams) key
    via espn_id matching, the DailyRanking at the old key must be
    re-keyed to follow the game — not deleted. The pre-game ranking
    data IS the only source of quality / importance / overall scores
    for the completed archive; losing it would degrade the archive
    entry permanently."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_daily_ranking, upsert_game
        from src.db.schema import DailyRanking, Game

        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                espn_id="evt",
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
        session.commit()

        # ESPN reschedules to a new date.
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-25",
            time="8:00 PM ET",
            broadcaster="ION",
            espn_id="evt",
        )

        # Game moved to the new date.
        assert session.query(Game).filter(Game.espn_id == "evt").one().date == (
            "2026-05-25"
        )
        # The DailyRanking moved with it: nothing at the old date,
        # exactly the original row at the new date with scores intact.
        assert (
            session.query(DailyRanking)
            .filter(DailyRanking.date == "2026-05-20")
            .count()
            == 0
        )
        moved = (
            session.query(DailyRanking).filter(DailyRanking.date == "2026-05-25").one()
        )
        assert moved.quality_score == 50.0
        assert moved.overall_score == 50.0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_upsert_game_scheduled_status_preserves_stored_final(tmp_path, monkeypatch):
    """STATUS_SCHEDULED and STATUS_IN_PROGRESS on an already-final game
    must NOT clear stored completion. A stale or partially rolled-back
    ESPN schedule payload reporting one of those for a previously-final
    row is more likely a transient glitch than a real un-finalization.
    Only POSTPONED / CANCELED / RESCHEDULED clear stored finals."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        # Seed a fully-stored final.
        now = datetime.now()
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="ev",
                excitement_index=5.0,
                excitement_computed_at=now,
            )
        )
        session.commit()

        # Simulate fetch_and_store_games' tristate is_complete on a
        # STATUS_SCHEDULED schedule payload (no winner_team, status not
        # in UN_FINALIZE_STATUSES → is_complete=None).
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=None,
            final_score_a=None,
            final_score_b=None,
            espn_id="ev",
            is_complete=None,
        )
        g = session.query(Game).filter(Game.espn_id == "ev").one()
        # Completion preserved — STATUS_SCHEDULED is treated as transient.
        assert g.winner_id == 1
        assert g.final_score_a == 80 and g.final_score_b == 70
        assert g.excitement_index == 5.0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_un_finalize_statuses_excludes_scheduled_and_in_progress():
    """UN_FINALIZE_STATUSES is the explicit allow-list for clearing
    stored completion. SCHEDULED and IN_PROGRESS are deliberately NOT
    in it so a stale/partial ESPN payload can't erase real archive
    entries. Only POSTPONED, CANCELED, RESCHEDULED clear."""
    from src.constants import UN_FINALIZE_STATUSES, GameStatus

    assert GameStatus.POSTPONED in UN_FINALIZE_STATUSES
    assert GameStatus.CANCELED in UN_FINALIZE_STATUSES
    assert GameStatus.RESCHEDULED in UN_FINALIZE_STATUSES
    assert GameStatus.SCHEDULED not in UN_FINALIZE_STATUSES
    assert GameStatus.IN_PROGRESS not in UN_FINALIZE_STATUSES


def test_fetch_and_store_unknown_status_preserves_stored_final(tmp_path, monkeypatch):
    """The daily schedule path must not destructively clear a stored final
    when ESPN returns STATUS_UNKNOWN (parser fallback for a malformed/
    partial response). Same rule as the refresh path: only the explicit
    un-finalize set downgrades."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        # Seed a completed game.
        now = datetime.now()
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=1,
            final_score_a=80,
            final_score_b=70,
            espn_id="ev1",
            is_complete=True,
        )
        # Manually attach the excitement cache (would normally come from
        # the backfill path).
        g = session.query(Game).filter(Game.espn_id == "ev1").one()
        g.excitement_index = 5.0
        g.excitement_computed_at = now
        session.commit()

        # Simulate a STATUS_UNKNOWN parse — caller passes is_complete=None.
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=None,
            final_score_a=None,
            final_score_b=None,
            espn_id="ev1",
            is_complete=None,
        )
        g = session.query(Game).filter(Game.espn_id == "ev1").one()
        # Completion preserved — UNKNOWN is treated as transient.
        assert g.winner_id == 1
        assert g.final_score_a == 80 and g.final_score_b == 70
        assert g.excitement_index == 5.0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_upsert_game_corrects_team_ids_when_matched_by_espn_id(tmp_path, monkeypatch):
    """If ESPN corrects an event's participating teams (data fix on the
    same event_id), the existing row's team_a_id / team_b_id must update
    to match the incoming source of truth — and the cached excitement
    must invalidate, since it was computed against the wrong matchup."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        now = datetime.now()
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="evt",
                excitement_index=5.0,
                excitement_computed_at=now,
            )
        )
        session.commit()

        # ESPN later reports the same event with the corrected team_a.
        upsert_game(
            session,
            team_a_id=3,
            team_b_id=2,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=3,
            final_score_a=85,
            final_score_b=72,
            espn_id="evt",
            is_complete=True,
        )
        games = session.query(Game).filter(Game.espn_id == "evt").all()
        assert len(games) == 1
        g = games[0]
        assert g.team_a_id == 3
        assert g.team_b_id == 2
        assert g.winner_id == 3
        # Excitement cache invalidated because the matchup changed —
        # the previous score was computed against teams 1 vs 2.
        assert g.excitement_index is None
        assert g.excitement_computed_at is None
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_upsert_game_matches_by_espn_id_on_reschedule(tmp_path, monkeypatch):
    """When ESPN reschedules an event (same event_id, new date), upsert_game
    must update the existing row's date instead of inserting a duplicate.
    Otherwise the old completed row stays stale in the archive and a fresh
    row appears at the new date."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        now = datetime.now()
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="evt123",
                excitement_index=5.0,
                excitement_computed_at=now,
            )
        )
        session.commit()

        # ESPN reschedules the same event to a new date and un-finalizes.
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-25",
            time="8:00 PM ET",
            broadcaster="ION",
            winner_id=None,
            final_score_a=None,
            final_score_b=None,
            espn_id="evt123",
            is_complete=False,
        )

        games = session.query(Game).filter(Game.espn_id == "evt123").all()
        assert len(games) == 1
        g = games[0]
        assert g.date == "2026-05-25"
        assert g.time == "8:00 PM ET"
        assert g.winner_id is None
        assert g.excitement_index is None
        # No orphan row at the old date.
        assert session.query(Game).filter(Game.date == "2026-05-20").count() == 0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_refresh_preserves_completion_on_unknown_status(tmp_path, monkeypatch):
    """STATUS_UNKNOWN is fetch_live_win_probability's parser fallback for
    malformed/partial ESPN responses — not a real un-finalization. The
    refresh path must NOT clear stored completion on it, otherwise a
    transient ESPN glitch erases valid completed games."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime, timedelta

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        one_day_ago = datetime.now() - timedelta(days=1)
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="glitched",
                excitement_index=5.0,
                excitement_computed_at=one_day_ago,
                excitement_last_attempt_at=one_day_ago,
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        monkeypatch.setattr(
            daily_update,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: {
                "status": "STATUS_UNKNOWN",
                "plays": [],
            },
        )
        daily_update.refresh_recent_excitement_scores(session, window_days=2)
        session.expire_all()
        g = session.query(Game).filter(Game.espn_id == "glitched").one()
        # All completion fields preserved — UNKNOWN is treated as transient.
        assert g.winner_id == 1
        assert g.final_score_a == 80 and g.final_score_b == 70
        assert g.excitement_index == 5.0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_standings_query_degrades_gracefully_with_unrepaired_nulls(
    tmp_path, monkeypatch
):
    """When the season_type backfill leaves rows unrepaired (e.g. ESPN
    returned a partial/empty schedule), get_completed_games degrades
    gracefully: it uses the looser NULL-tolerant filter so legacy
    regular-season games don't drop out of standings. The trade-off is
    that a legacy NULL preseason row might briefly count as competitive,
    but losing real regular-season results would be worse. Once every
    row has season_type populated, the strict IN (2, 3) filter
    activates — see test_get_completed_games_uses_strict_filter_after_backfill."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_completed_games
        from src.db.schema import Game

        # Two completed rows: a legacy preseason (NULL season_type) and
        # a real regular-season game.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-05",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="legacy-preseason",
            )
        )
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=85,
                final_score_b=78,
                espn_id="real-reg",
                season_type=2,
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        # ESPN returns an empty schedule — no exception, but the legacy
        # preseason row is NOT repaired.
        monkeypatch.setattr(
            daily_update,
            "fetch_games_for_range",
            lambda start, end: [],
        )
        daily_update.backfill_missing_season_types(session)
        session.expire_all()

        legacy = session.query(Game).filter(Game.espn_id == "legacy-preseason").one()
        assert legacy.season_type is None  # still unrepaired

        # Degrade-gracefully: the NULL row IS included alongside the
        # known regular-season game so legacy regulars don't disappear
        # from standings. Once backfill catches up, the strict filter
        # activates (see other test).
        games = get_completed_games(session, season_year=2026)
        espn_ids = {g.espn_id for g in games}
        assert espn_ids == {"legacy-preseason", "real-reg"}
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_backfill_season_type_repairs_legacy_nulls(tmp_path, monkeypatch):
    """Rows ingested before the season_type column existed have NULL
    season_type after migration. The daily schedule refresh only fetches
    yesterday-onward, so pre-deploy preseason finals would otherwise
    keep slipping past the NULL-tolerant standings filter forever.
    backfill_missing_season_types must repair those rows by re-fetching
    the full season schedule keyed by espn_id."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_completed_games
        from src.db.schema import Game

        # Legacy rows: pre-deploy ingestion left season_type NULL.
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-05",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="legacy-pre",
            )
        )
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
                espn_id="legacy-reg",
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        # ESPN's full-season fetch returns these two events with their
        # real season_types.
        monkeypatch.setattr(
            daily_update,
            "fetch_games_for_range",
            lambda start, end: [
                {"event_id": "legacy-pre", "season_type": 1},
                {"event_id": "legacy-reg", "season_type": 2},
                # A game we never ingested — should be ignored, no insert.
                {"event_id": "unknown", "season_type": 2},
            ],
        )
        daily_update.backfill_missing_season_types(session)
        session.expire_all()

        pre = session.query(Game).filter(Game.espn_id == "legacy-pre").one()
        reg = session.query(Game).filter(Game.espn_id == "legacy-reg").one()
        assert pre.season_type == 1
        assert reg.season_type == 2

        # And now get_completed_games (which feeds standings) correctly
        # filters the preseason game out.
        ids = {g.espn_id for g in get_completed_games(session, season_year=2026)}
        assert ids == {"legacy-reg"}
        assert "legacy-pre" not in ids
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_get_completed_games_uses_strict_filter_after_backfill(tmp_path, monkeypatch):
    """Once every row has season_type populated (no NULLs remain),
    get_completed_games activates the strict `IN (2, 3)` filter. This
    is the steady-state mode and the only one where standings are
    guaranteed not to be polluted by a NULL row sneaking in."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_completed_games
        from src.db.schema import Game

        # No NULL rows — backfill is complete.
        for date, espn_id, st in [
            ("2026-05-05", "pre", 1),
            ("2026-05-20", "reg", 2),
            ("2026-09-25", "post", 3),
        ]:
            session.add(
                Game(
                    team_a_id=1,
                    team_b_id=2,
                    date=date,
                    time="",
                    broadcaster="ION",
                    winner_id=1,
                    final_score_a=80,
                    final_score_b=70,
                    espn_id=espn_id,
                    season_type=st,
                )
            )
        session.commit()

        games = get_completed_games(session, season_year=2026)
        espn_ids = {g.espn_id for g in games}
        # Strict filter: only known regular + postseason.
        assert espn_ids == {"reg", "post"}
        assert "pre" not in espn_ids
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_completed_archive_excludes_preseason_games(tmp_path, monkeypatch):
    """Preseason games (season_type=1) must be filtered out of the
    completed archive — they're exhibitions, not regular-season results.
    Regular-season (2) and postseason (3) stay. NULL season_type stays
    included for backward compatibility with rows from before this column."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import (
            get_completed_games_missing_excitement,
            get_completed_rankings,
        )
        from src.db.schema import Game

        # Preseason (excluded), regular (included), postseason (included),
        # legacy NULL (included).
        for date, espn_id, st in [
            ("2026-05-05", "pre", 1),
            ("2026-05-20", "reg", 2),
            ("2026-09-25", "post", 3),
            ("2026-05-21", "legacy", None),
        ]:
            session.add(
                Game(
                    team_a_id=1,
                    team_b_id=2,
                    date=date,
                    time="",
                    broadcaster="ION",
                    winner_id=1,
                    final_score_a=80,
                    final_score_b=70,
                    espn_id=espn_id,
                    season_type=st,
                    excitement_index=4.0,
                )
            )
        session.commit()

        rankings = get_completed_rankings(session, season_year=2026)
        dates = {r.date for r in rankings}
        assert dates == {"2026-05-20", "2026-09-25", "2026-05-21"}
        assert "2026-05-05" not in dates  # preseason excluded

        # Same filter on the backfill-finder. Clear one excitement to make
        # it eligible, then re-check.
        session.query(Game).filter(Game.espn_id == "pre").update(
            {"excitement_index": None}
        )
        session.query(Game).filter(Game.espn_id == "reg").update(
            {"excitement_index": None}
        )
        session.commit()
        missing = get_completed_games_missing_excitement(session, season_year=2026)
        espn_ids = {g.espn_id for g in missing}
        assert "pre" not in espn_ids
        assert "reg" in espn_ids
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_refresh_does_not_clear_completion_on_non_final_status(tmp_path, monkeypatch):
    """The refresh path must NOT clear stored completion when the live-WP
    summary endpoint reports a non-final status. Un-finalization is
    handled by the schedule ingestion path (fetch_and_store_games); the
    summary endpoint isn't the source of truth, and a transient blip
    here would otherwise erase real archive entries."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime, timedelta

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        # Stored a day ago — inside the 2-day refresh window.
        one_day_ago = datetime.now() - timedelta(days=1)
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="reverted",
                excitement_index=5.0,
                excitement_computed_at=one_day_ago,
                excitement_last_attempt_at=one_day_ago,
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        # ESPN now reports STATUS_IN_PROGRESS (e.g. corrected after wrongful final).
        monkeypatch.setattr(
            daily_update,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: {
                "status": "STATUS_IN_PROGRESS",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "5:00", "home_pct": 0.6},
                ],
            },
        )
        daily_update.refresh_recent_excitement_scores(session, window_days=2)
        session.expire_all()
        g = session.query(Game).filter(Game.espn_id == "reverted").one()
        # Refresh path is not authoritative for completion state — all
        # stored fields stay intact.
        assert g.winner_id == 1
        assert g.final_score_a == 80 and g.final_score_b == 70
        assert g.excitement_index == 5.0
        assert g.excitement_computed_at is not None
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_upsert_game_clears_completion_when_un_finalized(tmp_path, monkeypatch):
    """If ESPN un-finalizes a previously-stored game (postponed, protested,
    or a data correction), upsert_game with is_complete=False must clear
    the stored winner, final scores, and the entire excitement cache so
    the archive doesn't keep showing the stale result."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        now = datetime.now()
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="x",
                excitement_index=5.0,
                excitement_computed_at=now,
                excitement_last_attempt_at=now,
            )
        )
        session.commit()

        # ESPN now reports the game as non-final (postponed, etc.).
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=None,
            final_score_a=None,
            final_score_b=None,
            espn_id="x",
            is_complete=False,
        )
        g = session.query(Game).filter(Game.date == "2026-05-20").one()
        assert g.winner_id is None
        assert g.final_score_a is None and g.final_score_b is None
        assert g.excitement_index is None
        assert g.excitement_computed_at is None
        assert g.excitement_last_attempt_at is None
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_upsert_game_invalidates_excitement_on_source_change(tmp_path, monkeypatch):
    """If ESPN corrects a completed game (changed espn_id, winner, or
    final score), the stored excitement_index must be cleared so the
    backfill recomputes from the new PBP. Otherwise the archive would
    sort/display the stale value indefinitely once it ages past the
    bounded refresh window."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        # Seed a completed, scored, computed-once game.
        now = datetime.now()
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="orig",
                excitement_index=5.0,
                excitement_computed_at=now,
                excitement_last_attempt_at=now,
            )
        )
        session.commit()

        # Corrected final score → invalidate.
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=1,
            final_score_a=85,
            final_score_b=70,
            espn_id="orig",
        )
        g = session.query(Game).filter(Game.date == "2026-05-20").one()
        assert g.excitement_index is None
        assert g.excitement_computed_at is None
        assert g.excitement_last_attempt_at is None
        assert g.final_score_a == 85
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_upsert_game_preserves_excitement_when_source_unchanged(tmp_path, monkeypatch):
    """A no-op upsert (same espn_id / winner / scores) must NOT clear
    excitement. Daily-update runs hit upsert_game for every game every
    morning; the cache must survive identity upserts."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import upsert_game
        from src.db.schema import Game

        now = datetime.now()
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="7:00 PM ET",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="orig",
                excitement_index=5.0,
                excitement_computed_at=now,
            )
        )
        session.commit()

        # Same source data → preserve cache.
        upsert_game(
            session,
            team_a_id=1,
            team_b_id=2,
            date="2026-05-20",
            time="7:00 PM ET",
            broadcaster="ION",
            winner_id=1,
            final_score_a=80,
            final_score_b=70,
            espn_id="orig",
        )
        g = session.query(Game).filter(Game.date == "2026-05-20").one()
        assert g.excitement_index == 5.0
        assert g.excitement_computed_at == now
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


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


def test_get_completed_games_missing_excitement_respects_limit(tmp_path, monkeypatch):
    """The retry-finder must honor `limit` (newest first) so the daily job
    can't be stalled by an unbounded ESPN-failure backlog."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.queries import get_completed_games_missing_excitement
        from src.db.schema import Game

        for date, espn_id in [
            ("2026-05-20", "old"),
            ("2026-05-21", "mid"),
            ("2026-05-22", "new"),
        ]:
            session.add(
                Game(
                    team_a_id=1,
                    team_b_id=2,
                    date=date,
                    time="",
                    broadcaster="",
                    winner_id=1,
                    final_score_a=80,
                    final_score_b=70,
                    espn_id=espn_id,
                )
            )
        session.commit()

        capped = get_completed_games_missing_excitement(
            session, season_year=2026, limit=2
        )
        # Newest first; old game waits for the next run.
        assert [g.espn_id for g in capped] == ["new", "mid"]

        # limit=None returns everything.
        full = get_completed_games_missing_excitement(
            session, season_year=2026, limit=None
        )
        assert [g.espn_id for g in full] == ["new", "mid", "old"]
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_refresh_rotates_when_cap_smaller_than_eligible_set(tmp_path, monkeypatch):
    """When more rows share the same `excitement_computed_at` than the
    per-run cap allows — the typical post-backfill state — successive
    runs must rotate through the tail instead of pinning to the head.
    Without the `excitement_last_attempt_at` ordering, all subsequent
    runs would re-select the same first-N rows until they aged out."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime, timedelta

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        # 4 rows, all stamped with the same `computed_at` an hour ago —
        # mimics post-backfill state where many games share a timestamp.
        same_stamp = datetime.now() - timedelta(hours=1)
        for i in range(4):
            session.add(
                Game(
                    team_a_id=1,
                    team_b_id=2,
                    date=f"2026-05-{20 + i:02d}",
                    time="",
                    broadcaster="ION",
                    winner_id=1,
                    final_score_a=80,
                    final_score_b=70,
                    espn_id=f"g{i}",
                    excitement_index=4.0,
                    excitement_computed_at=same_stamp,
                )
            )
        session.commit()

        import scripts.daily_update as daily_update

        def fake_fetch(espn_id, timeout=10):
            return {
                "status": "STATUS_FINAL",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "0:00", "home_pct": 0.6},
                ],
            }

        monkeypatch.setattr(daily_update, "fetch_live_win_probability", fake_fetch)

        # Run 1 with cap=2: hits two rows, stamps their last_attempt_at.
        daily_update.refresh_recent_excitement_scores(session, window_days=2, limit=2)
        session.expire_all()
        round1_touched = {
            g.espn_id
            for g in session.query(Game).filter(
                Game.excitement_last_attempt_at.isnot(None)
            )
        }
        assert len(round1_touched) == 2

        # Run 2 with cap=2: the two never-touched rows take precedence.
        daily_update.refresh_recent_excitement_scores(session, window_days=2, limit=2)
        session.expire_all()
        all_touched = {
            g.espn_id
            for g in session.query(Game).filter(
                Game.excitement_last_attempt_at.isnot(None)
            )
        }
        assert all_touched == {"g0", "g1", "g2", "g3"}
        # The tail rows that didn't get a turn in run 1 are exactly the
        # ones run 2 picked up.
        round2_only = all_touched - round1_touched
        assert len(round2_only) == 2
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_refresh_one_bad_payload_does_not_abort_queue(tmp_path, monkeypatch):
    """A malformed ESPN payload (e.g. home_pct=None raising inside
    compute_excitement) must not abort the refresh loop. Otherwise one
    bad row could permanently block every other eligible refresh."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime, timedelta

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        # Two rows in the refresh window. Refresh ordering is by
        # computed_at ASC, so "bad" (older computed_at) goes first.
        older = datetime.now() - timedelta(days=1, hours=12)
        newer = datetime.now() - timedelta(hours=1)
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-19",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="bad",
                excitement_index=2.0,
                excitement_computed_at=older,
            )
        )
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-20",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=85,
                final_score_b=72,
                espn_id="good",
                excitement_index=3.0,
                excitement_computed_at=newer,
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        def fake_fetch(espn_id, timeout=10):
            if espn_id == "bad":
                # home_pct=None will raise inside compute_excitement when
                # the formula tries to subtract.
                return {
                    "status": "STATUS_FINAL",
                    "plays": [
                        {"period": 1, "clock": "10:00", "home_pct": 0.5},
                        {"period": 4, "clock": "0:00", "home_pct": None},
                    ],
                }
            return {
                "status": "STATUS_FINAL",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "0:30", "home_pct": 0.7},
                    {"period": 4, "clock": "0:00", "home_pct": 0.05},
                ],
            }

        monkeypatch.setattr(daily_update, "fetch_live_win_probability", fake_fetch)
        daily_update.refresh_recent_excitement_scores(session, window_days=2)

        session.expire_all()
        bad = session.query(Game).filter(Game.espn_id == "bad").one()
        good = session.query(Game).filter(Game.espn_id == "good").one()
        # Bad row: score untouched, no crash.
        assert bad.excitement_index == 2.0
        # Good row: still got refreshed despite the earlier failure.
        assert good.excitement_index != 3.0
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_refresh_window_is_bounded_not_sliding(tmp_path, monkeypatch):
    """The freshness window must be anchored to `excitement_computed_at`
    from the first compute, never extended by subsequent refreshes.
    Otherwise refresh attempts perpetually re-qualify the same row and
    the daily job hammers ESPN for stale games indefinitely."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime, timedelta

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        long_ago = datetime.now() - timedelta(days=10)
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-10",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="locked",
                excitement_index=5.0,
                excitement_computed_at=long_ago,
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        # If refresh wrongly selects this row, fetch would be invoked and
        # we'd overwrite the score. The mock makes that visible.
        call_count = {"n": 0}

        def fake_fetch(espn_id, timeout=10):
            call_count["n"] += 1
            return {
                "status": "STATUS_FINAL",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "0:00", "home_pct": 0.99},
                ],
            }

        monkeypatch.setattr(daily_update, "fetch_live_win_probability", fake_fetch)
        daily_update.refresh_recent_excitement_scores(session, window_days=2)

        game = session.query(Game).filter(Game.espn_id == "locked").one()
        # Past the 2-day window → not selected, no ESPN call, score unchanged.
        assert call_count["n"] == 0
        assert game.excitement_index == 5.0
        assert game.excitement_computed_at == long_ago
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_refresh_does_not_slide_window_on_successive_runs(tmp_path, monkeypatch):
    """A successful refresh must NOT update `excitement_computed_at`,
    otherwise the row would keep re-qualifying every run forever."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from datetime import datetime, timedelta

    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.db.schema import Game

        # Stored 1 day ago — still inside a 2-day window.
        one_day_ago = datetime.now() - timedelta(days=1)
        session.add(
            Game(
                team_a_id=1,
                team_b_id=2,
                date="2026-05-10",
                time="",
                broadcaster="ION",
                winner_id=1,
                final_score_a=80,
                final_score_b=70,
                espn_id="evolving",
                excitement_index=3.0,
                excitement_computed_at=one_day_ago,
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        monkeypatch.setattr(
            daily_update,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: {
                "status": "STATUS_FINAL",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "0:00", "home_pct": 0.6},
                ],
            },
        )
        daily_update.refresh_recent_excitement_scores(session, window_days=2)
        game = session.query(Game).filter(Game.espn_id == "evolving").one()
        # Score may have changed, but computed_at must still anchor at the
        # first-compute timestamp — never extended by refresh.
        assert game.excitement_computed_at == one_day_ago
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_refresh_updates_score_after_late_espn_correction(tmp_path, monkeypatch):
    """A persisted STATUS_FINAL excitement_index must still be refreshable
    inside a bounded window. ESPN sometimes refines PBP after first
    finalizing a game; refresh_recent_excitement_scores re-fetches and
    overwrites when the recompute disagrees with the stored value."""
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
                espn_id="evolving",
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        # First daily run: ESPN's initial FINAL payload has minimal plays.
        first_payload = {
            "status": "STATUS_FINAL",
            "plays": [
                {"period": 1, "clock": "10:00", "home_pct": 0.5},
                {"period": 4, "clock": "0:00", "home_pct": 0.55},
            ],
        }
        monkeypatch.setattr(
            daily_update,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: first_payload,
        )
        daily_update.populate_excitement_for_recent_completions(session)
        game = session.query(Game).filter(Game.espn_id == "evolving").one()
        first_score = game.excitement_index
        first_computed_at = game.excitement_computed_at
        assert first_score is not None
        assert first_computed_at is not None

        # Second daily run: ESPN has added late plays — bigger past movement.
        second_payload = {
            "status": "STATUS_FINAL",
            "plays": [
                {"period": 1, "clock": "10:00", "home_pct": 0.5},
                {"period": 4, "clock": "0:30", "home_pct": 0.7},
                {"period": 4, "clock": "0:00", "home_pct": 0.05},
            ],
        }
        monkeypatch.setattr(
            daily_update,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: second_payload,
        )
        daily_update.refresh_recent_excitement_scores(session)
        session.expire_all()
        game = session.query(Game).filter(Game.espn_id == "evolving").one()
        assert game.excitement_index != first_score
        assert game.excitement_index > first_score  # bigger past swing wins
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_populate_excitement_leaves_null_on_non_final_status(tmp_path, monkeypatch):
    """An ESPN payload with status != STATUS_FINAL must NOT be persisted,
    even when it has enough plays to compute a score. The DB's winner_id
    can be set before ESPN finalizes PBP; persisting an in-progress
    score would never be retried because the retry filter is NULL-only."""
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
                espn_id="early",
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        monkeypatch.setattr(
            daily_update,
            "fetch_live_win_probability",
            lambda espn_id, timeout=10: {
                "status": "STATUS_IN_PROGRESS",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "5:00", "home_pct": 0.7},
                ],
            },
        )
        daily_update.populate_excitement_for_recent_completions(session)
        game = session.query(Game).filter(Game.espn_id == "early").one()
        # Score is NOT persisted — partial payload, would never be refreshed.
        assert game.excitement_index is None
        # last_attempt_at IS set so the rotation logic eventually retries.
        assert game.excitement_last_attempt_at is not None
    finally:
        session.close()
        schema._engine = None
        schema._session_factory = None


def test_populate_excitement_rotates_through_failing_backlog(tmp_path, monkeypatch):
    """A cluster of permanently-failing newest rows must not starve older
    NULL rows forever. After a run, those rows' last_attempt_at is set, so
    on the next run never-attempted rows take precedence under the cap."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    try:
        from src.data.espn_api import ESPNAPIError
        from src.db.schema import Game

        # Three "newest" rows that always fail.
        for i, date in enumerate(("2026-05-30", "2026-05-29", "2026-05-28")):
            session.add(
                Game(
                    team_a_id=1,
                    team_b_id=2,
                    date=date,
                    time="",
                    broadcaster="",
                    winner_id=1,
                    final_score_a=80,
                    final_score_b=70,
                    espn_id=f"fail-{i}",
                )
            )
        # One older row that would succeed if given a turn.
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
                espn_id="old-ok",
            )
        )
        session.commit()

        import scripts.daily_update as daily_update

        def fake_fetch(espn_id, timeout=10):
            if espn_id.startswith("fail-"):
                raise ESPNAPIError("permanent failure")
            return {
                "status": "STATUS_FINAL",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "0:00", "home_pct": 0.9},
                ],
            }

        monkeypatch.setattr(daily_update, "fetch_live_win_probability", fake_fetch)

        # Run 1 with cap=3: the three newest failing rows are tried first
        # (never-attempted bucket, ordered by date desc), older row waits.
        daily_update.populate_excitement_for_recent_completions(
            session, limit=3, timeout=1
        )
        old_row = session.query(Game).filter(Game.espn_id == "old-ok").one()
        assert old_row.excitement_index is None  # not yet attempted
        assert old_row.excitement_last_attempt_at is None
        fail_attempts = session.query(Game).filter(Game.espn_id.like("fail-%")).all()
        assert all(g.excitement_last_attempt_at is not None for g in fail_attempts)

        # Run 2 with cap=3: the failing rows have a timestamp now, so the
        # never-attempted older row jumps ahead and gets a turn.
        daily_update.populate_excitement_for_recent_completions(
            session, limit=3, timeout=1
        )
        old_row = session.query(Game).filter(Game.espn_id == "old-ok").one()
        assert old_row.excitement_index is not None  # rotated in, succeeded
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
            lambda espn_id, timeout=10: {"plays": []},
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

        def fake_fetch(espn_id, timeout=10):
            if espn_id == "FAIL":
                raise ESPNAPIError("simulated outage")
            return {
                "status": "STATUS_FINAL",
                "plays": [
                    {"period": 1, "clock": "10:00", "home_pct": 0.5},
                    {"period": 4, "clock": "0:00", "home_pct": 0.7},
                ],
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


def test_get_completed_postseason_games_returns_old_and_new(session, team_ids):
    """A postseason game from many days ago must still come back from the DB.

    Regression guard: bracket-state reconstruction relies on the durable DB
    history, not the rolling ESPN fetch payload (yesterday-forward). A Bo3
    takes ~4 days; Game 1 must remain visible when Game 3 is played.
    """
    a_id, b_id = team_ids
    # Old QF game (4 days before "today"), and a regular-season game on the
    # same date that must NOT show up.
    session.add(
        Game(
            team_a_id=a_id,
            team_b_id=b_id,
            date="2026-09-15",
            time="7:00 PM ET",
            winner_id=a_id,
            espn_id="post_g1",
            season_type=3,
        )
    )
    session.add(
        Game(
            team_a_id=a_id,
            team_b_id=b_id,
            date="2026-09-17",
            time="7:00 PM ET",
            winner_id=a_id,
            espn_id="post_g2",
            season_type=3,
        )
    )
    # Regular season completed game on a postseason date — wrong season_type.
    session.add(
        Game(
            team_a_id=a_id,
            team_b_id=b_id,
            date="2026-09-15",
            time="9:00 PM ET",
            winner_id=b_id,
            espn_id="rs_old",
            season_type=2,
        )
    )
    # In-progress postseason game (no winner) — must not be returned.
    session.add(
        Game(
            team_a_id=a_id,
            team_b_id=b_id,
            date="2026-09-19",
            time="7:00 PM ET",
            winner_id=None,
            espn_id="post_pending",
            season_type=3,
        )
    )
    session.commit()

    rows = get_completed_postseason_games(session, season_year=2026)
    espn_ids = [r.espn_id for r in rows]
    assert espn_ids == ["post_g1", "post_g2"]  # ordered by date,time; both old QF games


def test_upsert_game_persists_time_utc(session, team_ids):
    a_id, b_id = team_ids

    game = upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-05-21",
        time="7:00 PM ET",
        time_utc="2026-05-21T23:00:00+00:00",
        broadcaster="ESPN",
        espn_id="401717777",
    )

    assert game.time_utc == "2026-05-21T23:00:00+00:00"


def test_upsert_game_clears_time_utc_when_explicit_none(session, team_ids):
    """ESPN's timeValid=False (TBD) signal must clear a stale tip time.

    _parse_event emits time_utc=None alongside time="" when ESPN withdraws
    a previously-known tip time. The caller passes those values explicitly,
    so the stored time_utc must be overwritten with NULL — otherwise the
    frontend keeps localizing a stale UTC timestamp ESPN has retracted.
    """
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-05-21",
        time="7:00 PM ET",
        time_utc="2026-05-21T23:00:00+00:00",
        broadcaster="ESPN",
        espn_id="401717778",
    )
    game = upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-05-21",
        time="",
        time_utc=None,
        broadcaster="ESPN",
        espn_id="401717778",
    )

    assert game.time_utc is None


def test_upsert_game_preserves_time_utc_when_kwarg_omitted(session, team_ids):
    """A caller that doesn't pass time_utc at all must leave the stored value alone.

    Distinct from the explicit-None case: omitting the kwarg means "no
    info about this field" (e.g. a non-ESPN code path); the sentinel
    default keeps prior data intact.
    """
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-05-21",
        time="7:00 PM ET",
        time_utc="2026-05-21T23:00:00+00:00",
        broadcaster="ESPN",
        espn_id="401717779",
    )
    game = upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-05-21",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401717779",
    )

    assert game.time_utc == "2026-05-21T23:00:00+00:00"


def test_backfill_time_utc_derives_from_existing_time_and_date(session, team_ids):
    """Legacy rows with time + date but no time_utc get UTC derived locally."""
    from src.db.schema import backfill_time_utc_from_legacy

    a_id, b_id = team_ids

    game = Game(
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        time_utc=None,
        broadcaster="ESPN",
        espn_id="legacy_known",
    )
    session.add(game)
    session.commit()

    updated = backfill_time_utc_from_legacy(session)
    assert updated == 1

    session.expire_all()
    refreshed = session.query(Game).filter_by(espn_id="legacy_known").one()
    # 7:00 PM EDT on Jun 1 = 23:00 UTC.
    assert refreshed.time_utc == "2026-06-01T23:00:00+00:00"


def test_backfill_time_utc_skips_tbd_rows(session, team_ids):
    """Empty time means genuine TBD — leave time_utc NULL."""
    from src.db.schema import backfill_time_utc_from_legacy

    a_id, b_id = team_ids

    game = Game(
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="",
        time_utc=None,
        broadcaster="ESPN",
        espn_id="legacy_tbd",
    )
    session.add(game)
    session.commit()

    updated = backfill_time_utc_from_legacy(session)
    assert updated == 0

    session.expire_all()
    refreshed = session.query(Game).filter_by(espn_id="legacy_tbd").one()
    assert refreshed.time_utc is None


def test_backfill_time_utc_skips_already_populated(session, team_ids):
    """Rows with time_utc set are left untouched — idempotent across runs."""
    from src.db.schema import backfill_time_utc_from_legacy

    a_id, b_id = team_ids

    game = Game(
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        time_utc="2026-06-01T22:00:00+00:00",  # deliberately "wrong" value
        broadcaster="ESPN",
        espn_id="legacy_already",
    )
    session.add(game)
    session.commit()

    updated = backfill_time_utc_from_legacy(session)
    assert updated == 0

    session.expire_all()
    refreshed = session.query(Game).filter_by(espn_id="legacy_already").one()
    # Untouched — backfill only fills gaps, never overwrites.
    assert refreshed.time_utc == "2026-06-01T22:00:00+00:00"


def test_backfill_time_utc_dst_aware(session, team_ids):
    """Standard time (EST, UTC-5) is handled correctly alongside DST (EDT, UTC-4)."""
    from src.db.schema import backfill_time_utc_from_legacy

    a_id, b_id = team_ids

    # November tip-off: EST (UTC-5). 7 PM EST = 00:00 UTC next day.
    game = Game(
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-11-15",
        time="7:00 PM ET",
        time_utc=None,
        broadcaster="ESPN",
        espn_id="legacy_est",
    )
    session.add(game)
    session.commit()

    backfill_time_utc_from_legacy(session)

    session.expire_all()
    refreshed = session.query(Game).filter_by(espn_id="legacy_est").one()
    assert refreshed.time_utc == "2026-11-16T00:00:00+00:00"
