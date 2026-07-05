from unittest.mock import patch

from src.db.queries import get_team_styles, upsert_team


def test_populate_team_style_upserts_rows(env):
    from scripts import daily_update

    session = env.get_session()
    try:
        upsert_team(session, "New York Liberty", 3.0, abbreviation="NY")
        upsert_team(session, "Las Vegas Aces", 4.0, abbreviation="LV")
        fake = [
            {
                "team": "New York Liberty",
                "pace": 84.0,
                "three_pa_rate": 0.34,
                "ft_rate": 0.22,
                "oreb_pct": 27.0,
                "assist_rate": 0.62,
                "def_pressure": 0.15,
                "games_played": 30,
            },
            {
                "team": "Unknown Team",
                "pace": 80.0,
                "three_pa_rate": 0.30,
                "ft_rate": 0.20,
                "oreb_pct": 25.0,
                "assist_rate": 0.55,
                "def_pressure": 0.14,
                "games_played": 30,
            },
        ]
        with patch.object(daily_update, "fetch_team_style_stats", return_value=fake):
            daily_update.populate_team_style(session, 2026)
        rows = get_team_styles(session, 2026)
        # Only the resolvable team is stored; the unknown name is skipped.
        assert len(rows) == 1
        assert rows[0].pace == 84.0
    finally:
        session.close()


def test_populate_team_style_nonfatal_on_fetch_error(env):
    """A fetch failure must not raise — the daily job continues (returns 0)."""
    from scripts import daily_update

    session = env.get_session()
    try:
        with patch.object(
            daily_update,
            "fetch_team_style_stats",
            side_effect=RuntimeError("ESPN down"),
        ):
            assert daily_update.populate_team_style(session, 2026) == 0
    finally:
        session.close()
