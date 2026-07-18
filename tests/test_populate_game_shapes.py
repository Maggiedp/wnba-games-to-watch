from src.constants import GameStatus
from src.db.queries import upsert_game, upsert_team


def test_populate_game_shapes_stores_row(env, monkeypatch, wp_plays):
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

    fake = {
        "status": GameStatus.FINAL,
        "home_team": "Las Vegas Aces",
        "away_team": "New York Liberty",
        "home_score": "88",
        "away_score": "86",
        "plays": wp_plays([0.55, 0.20, 0.85]),
    }
    import scripts.daily_update as du

    monkeypatch.setattr(du, "fetch_live_win_probability", lambda *a, **k: fake)

    du.populate_game_shapes_for_recent_completions(session, limit=None, timeout=10)

    row = session.query(env.GameShape).filter_by(espn_id="401").one()
    assert row.winner == "home"
    assert row.home_abbr == "LV"
    assert row.comeback > 0
    assert row.season == 2026
    session.close()
