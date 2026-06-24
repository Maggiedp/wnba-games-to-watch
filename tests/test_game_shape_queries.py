from src.db.queries import (
    get_completed_games_missing_shape,
    get_existing_shape_espn_ids,
    get_team_abbrev_map,
    upsert_game,
    upsert_game_shape,
    upsert_team,
)


def _seed_shape(env, espn_id="401", season=2026):
    session = env.get_session()
    upsert_game_shape(
        session,
        espn_id=espn_id,
        season=season,
        date=f"{season}-08-15",
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=86,
        winner="home",
        excitement=9.4,
        tension=0.87,
        comeback=0.31,
        lead_changes=9,
        winner_low_wp=0.33,
        curve=[[0.0, 0.5], [2400.0, 0.85]],
    )
    session.commit()
    return session


def test_upsert_game_shape_is_idempotent(env):
    session = _seed_shape(env)
    # second upsert updates in place, not a duplicate row
    upsert_game_shape(
        session,
        espn_id="401",
        season=2026,
        date="2026-08-15",
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=90,
        away_score=80,
        winner="home",
        excitement=2.0,
        tension=0.1,
        comeback=0.0,
        lead_changes=0,
        winner_low_wp=0.55,
        curve=[[0.0, 0.6]],
    )
    session.commit()
    rows = session.query(env.GameShape).filter_by(espn_id="401").all()
    assert len(rows) == 1
    assert rows[0].excitement == 2.0
    assert rows[0].curve == "[[0.0, 0.6]]"
    session.close()


def test_get_existing_shape_espn_ids(env):
    session = _seed_shape(env)
    assert get_existing_shape_espn_ids(session, ["401", "999"]) == {"401"}
    assert get_existing_shape_espn_ids(session, []) == set()
    session.close()


def test_get_team_abbrev_map(env):
    session = env.get_session()
    upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    session.commit()
    assert get_team_abbrev_map(session)["Las Vegas Aces"] == "LV"
    session.close()


def test_get_completed_games_missing_shape_excludes_shaped(env):
    session = env.get_session()
    a = upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    b = upsert_team(session, name="New York Liberty", bpi_rating=0.0, abbreviation="NY")
    session.commit()
    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date="2026-08-15",
        time="",
        broadcaster="",
        espn_id="401",
        winner_id=a.id,
        season_type=2,
    )
    session.commit()
    # missing a shape → returned
    missing = get_completed_games_missing_shape(session)
    assert [g.espn_id for g in missing] == ["401"]
    # once shaped → excluded
    upsert_game_shape(
        session,
        espn_id="401",
        season=2026,
        date="2026-08-15",
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=86,
        winner="home",
        excitement=9.4,
        tension=0.87,
        comeback=0.31,
        lead_changes=9,
        winner_low_wp=0.33,
        curve=[[0.0, 0.5]],
    )
    session.commit()
    assert get_completed_games_missing_shape(session) == []
    session.close()


def test_get_completed_games_missing_shape_orders_oldest_first(env):
    session = env.get_session()
    a = upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    b = upsert_team(session, name="New York Liberty", bpi_rating=0.0, abbreviation="NY")
    session.commit()
    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date="2026-06-10",
        time="",
        broadcaster="",
        espn_id="older",
        winner_id=a.id,
        season_type=2,
    )
    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date="2026-08-15",
        time="",
        broadcaster="",
        espn_id="newer",
        winner_id=a.id,
        season_type=2,
    )
    session.commit()
    # oldest-first so transient-failing recent games can't starve older rows
    assert [g.espn_id for g in get_completed_games_missing_shape(session)] == [
        "older",
        "newer",
    ]
    session.close()
