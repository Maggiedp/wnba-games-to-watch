import json
import re

from src.db import queries as q
from src.db.schema import get_session


def _shot(
    pid,
    athlete_id,
    athlete_name,
    team_id,
    team_abbr,
    *,
    x=25,
    y=1,
    made=True,
    pv=2,
    dist=1.0,
):
    return {
        "play_id": pid,
        "athlete_id": athlete_id,
        "athlete_name": athlete_name,
        "team_id": team_id,
        "team_abbr": team_abbr,
        "shot_type": "Layup Shot",
        "distance_ft": dist,
        "coord_x": x,
        "coord_y": y,
        "points": pv if made else 0,
        "point_value": pv,
        "made": made,
    }


def _seed_qualified(env):
    session = get_session()
    q.upsert_shot_making(
        session,
        2026,
        "p-qual",
        athlete_name="Sabrina Ionescu",
        team_id="1",
        team_abbr="NY",
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
    # Some other player so league_avg_xpps has more than one row.
    q.upsert_shot_making(
        session,
        2026,
        "p-other",
        athlete_name="Other Player",
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
    q.upsert_shots(
        session,
        "g1",
        2026,
        [
            _shot(
                "s1", "p-qual", "Sabrina Ionescu", "1", "NY", x=25, y=1, made=True, pv=2
            ),
            _shot(
                "s2",
                "p-qual",
                "Sabrina Ionescu",
                "1",
                "NY",
                x=25,
                y=24,
                made=True,
                pv=3,
                dist=24.0,
            ),
        ],
    )
    session.close()


def _seed_sub_threshold(env):
    session = get_session()
    q.upsert_shots(
        session,
        "g2",
        2026,
        [
            _shot("s3", "p-sub", "Sub Player", "3", "LV", x=25, y=1, made=True, pv=2),
        ],
    )
    session.close()


def test_player_page_qualified(client, env):
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    _seed_qualified(env)

    r = client.get("/player/p-qual")
    assert r.status_code == 200
    body = r.text
    assert "<title>" in body and "Sabrina" in body
    assert 'property="og:title" content="Sabrina' in body
    assert "points added" in body and "#" in body
    assert 'og:image" content="' in body and "/player/p-qual/og.png" in body
    assert 'type="application/json" id="shot-data"' in body

    blob = re.search(r'id="shot-data">(.*?)</script>', body, re.S).group(1)
    parsed = json.loads(blob)
    assert isinstance(parsed, list) and len(parsed) == 2

    assert 'aria-current="page"' in body and "/shot-making" in body


def test_player_page_sub_threshold(client, env):
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    _seed_sub_threshold(env)

    r = client.get("/player/p-sub")
    assert r.status_code == 200
    assert "showing shot chart only" in r.text
    assert "shot-making rank" not in r.text


def test_player_page_unknown_404(client, env):
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    assert client.get("/player/nobody").status_code == 404
