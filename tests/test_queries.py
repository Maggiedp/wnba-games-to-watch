"""Tests for src/db/queries.py — focused on upsert correctness."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.queries import (
    get_playoff_probabilities,
    upsert_game,
    upsert_playoff_probability,
    upsert_team,
)
from src.db.schema import Base, PlayoffProbability


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
