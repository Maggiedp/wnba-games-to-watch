from src.data.espn_api import today_et
from src.db.queries import upsert_game_shape


def _seed(env, espn_id, season, date, excitement=5.0):
    session = env.get_session()
    upsert_game_shape(
        session,
        espn_id=espn_id,
        season=season,
        date=date,
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=86,
        winner="home",
        excitement=excitement,
        tension=0.5,
        comeback=0.1,
        lead_changes=4,
        winner_low_wp=0.4,
        curve=[[0.0, 0.5], [2400.0, 0.9]],
    )
    session.close()


def test_replay_returns_seasons_games_and_curve(env, client):
    _seed(env, "a", 2026, "2026-06-01", excitement=3.0)
    _seed(env, "b", 2026, "2026-08-15", excitement=9.0)
    _seed(env, "c", 2025, "2025-07-01")
    r = client.get("/api/replay?season=2026")
    assert r.status_code == 200
    data = r.json()
    assert data["season"] == 2026
    assert data["seasons"] == [2026, 2025]
    assert len(data["games"]) == 2
    g = data["games"][0]
    assert {
        "espn_id",
        "winner",
        "excitement",
        "tension",
        "comeback",
        "lead_changes",
        "winner_low_wp",
        "curve",
        "home_abbr",
    } <= g.keys()
    assert isinstance(g["curve"], list) and g["curve"][0] == [0.0, 0.5]


def test_replay_defaults_to_current_season(env, client):
    season = int(today_et()[:4])
    _seed(env, "x", season, f"{season}-06-01")
    r = client.get("/api/replay")
    assert r.status_code == 200
    assert r.json()["season"] == season
    assert len(r.json()["games"]) == 1


def test_replay_empty_season_returns_empty_games(env, client):
    r = client.get("/api/replay?season=2024")
    assert r.status_code == 200
    assert r.json()["games"] == []
