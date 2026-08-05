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


def _seed_sub_threshold_traded(env):
    """A sub-threshold player traded mid-season: 1 shot for their OLD team (LV,
    inserted first — so a `rows[0]`-based team pick would wrongly report LV)
    and 3 shots for their CURRENT team (NY, the plurality). Proves the page's
    team attribution is deterministic by shot count, not insertion order."""
    session = get_session()
    q.upsert_shots(
        session,
        "g3",
        2026,
        [
            _shot("t1", "p-traded", "Traded Player", "3", "LV", x=25, y=1),
            _shot("t2", "p-traded", "Traded Player", "1", "NY", x=25, y=1),
            _shot("t3", "p-traded", "Traded Player", "1", "NY", x=26, y=1),
            _shot("t4", "p-traded", "Traded Player", "1", "NY", x=27, y=1),
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
    assert "points added" in body and "#1 of 2" in body
    assert 'og:image" content="' in body and "/player/p-qual/og.png" in body
    assert 'type="application/json" id="shot-data"' in body

    blob = re.search(r'id="shot-data">(.*?)</script>', body, re.S).group(1)
    parsed = json.loads(blob)
    assert isinstance(parsed, list) and len(parsed) == 2

    assert 'aria-current="page"' in body and "/shot-making" in body


def test_player_page_style_tags_are_balanced(client, env):
    """Regression: player.html must NOT open its own <style> after
    {{ shared_head }} — the shared head opens one and leaves it open for the
    page to close. A stray nested <style> corrupts CSS parsing; the browser
    error-recovers by dropping the first rule (`main`, the layout container),
    which made the whole page render full-width and left-pinned. The
    structural invariant is balanced style tags + the `main` rule surviving."""
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    _seed_qualified(env)

    body = client.get("/player/p-qual").text
    assert body.count("<style") == body.count("</style")
    assert "main { max-width: 760px" in body


def test_player_page_sub_threshold(client, env):
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    _seed_sub_threshold(env)

    r = client.get("/player/p-sub")
    assert r.status_code == 200
    assert "Not yet ranked on the shot-making leaderboard" in r.text
    assert "shot-making rank" not in r.text


def test_player_page_sub_threshold_team_is_deterministic_plurality(client, env):
    """get_shots_for_player has no ORDER BY, so the sub-threshold branch must
    NOT report `rows[0].team_abbr` (nondeterministic for a traded player) —
    it must report the team the player took the most shots for. The seed puts
    the minority team (LV, 1 shot) FIRST in insertion order and the majority
    team (NY, 3 shots) after, so a `rows[0]`-based bug would show LV."""
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    _seed_sub_threshold_traded(env)

    r = client.get("/player/p-traded")
    assert r.status_code == 200
    sub = re.search(r'<p class="player-sub">(.*?)</p>', r.text, re.S).group(1)
    assert "NY" in sub
    assert "LV" not in sub


def test_player_page_unknown_404(client, env):
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    assert client.get("/player/nobody").status_code == 404


def test_player_page_renders_the_bridge_when_anchors_exist(client, env):
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    _seed_qualified(env)
    session = get_session()
    q.upsert_shot_league_avg(session, 2026, avg_xpps=1.027, avg_pps=1.037, fga=25790)
    session.close()

    html = client.get("/player/p-qual").text
    assert "How she scores" in html
    assert "bridge-seg is-diet" in html
    assert "in points per shot" in html


def test_player_page_omits_the_bridge_before_the_first_daily_run(client, env):
    from src.api import app as app_module

    app_module._shot_baseline_cache = None
    _seed_qualified(env)  # no anchor row seeded

    html = client.get("/player/p-qual").text
    assert "How she scores" not in html
    # the rest of the page is unaffected
    assert "shot-chart" in html
