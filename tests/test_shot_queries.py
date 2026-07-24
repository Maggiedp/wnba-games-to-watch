from src.db.schema import Base, get_engine, Shot, ShotMaking
from sqlalchemy.orm import sessionmaker
import pytest


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    engine = get_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_shot_table_roundtrips_and_dedups(session):
    session.add(
        Shot(
            espn_game_id="401857083",
            play_id="1",
            season=2026,
            athlete_id="5345444",
            athlete_name="Laura Juskaite",
            team_id="20",
            shot_type="Driving Layup Shot",
            distance_ft=4.0,
            coord_x=28,
            coord_y=3,
            points=0,
            made=False,
        )
    )
    session.commit()
    assert session.query(Shot).count() == 1
    from sqlalchemy.exc import IntegrityError

    session.add(
        Shot(
            espn_game_id="401857083",
            play_id="1",
            season=2026,
            athlete_id="5345444",
            athlete_name="Laura Juskaite",
            team_id="20",
            shot_type="x",
            points=0,
            made=False,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_shot_making_table_roundtrips(session):
    session.add(
        ShotMaking(
            season=2026,
            athlete_id="3910470",
            athlete_name="Maria Conde",
            team_id="4",
            fga=120,
            made=60,
            actual_pts=140.0,
            expected_pts=130.0,
            points_added=10.0,
            points_added_per_100=8.33,
            actual_pps=1.166,
            expected_pps=1.083,
            diet="{}",
        )
    )
    session.commit()
    assert session.query(ShotMaking).one().points_added == 10.0
