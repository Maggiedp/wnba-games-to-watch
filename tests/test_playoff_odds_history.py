from src.db.queries import (
    get_playoff_probability_history,
    upsert_playoff_probability,
    upsert_team,
)


def _seed(schema):
    session = schema.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0)
    a = session.query(schema.Team).filter_by(name="Aces").one().id
    b = session.query(schema.Team).filter_by(name="Storm").one().id
    upsert_playoff_probability(session, "2026-05-10", a, 0.80)
    upsert_playoff_probability(session, "2026-05-10", b, 0.40)
    upsert_playoff_probability(session, "2026-05-15", a, 0.85)
    upsert_playoff_probability(session, "2025-09-01", a, 0.10)  # other season
    session.close()
    return a, b


def test_history_filters_season_and_orders_by_date_then_team(env):
    a, b = _seed(env)
    session = env.get_session()
    rows = get_playoff_probability_history(session, 2026)
    session.close()
    assert [(r.date, r.team_id, r.probability) for r in rows] == [
        ("2026-05-10", a, 0.80),
        ("2026-05-10", b, 0.40),
        ("2026-05-15", a, 0.85),
    ]


def test_history_empty_season_returns_empty_list(env):
    _seed(env)
    session = env.get_session()
    rows = get_playoff_probability_history(session, 1999)
    session.close()
    assert rows == []


def test_history_endpoint_shape(env, client):
    _seed(env)
    r = client.get("/api/playoff-odds-history?season=2026")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == 2026
    assert body["teams"]["Aces"] == [
        {"date": "2026-05-10", "value": 0.80},
        {"date": "2026-05-15", "value": 0.85},
    ]
    assert body["teams"]["Storm"] == [{"date": "2026-05-10", "value": 0.40}]
    assert body["abbrevs"]["Aces"] == "LV"


def test_history_endpoint_empty_season(client):
    r = client.get("/api/playoff-odds-history?season=1999")
    assert r.status_code == 200
    assert r.json() == {"season": 1999, "teams": {}}
