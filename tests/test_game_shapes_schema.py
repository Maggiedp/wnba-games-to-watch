def test_game_shapes_table_roundtrip(env):
    session = env.get_session()
    row = env.GameShape(
        espn_id="401620366",
        season=2024,
        date="2024-08-15",
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
        curve="[[0.0,0.5],[2400.0,0.85]]",
    )
    session.add(row)
    session.commit()
    got = session.query(env.GameShape).filter_by(espn_id="401620366").one()
    assert got.season == 2024
    assert got.winner == "home"
    assert got.lead_changes == 9
    session.close()
