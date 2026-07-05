from src.db.queries import (
    get_team_style_seasons,
    get_team_styles,
    upsert_team_style,
    upsert_team,
)


def test_upsert_and_read_team_style(env):
    session = env.get_session()
    try:
        team = upsert_team(session, "New York Liberty", 3.0, abbreviation="NY")
        upsert_team_style(
            session,
            season=2026,
            team_id=team.id,
            pace=84.0,
            three_pa_rate=0.34,
            ft_rate=0.22,
            oreb_pct=27.5,
            assist_rate=0.62,
            def_pressure=0.15,
            games_played=30,
        )
        # Idempotent: second upsert updates, does not duplicate.
        upsert_team_style(
            session,
            season=2026,
            team_id=team.id,
            pace=85.0,
            three_pa_rate=0.34,
            ft_rate=0.22,
            oreb_pct=27.5,
            assist_rate=0.62,
            def_pressure=0.15,
            games_played=31,
        )
        rows = get_team_styles(session, 2026)
        assert len(rows) == 1
        assert rows[0].pace == 85.0
        assert rows[0].games_played == 31
        assert get_team_style_seasons(session) == [2026]
    finally:
        session.close()
