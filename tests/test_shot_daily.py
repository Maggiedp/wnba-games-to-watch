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
            "team_abbr": "LV",
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
                    "team_abbr": "LV",
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
    assert q.get_shot_making(session, 2026)[0].team_abbr == "LV"


def test_populate_shots_isolates_a_bad_game(session, monkeypatch):
    # Two completed games; G2's payload carries a NULL in a NOT-NULL column so its
    # per-game commit fails. G1 must stay committed and G2 must leave ZERO rows and
    # remain a retry candidate — no cross-game transaction corruption (Codex R1).
    session.add_all([Team(id=1, name="A"), Team(id=2, name="B")])
    session.add_all(
        [
            Game(
                id=1,
                team_a_id=1,
                team_b_id=2,
                date="2026-07-20",
                espn_id="G1",
                winner_id=1,
                season_type=2,
            ),
            Game(
                id=2,
                team_a_id=1,
                team_b_id=2,
                date="2026-07-19",
                espn_id="G2",
                winner_id=1,
                season_type=2,
            ),
        ]
    )
    session.commit()

    def fake_fetch(espn_id, **k):
        shot = {
            "play_id": "1",
            "athlete_id": "10",
            "athlete_name": "X",
            "team_id": "1",
            "team_abbr": "LV",
            "shot_type": "Layup Shot",
            "distance_ft": 2.0,
            "coord_x": None,
            "coord_y": None,
            "points": 2,
            "point_value": 2,
            "made": True,
        }
        if espn_id == "G2":
            shot["point_value"] = None  # NOT NULL -> commit raises IntegrityError
        return [shot]

    monkeypatch.setattr(du, "fetch_shots", fake_fetch)
    du.populate_shots_for_recent_completions(session)  # must not raise

    persisted = {s.espn_game_id for s in q.get_shots_for_season(session, 2026)}
    assert persisted == {"G1"}  # G1 committed, G2 rolled back cleanly
    candidates = {g.espn_id for g in q.get_completed_games_missing_shots(session, 2026)}
    assert "G2" in candidates and "G1" not in candidates  # G2 still retryable


def test_recompute_removes_stale_players(session):
    # A pre-existing shot_making row for a player who has NO shots in this
    # recompute (e.g. dropped below min_fga after a corrected re-ingest). The
    # wholesale replace must drop it, not keep serving a ghost (Codex R2).
    q.upsert_shot_making(
        session,
        2026,
        "gone",
        athlete_name="Gone",
        team_id="9",
        team_abbr="XX",
        fga=200,
        made=100,
        actual_pts=1.0,
        expected_pts=1.0,
        points_added=0.0,
        points_added_per_100=0.0,
        actual_pps=1.0,
        expected_pps=1.0,
        diet="{}",
    )
    for i in range(120):
        q.upsert_shots(
            session,
            "G1",
            2026,
            [
                {
                    "play_id": str(i),
                    "athlete_id": "keep",
                    "athlete_name": "Keep",
                    "team_id": "1",
                    "team_abbr": "LV",
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
    du.recompute_shot_making(session, 2026)
    ids = {r.athlete_id for r in q.get_shot_making(session, 2026)}
    assert "keep" in ids
    assert "gone" not in ids  # stale row removed by the wholesale replace


def test_populate_shots_derives_current_season(session, monkeypatch):
    # Season comes from today(), not a hard-coded 2026 — a 2027 game ingests and
    # its shots are tagged season=2027, staying consistent with recompute's
    # int(today[:4]) (Codex R1 rollover).
    monkeypatch.setattr(du, "today_et", lambda: "2027-06-15")
    session.add_all([Team(id=1, name="A"), Team(id=2, name="B")])
    session.add(
        Game(
            id=1,
            team_a_id=1,
            team_b_id=2,
            date="2027-06-14",
            espn_id="G27",
            winner_id=1,
            season_type=2,
        )
    )
    session.commit()
    fake = [
        {
            "play_id": "1",
            "athlete_id": "10",
            "athlete_name": "X",
            "team_id": "1",
            "team_abbr": "LV",
            "shot_type": "Layup Shot",
            "distance_ft": 2.0,
            "coord_x": None,
            "coord_y": None,
            "points": 2,
            "point_value": 2,
            "made": True,
        }
    ]
    monkeypatch.setattr(du, "fetch_shots", lambda *a, **k: fake)
    du.populate_shots_for_recent_completions(session)
    rows_2027 = q.get_shots_for_season(session, 2027)
    assert len(rows_2027) == 1 and rows_2027[0].season == 2027
    assert q.get_shots_for_season(session, 2026) == []
