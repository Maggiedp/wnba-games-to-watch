import pytest
from sqlalchemy.orm import sessionmaker
from src.db.schema import Base, get_engine, Team, Game
from src.db import queries as q
import scripts.daily_update as du


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    # Reset the cached engine so each test gets a fresh database (mirrors
    # tests/test_shot_queries.py — get_engine() memoizes globally, so without
    # this a later test can collide with an earlier test's sqlite file).
    import src.db.schema

    src.db.schema._engine = None
    engine = get_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_game(session, espn_id):
    session.add_all([Team(id=1, name="A"), Team(id=2, name="B")])
    session.add(
        Game(
            id=1,
            team_a_id=1,
            team_b_id=2,
            date="2026-07-20",
            espn_id=espn_id,
            winner_id=1,
            season_type=2,
        )
    )
    session.commit()


def test_populate_shots_ingests_and_is_idempotent(session, monkeypatch):
    _seed_game(session, "G1")
    fake = [
        {
            "play_id": "1",
            "athlete_id": "10",
            "athlete_name": "X",
            "team_id": "1",
            "shot_type": "Layup Shot",
            "distance_ft": 2.0,
            "coord_x": 1,
            "coord_y": 1,
            "points": 2,
            "point_value": 2,
            "made": True,
        }
    ]
    monkeypatch.setattr(du, "fetch_shots", lambda *a, **k: fake)
    du.populate_shots_for_recent_completions(session)
    assert len(q.get_shots_for_season(session, 2026)) == 1
    du.populate_shots_for_recent_completions(session)  # game no longer a candidate
    assert len(q.get_shots_for_season(session, 2026)) == 1


def test_populate_shots_survives_espn_error(session, monkeypatch):
    _seed_game(session, "G1")

    def boom(*a, **k):
        raise du.ESPNAPIError("down")

    monkeypatch.setattr(du, "fetch_shots", boom)
    du.populate_shots_for_recent_completions(session)  # must not raise
    assert q.get_shots_for_season(session, 2026) == []


def test_recompute_shot_making_writes_rows(session):
    for i in range(120):
        q.upsert_shots(
            session,
            "G1",
            2026,
            [
                {
                    "play_id": str(i),
                    "athlete_id": "10",
                    "athlete_name": "X",
                    "team_id": "1",
                    "shot_type": "Layup Shot",
                    "distance_ft": 2.0,
                    "coord_x": None,
                    "coord_y": None,
                    "points": 2 if i < 80 else 0,
                    "point_value": 2,
                    "made": i < 80,
                }
            ],
            commit=False,
        )
    session.commit()
    assert du.recompute_shot_making(session, 2026) == 1
    assert q.get_shot_making(session, 2026)[0].fga == 120
