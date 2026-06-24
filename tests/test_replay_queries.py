from src.db.queries import get_game_shapes, get_shape_seasons, upsert_game_shape


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


def test_get_game_shapes_filters_by_season_newest_first(env):
    _seed(env, "a", 2026, "2026-06-01")
    _seed(env, "b", 2026, "2026-08-15")
    _seed(env, "c", 2025, "2025-07-01")
    session = env.get_session()
    rows = get_game_shapes(session, 2026)
    assert [r.espn_id for r in rows] == ["b", "a"]  # date desc
    assert [r.espn_id for r in get_game_shapes(session, 2025)] == ["c"]
    assert get_game_shapes(session, 2024) == []
    session.close()


def test_get_shape_seasons_distinct_desc(env):
    _seed(env, "a", 2026, "2026-06-01")
    _seed(env, "b", 2026, "2026-08-15")
    _seed(env, "c", 2025, "2025-07-01")
    session = env.get_session()
    assert get_shape_seasons(session) == [2026, 2025]
    session.close()
