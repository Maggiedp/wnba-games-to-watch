from src.db.schema import Base, get_engine, Shot, ShotMaking, Game, Team
from sqlalchemy.orm import sessionmaker
import pytest

from src.db import queries as q


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    # Reset the cached engine so each test gets a fresh database
    import src.db.schema

    src.db.schema._engine = None
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
            team_abbr="LV",
            shot_type="Driving Layup Shot",
            distance_ft=4.0,
            coord_x=28,
            coord_y=3,
            points=0,
            point_value=2,
            made=False,
        )
    )
    session.commit()
    assert session.query(Shot).count() == 1
    row = session.query(Shot).one()
    assert row.team_abbr == "LV"
    from sqlalchemy.exc import IntegrityError

    session.add(
        Shot(
            espn_game_id="401857083",
            play_id="1",
            season=2026,
            athlete_id="5345444",
            athlete_name="Laura Juskaite",
            team_id="20",
            team_abbr="LV",
            shot_type="x",
            points=0,
            point_value=2,
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


def _two_teams_and_game(session, espn_id, date):
    session.add_all([Team(id=1, name="A"), Team(id=2, name="B")])
    session.add(
        Game(
            id=1,
            team_a_id=1,
            team_b_id=2,
            date=date,
            espn_id=espn_id,
            winner_id=1,
            season_type=2,
        )
    )
    session.commit()


def test_upsert_shots_is_idempotent(session):
    rows = [
        {
            "play_id": "1",
            "athlete_id": "10",
            "athlete_name": "X",
            "team_id": "1",
            "shot_type": "Jump Shot",
            "distance_ft": 15.0,
            "coord_x": 1,
            "coord_y": 2,
            "points": 2,
            "point_value": 2,
            "made": True,
        }
    ]
    assert q.upsert_shots(session, "G1", 2026, rows) == 1
    assert q.upsert_shots(session, "G1", 2026, rows) == 0  # dedup
    assert len(q.get_shots_for_season(session, 2026)) == 1


def test_upsert_shots_dedups_within_batch(session):
    # ESPN returning the same play_id twice in one payload must not hit the
    # uq_shot_play constraint (Codex R1) — the second copy is skipped, one row.
    def _shot(pid):
        return {
            "play_id": pid,
            "athlete_id": "10",
            "athlete_name": "X",
            "team_id": "1",
            "shot_type": "Jump Shot",
            "distance_ft": 15.0,
            "coord_x": 1,
            "coord_y": 2,
            "points": 2,
            "point_value": 2,
            "made": True,
        }

    assert (
        q.upsert_shots(session, "G1", 2026, [_shot("1"), _shot("1"), _shot("2")]) == 2
    )
    assert len(q.get_shots_for_season(session, 2026)) == 2


def test_missing_shots_excludes_already_ingested(session):
    _two_teams_and_game(session, "G1", "2026-07-20")
    assert [g.espn_id for g in q.get_completed_games_missing_shots(session, 2026)] == [
        "G1"
    ]
    q.upsert_shots(
        session,
        "G1",
        2026,
        [
            {
                "play_id": "1",
                "athlete_id": "10",
                "athlete_name": "X",
                "team_id": "1",
                "shot_type": "Jump Shot",
                "distance_ft": None,
                "coord_x": None,
                "coord_y": None,
                "points": 2,
                "point_value": 2,
                "made": True,
            }
        ],
    )
    assert q.get_completed_games_missing_shots(session, 2026) == []


def test_shot_making_upsert_and_read(session):
    q.upsert_shot_making(
        session,
        2026,
        "10",
        athlete_name="X",
        team_id="1",
        team_abbr="LV",
        fga=100,
        made=50,
        actual_pts=110.0,
        expected_pts=100.0,
        points_added=10.0,
        points_added_per_100=10.0,
        actual_pps=1.1,
        expected_pps=1.0,
        diet="{}",
    )
    q.upsert_shot_making(
        session,
        2026,
        "10",
        athlete_name="X",
        team_id="1",
        team_abbr="LV",
        fga=101,
        made=51,
        actual_pts=112.0,
        expected_pts=101.0,
        points_added=11.0,
        points_added_per_100=10.89,
        actual_pps=1.108,
        expected_pps=1.0,
        diet="{}",
    )
    rows = q.get_shot_making(session, 2026)
    assert len(rows) == 1 and rows[0].fga == 101  # upsert, not insert
    assert rows[0].team_abbr == "LV"


def _shot_payload(play_id, aid, x=25, y=4):
    return {
        "play_id": play_id,
        "athlete_id": aid,
        "athlete_name": "P",
        "team_id": "t1",
        "team_abbr": "AAA",
        "shot_type": "Layup Shot",
        "distance_ft": 3.0,
        "coord_x": x,
        "coord_y": y,
        "points": 2,
        "point_value": 2,
        "made": True,
    }


def test_get_shots_for_player_filters_by_season_and_athlete(session):
    q.upsert_shots(
        session, "g1", 2026, [_shot_payload("p1", "star"), _shot_payload("p2", "other")]
    )
    q.upsert_shots(session, "g2", 2025, [_shot_payload("p3", "star")])
    rows = q.get_shots_for_player(session, 2026, "star")
    assert len(rows) == 1
    assert rows[0].athlete_id == "star" and rows[0].season == 2026
