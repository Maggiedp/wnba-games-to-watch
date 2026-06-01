import pytest
from fastapi.testclient import TestClient

from src.db.queries import replace_elo_history, upsert_team


@pytest.fixture
def env(tmp_path, monkeypatch):
    """File-backed sqlite shared across the seed session and the request
    session. Mirrors tests/test_routes.py. Yields the schema module so tests
    can call env.get_session() / env.Team."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    yield schema
    schema._engine = None
    schema._session_factory = None


def _seed_elo(schema):
    session = schema.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0)
    a = session.query(schema.Team).filter_by(name="Aces").one().id
    b = session.query(schema.Team).filter_by(name="Storm").one().id
    replace_elo_history(
        session,
        "2026",
        [
            (a, "2026-05-10", 1600.0),
            (b, "2026-05-10", 1450.0),
            (a, "2026-05-15", 1615.0),
        ],
    )
    session.close()


def test_elo_history_shape(env):
    _seed_elo(env)
    from src.api.app import app

    r = TestClient(app).get("/api/elo-history?season=2026")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == "2026"
    assert body["teams"]["Aces"] == [
        {"date": "2026-05-10", "rating": 1600.0},
        {"date": "2026-05-15", "rating": 1615.0},
    ]
    assert body["teams"]["Storm"] == [{"date": "2026-05-10", "rating": 1450.0}]


def test_elo_history_empty_season(env):
    from src.api.app import app

    r = TestClient(app).get("/api/elo-history?season=1999")
    assert r.status_code == 200
    assert r.json() == {"season": "1999", "teams": {}}


def test_transparency_page_renders():
    from src.api.app import app

    r = TestClient(app).get("/transparency")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="elo-chart"' in r.text
    assert 'id="calibration-chart"' in r.text
    assert "loadCalibration" in r.text
    assert "Behind the numbers" in r.text


def test_calibration_endpoint(env):
    from src.db.queries import upsert_daily_ranking
    from src.db.schema import Game

    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Storm").one().id
    # Two completed regular-season games.
    session.add_all(
        [
            Game(
                team_a_id=a,
                team_b_id=b,
                date="2026-05-10",
                time="",
                broadcaster="",
                winner_id=a,
                season_type=2,
                espn_id="e1",
            ),
            Game(
                team_a_id=a,
                team_b_id=b,
                date="2026-05-12",
                time="",
                broadcaster="",
                winner_id=b,
                season_type=2,
                espn_id="e2",
            ),
        ]
    )
    session.commit()
    for d, wp in [("2026-05-10", 0.9), ("2026-05-12", 0.4)]:
        upsert_daily_ranking(
            session,
            date=d,
            team_a_id=a,
            team_b_id=b,
            quality_score=0.0,
            importance_score=None,
            overall_score=0.0,
            broadcaster="",
            win_prob_a=wp,
        )
    session.commit()
    session.close()

    from src.api.app import app

    r = TestClient(app).get("/api/calibration?season=2026")
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 2
    assert "brier" in body
    assert isinstance(body["buckets"], list)
