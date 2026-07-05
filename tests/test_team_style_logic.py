from src.db.queries import (
    get_team_style_seasons,
    get_team_styles,
    upsert_team_style,
    upsert_team,
)

from src.scoring.team_style import AXES, compute_style_view


def _row(team, abbr, gp, **metrics):
    base = dict(
        team_id=abs(hash(team)) % 100000,
        team=team,
        abbr=abbr,
        pace=80.0,
        three_pa_rate=0.30,
        ft_rate=0.20,
        oreb_pct=25.0,
        assist_rate=0.55,
        def_pressure=0.14,
        games_played=gp,
    )
    base.update(metrics)
    return base


def test_compute_style_view_ranks_neighbors_descriptor():
    rows = [
        _row("Fast Team", "FAS", 30, pace=95.0, three_pa_rate=0.45),
        _row("Slow Team", "SLO", 30, pace=70.0, three_pa_rate=0.22),
        _row("Mid A", "MDA", 30, pace=82.0, three_pa_rate=0.31),
        _row("Mid B", "MDB", 30, pace=83.0, three_pa_rate=0.32),
    ]
    view = {t["team"]: t for t in compute_style_view(rows)}

    fast = view["Fast Team"]
    assert fast["low_confidence"] is False
    assert [a["key"] for a in fast["axes"]] == [k for k, _ in AXES]
    pace_axis = next(a for a in fast["axes"] if a["key"] == "pace")
    assert pace_axis["rank"] == 1 and pace_axis["of"] == 4
    assert pace_axis["norm"] == 100.0
    assert fast["descriptor"][0] == "Up-tempo"
    assert fast["plays_like"][0]["abbr"] in {"MDA", "MDB"}
    assert len(fast["chips"]) == 2


def test_compute_style_view_low_confidence_gate():
    rows = [_row("Rookie", "ROO", 2), _row("Vet", "VET", 30)]
    view = {t["team"]: t for t in compute_style_view(rows)}
    assert view["Rookie"]["low_confidence"] is True
    assert view["Rookie"]["axes"] == []
    assert view["Vet"]["low_confidence"] is True


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
