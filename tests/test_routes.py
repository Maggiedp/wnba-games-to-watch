"""Tests for src/api/routes.py — focused on response formatting."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.routes import format_games_response
from src.db.queries import (
    upsert_daily_ranking,
    upsert_game,
    upsert_playoff_probability,
    upsert_team,
)
from src.db.schema import Base


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


def test_format_games_response_includes_time_from_game_row(session, team_ids):
    """Response time comes from the Game row (DailyRanking has no time column)."""
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )

    [resp] = format_games_response([ranking], session)

    assert resp.time == "7:00 PM ET"


def test_format_games_response_empty_time_when_no_game_row(session, team_ids):
    """Falls back to empty string if no Game row matches the ranking."""
    a_id, b_id = team_ids

    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="",
    )

    [resp] = format_games_response([ranking], session)

    assert resp.time == ""


def test_format_games_response_includes_playoff_probs(session, team_ids):
    """format_games_response joins today's playoff odds onto each game response."""
    from datetime import datetime

    a_id, b_id = team_ids
    today = datetime.now().strftime("%Y-%m-%d")

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=today,
        time="7:00 PM ET",
        broadcaster="ESPN",
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=40.0,
        overall_score=46.0,
        broadcaster="ESPN",
    )
    upsert_playoff_probability(session, date=today, team_id=a_id, probability=0.72)
    upsert_playoff_probability(session, date=today, team_id=b_id, probability=0.48)

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.team_a_playoff_prob == pytest.approx(0.72)
    assert resp.team_b_playoff_prob == pytest.approx(0.48)


def test_format_games_response_playoff_probs_none_when_missing(session, team_ids):
    """playoff probs are None when no odds have been stored for the date."""
    from datetime import datetime

    a_id, b_id = team_ids
    today = datetime.now().strftime("%Y-%m-%d")

    upsert_game(
        session, team_a_id=a_id, team_b_id=b_id, date=today, time="", broadcaster=""
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=None,
        overall_score=30.0,
        broadcaster="",
    )

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.team_a_playoff_prob is None
    assert resp.team_b_playoff_prob is None


def test_format_games_response_passes_win_prob(session, team_ids):
    """win_prob_a from DailyRanking flows through to GameResponse."""
    from datetime import datetime

    a_id, b_id = team_ids
    today = datetime.now().strftime("%Y-%m-%d")

    upsert_game(
        session, team_a_id=a_id, team_b_id=b_id, date=today, time="", broadcaster=""
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=40.0,
        overall_score=46.0,
        broadcaster="",
        win_prob_a=0.62,
    )

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.win_prob_a == pytest.approx(0.62)


def test_format_games_response_win_prob_none_when_missing(session, team_ids):
    """win_prob_a is None in response when not stored."""
    from datetime import datetime

    a_id, b_id = team_ids
    today = datetime.now().strftime("%Y-%m-%d")

    upsert_game(
        session, team_a_id=a_id, team_b_id=b_id, date=today, time="", broadcaster=""
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=None,
        overall_score=30.0,
        broadcaster="",
    )

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.win_prob_a is None


def test_format_games_response_includes_espn_id(session, team_ids):
    """espn_id from the Game row is passed through to GameResponse."""
    from src.db.queries import get_espn_ids  # noqa: F401 — verify it's importable

    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )
    result = format_games_response([ranking], session)
    assert result[0].espn_id == "401856901"


def test_format_games_response_populates_game_status_from_dict(session, team_ids):
    """game_status_by_espn_id dict is applied when provided."""
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )
    result = format_games_response(
        [ranking],
        session,
        game_status_by_espn_id={"401856901": "STATUS_IN_PROGRESS"},
    )
    assert result[0].game_status == "STATUS_IN_PROGRESS"


def test_format_games_response_game_status_none_when_no_dict(session, team_ids):
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )
    result = format_games_response([ranking], session)
    assert result[0].game_status is None
