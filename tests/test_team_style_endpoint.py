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


def _seed_style(env, season=2026):
    from src.db.queries import upsert_team_style

    session = env.get_session()
    try:
        specs = [
            ("New York Liberty", "NY", 95.0, 0.45),
            ("Las Vegas Aces", "LV", 82.0, 0.31),
            ("Minnesota Lynx", "MIN", 83.0, 0.32),
            ("Seattle Storm", "SEA", 70.0, 0.22),
        ]
        for name, abbr, pace, tpa in specs:
            t = upsert_team(session, name, 1.0, abbreviation=abbr)
            upsert_team_style(
                session,
                season=season,
                team_id=t.id,
                pace=pace,
                three_pa_rate=tpa,
                ft_rate=0.20,
                oreb_pct=25.0,
                assist_rate=0.55,
                def_pressure=0.14,
                games_played=30,
            )
    finally:
        session.close()


def test_team_style_endpoint_shape(client, env):
    _seed_style(env)
    r = client.get("/api/team-style")
    assert r.status_code == 200
    data = r.json()
    assert data["season"] == 2026
    assert len(data["teams"]) == 4
    ny = next(t for t in data["teams"] if t["abbr"] == "NY")
    assert ny["low_confidence"] is False
    assert len(ny["axes"]) == 6
    assert ny["descriptor"]
    assert ny["plays_like"]
    # W-L attached on a today (default) request even with no games -> 0-0.
    assert ny["wins"] == 0 and ny["losses"] == 0


def test_team_style_endpoint_empty_when_no_data(client, env):
    r = client.get("/api/team-style")
    assert r.status_code == 200
    assert r.json()["teams"] == []


def test_style_page_renders_with_nav(client):
    r = client.get("/style")
    assert r.status_code == 200
    body = r.text
    assert "Team Style" in body
    assert 'id="style-grid"' in body
    # Shared top nav present and links to the new page.
    assert 'href="/style"' in body
