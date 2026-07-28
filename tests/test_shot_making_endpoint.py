import json
from src.db import queries as q
from src.db.schema import get_session


def _seed(env):
    session = get_session()
    q.upsert_shot_making(
        session,
        2026,
        "10",
        athlete_name="Hot",
        team_id="1",
        team_abbr="LV",
        fga=150,
        made=90,
        actual_pts=190.0,
        expected_pts=170.0,
        points_added=20.0,
        points_added_per_100=13.33,
        actual_pps=1.266,
        expected_pps=1.133,
        diet=json.dumps({"rim": 0.6, "three": 0.4}),
    )
    q.upsert_shot_making(
        session,
        2026,
        "11",
        athlete_name="Cold",
        team_id="2",
        team_abbr="TOR",
        fga=150,
        made=60,
        actual_pts=130.0,
        expected_pts=150.0,
        points_added=-20.0,
        points_added_per_100=-13.33,
        actual_pps=0.866,
        expected_pps=1.0,
        diet=json.dumps({"mid": 1.0}),
    )
    session.commit()
    session.close()


def test_shot_making_endpoint_ranks_desc(client, env):
    _seed(env)
    r = client.get("/api/shot-making")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == 2026
    names = [p["athlete_name"] for p in body["players"]]
    assert names == ["Hot", "Cold"]
    assert body["players"][0]["rank"] == 1
    assert body["players"][0]["points_added"] == 20.0
    assert body["players"][0]["team_abbr"] == "LV"


def test_shot_making_endpoint_empty(client, env):
    from src.data.espn_api import today_et

    r = client.get("/api/shot-making")
    assert r.status_code == 200
    assert r.json() == {"season": int(today_et()[:4]), "players": []}


def test_endpoint_does_not_fall_back_to_prior_season(client, env, monkeypatch):
    # The current season has no rows but a PRIOR season does: the endpoint must
    # return an empty CURRENT-season board, never last season's leaderboard
    # silently mislabeled as "this season" (Codex R3).
    import src.api.app as app_mod

    monkeypatch.setattr(app_mod, "today_et", lambda: "2099-07-01")
    session = get_session()
    q.upsert_shot_making(
        session,
        2026,
        "old",
        athlete_name="Old",
        team_id="1",
        team_abbr="LV",
        fga=150,
        made=80,
        actual_pts=170.0,
        expected_pts=160.0,
        points_added=10.0,
        points_added_per_100=6.67,
        actual_pps=1.133,
        expected_pps=1.067,
        diet="{}",
    )
    session.commit()
    session.close()
    r = client.get("/api/shot-making")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == 2099  # current season, not the populated 2026
    assert body["players"] == []
