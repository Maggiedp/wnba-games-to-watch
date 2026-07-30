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


def test_shot_making_endpoint_reports_league_avg_xpps(client, env):
    _seed(env)
    body = client.get("/api/shot-making").json()
    total_ex = 1.133 * 150 + 1.0 * 150
    total_fga = 150 + 150
    assert body["league_avg_xpps"] == round(total_ex / total_fga, 3)


def test_shot_making_endpoint_empty(client, env):
    from src.data.espn_api import today_et

    r = client.get("/api/shot-making")
    assert r.status_code == 200
    assert r.json() == {
        "season": int(today_et()[:4]),
        "league_avg_xpps": None,
        "players": [],
    }


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


def _p(play_id, aid, name, x=25, y=4, made=True, pv=2, dist=3.0, stype="Layup Shot"):
    return {
        "play_id": play_id,
        "athlete_id": aid,
        "athlete_name": name,
        "team_id": "t1",
        "team_abbr": "AAA",
        "shot_type": stype,
        "distance_ft": dist,
        "coord_x": x,
        "coord_y": y,
        "points": pv if made else 0,
        "point_value": pv,
        "made": made,
    }


def test_player_shots_returns_chart_and_zones(client, monkeypatch):
    from src.api import app as app_module
    from src.db.queries import upsert_shots
    from src.db.schema import get_session

    monkeypatch.setattr(app_module, "today_et", lambda: "2026-07-28")
    app_module._shot_baseline_cache = None  # clear TTL cache between tests
    session = get_session()
    payload = []
    for i in range(40):
        payload.append(_p(f"a{i}", f"g{i}", "Filler", made=(i % 2 == 0)))
        payload.append(
            _p(
                f"b{i}",
                f"g{i}",
                "Filler",
                x=2,
                y=2,
                made=(i % 3 == 0),
                pv=3,
                dist=24.0,
                stype="Jump Shot",
            )
        )
    payload.append(
        _p(
            "s1",
            "star",
            "Star Player",
            made=True,
            pv=3,
            dist=24.0,
            x=2,
            y=2,
            stype="Jump Shot",
        )
    )
    payload.append(_p("s2", "star", "Star Player", made=False))
    upsert_shots(session, "g1", 2026, payload)
    session.close()

    r = client.get("/api/player-shots?athlete_id=star")
    assert r.status_code == 200
    data = r.json()
    assert data["athlete_name"] == "Star Player"
    assert data["fga"] == 2
    assert len(data["shots"]) == 2
    assert {z["family"] for z in data["zones"]} == {"rim", "three"}


def test_player_shots_unknown_athlete_is_empty_not_500(client, monkeypatch):
    from src.api import app as app_module

    monkeypatch.setattr(app_module, "today_et", lambda: "2026-07-28")
    app_module._shot_baseline_cache = None
    r = client.get("/api/player-shots?athlete_id=nobody")
    assert r.status_code == 200
    data = r.json()
    assert data["fga"] == 0 and data["shots"] == [] and data["zones"] == []


def test_player_shots_omits_team_and_total_for_traded_player(client, monkeypatch):
    """The response must NOT carry `team_abbr` or `points_added`. The panel renders
    only shots+zones, so both are unused — and each is a trap: `team_abbr` would be
    `rows[0]`'s team, nondeterministic for a traded player (get_shots_for_player has
    no ORDER BY); `points_added` is live-recomputed and would spuriously mismatch
    the daily `shot_making` leaderboard row during the 6 AM ingest→recompute window.
    Dropping them is the fix (Codex adversarial R2)."""
    from src.api import app as app_module
    from src.db.queries import upsert_shots
    from src.db.schema import get_session

    monkeypatch.setattr(app_module, "today_et", lambda: "2026-07-28")
    app_module._shot_baseline_cache = None
    session = get_session()

    def shot(pid, team_id, abbr):
        return {
            "play_id": pid,
            "athlete_id": "traded",
            "athlete_name": "Traded Player",
            "team_id": team_id,
            "team_abbr": abbr,
            "shot_type": "Layup Shot",
            "distance_ft": 3.0,
            "coord_x": 25,
            "coord_y": 4,
            "points": 2,
            "point_value": 2,
            "made": True,
        }

    # Two teams' shots for one player — team_abbr would flap on row order if exposed.
    upsert_shots(
        session,
        "g1",
        2026,
        [shot("t1a", "10", "LV"), shot("t1b", "10", "LV"), shot("t2a", "20", "NY")],
    )
    session.close()

    data = client.get("/api/player-shots?athlete_id=traded").json()
    assert "team_abbr" not in data
    assert "points_added" not in data
    assert data["fga"] == 3
