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
    from src.db.queries import get_game_fields  # noqa: F401 — verify it's importable

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


def test_completed_endpoint_returns_excitement_sorted_games(tmp_path, monkeypatch):
    """GET /api/games/completed returns 2026 completed games sorted by excitement desc."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    from src.db.queries import upsert_daily_ranking, upsert_team
    from src.db.schema import Game

    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    team_a_id = session.query(schema.Team).filter_by(name="Aces").one().id
    team_b_id = session.query(schema.Team).filter_by(name="Liberty").one().id

    for date, excitement in [
        ("2026-05-20", 3.0),
        ("2026-05-21", 7.0),
        ("2026-05-22", 5.0),
    ]:
        session.add(
            Game(
                team_a_id=team_a_id,
                team_b_id=team_b_id,
                date=date,
                time="",
                broadcaster="ION",
                winner_id=team_a_id,
                final_score_a=80,
                final_score_b=70,
                espn_id=f"e{date}",
                excitement_index=excitement,
            )
        )
        upsert_daily_ranking(
            session,
            date=date,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            quality_score=50.0,
            importance_score=None,
            overall_score=50.0,
            broadcaster="ION",
        )
    session.commit()
    session.close()

    from fastapi.testclient import TestClient

    from src.api.app import app

    client = TestClient(app)
    resp = client.get("/api/games/completed")
    assert resp.status_code == 200
    rows = resp.json()
    dates_in_order = [r["date"] for r in rows]
    assert dates_in_order == ["2026-05-21", "2026-05-22", "2026-05-20"]
    assert rows[0]["excitement_index"] == 7.0
    assert rows[0]["final_score_a"] == 80
    assert rows[0]["final_score_b"] == 70
    schema._engine = None
    schema._session_factory = None


def test_completed_endpoint_includes_orphan_games_without_ranking(
    tmp_path, monkeypatch
):
    """A completed game with excitement_index but no DailyRanking row must
    still appear in /api/games/completed (with None scored fields), so the
    archive doesn't silently hide games on a missed daily-update day."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    from src.db.queries import upsert_team
    from src.db.schema import Game

    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(schema.Team).filter_by(name="Aces").one().id
    b = session.query(schema.Team).filter_by(name="Liberty").one().id

    # Orphan: completed + has excitement, no DailyRanking row.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-20",
            time="",
            broadcaster="ESPN",
            winner_id=a,
            final_score_a=85,
            final_score_b=82,
            espn_id="orphan",
            excitement_index=6.4,
        )
    )
    session.commit()
    session.close()

    from fastapi.testclient import TestClient

    from src.api.app import app

    client = TestClient(app)
    resp = client.get("/api/games/completed")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2026-05-20"
    assert row["excitement_index"] == 6.4
    assert row["final_score_a"] == 85 and row["final_score_b"] == 82
    # Pre-game ranking fields are None because no DailyRanking exists.
    assert row["quality_score"] is None
    assert row["overall_score"] is None
    schema._engine = None
    schema._session_factory = None


def test_filter_endpoint_mode_completed(tmp_path, monkeypatch):
    """/api/games/filter?mode=completed&broadcaster=ION restricts to completed ION games."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    session = schema.get_session()
    from src.db.queries import upsert_daily_ranking, upsert_team
    from src.db.schema import Game

    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(schema.Team).filter_by(name="Aces").one().id
    b = session.query(schema.Team).filter_by(name="Liberty").one().id

    # Completed ION game — past date, would be excluded by date >= today filter.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-10",
            time="",
            broadcaster="ION",
            winner_id=a,
            final_score_a=80,
            final_score_b=70,
            espn_id="x1",
            excitement_index=5.0,
        )
    )
    upsert_daily_ranking(
        session,
        date="2026-05-10",
        team_a_id=a,
        team_b_id=b,
        quality_score=50.0,
        importance_score=None,
        overall_score=50.0,
        broadcaster="ION",
    )
    # Completed but wrong broadcaster — also past.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-11",
            time="",
            broadcaster="ESPN",
            winner_id=a,
            final_score_a=80,
            final_score_b=70,
            espn_id="x2",
            excitement_index=8.0,
        )
    )
    upsert_daily_ranking(
        session,
        date="2026-05-11",
        team_a_id=a,
        team_b_id=b,
        quality_score=50.0,
        importance_score=None,
        overall_score=50.0,
        broadcaster="ESPN",
    )
    session.commit()
    session.close()

    from fastapi.testclient import TestClient
    from src.api.app import app

    client = TestClient(app)
    resp = client.get("/api/games/filter?mode=completed&broadcaster=ION")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["date"] for r in rows] == ["2026-05-10"]
    schema._engine = None
    schema._session_factory = None
