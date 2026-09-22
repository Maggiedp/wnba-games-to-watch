"""Tests for the schedule-completeness checker."""

from src.db.queries import upsert_game, upsert_team


def _seed(session, rows, teams):
    ids = {
        n: upsert_team(
            session, name=n, abbreviation=n[:3].upper(), logo_url="", bpi_rating=0.0
        ).id
        for n in teams
    }
    for a, b, date, winner, season_type in rows:
        upsert_game(
            session,
            team_a_id=ids[a],
            team_b_id=ids[b],
            date=date,
            time="",
            broadcaster="",
            winner_id=ids[winner] if winner else None,
            season_type=season_type,
            espn_id=f"{a[:3]}{b[:3]}{date}",
        )
    session.commit()
    return ids


TEAMS = ["Washington Mystics", "Indiana Fever"]


def _run(env, monkeypatch, expected):
    import sys

    from scripts import check_schedule_completeness as mod

    monkeypatch.setattr(mod, "today_et", lambda: "2026-09-22")
    monkeypatch.setattr(
        sys, "argv", ["x", "--season", "2026", "--expected", str(expected)]
    )
    return mod.main()


def test_complete_season_passes(env, monkeypatch):
    session = env.get_session()
    _seed(
        session,
        [
            (
                "Washington Mystics",
                "Indiana Fever",
                "2026-09-10",
                "Washington Mystics",
                2,
            ),
            ("Washington Mystics", "Indiana Fever", "2026-09-30", None, 2),
        ],
        TEAMS,
    )
    session.close()
    assert _run(env, monkeypatch, expected=2) == 0


def test_past_game_without_a_winner_is_counted_by_neither_side(env, monkeypatch):
    """The 2026-09-16 shape: a result that never landed. Not in standings (no
    winner), not in the remaining schedule (date has passed) — the team's season
    is a game short and the sim can never move it."""
    session = env.get_session()
    _seed(
        session,
        [
            (
                "Washington Mystics",
                "Indiana Fever",
                "2026-09-10",
                "Washington Mystics",
                2,
            ),
            ("Washington Mystics", "Indiana Fever", "2026-09-16", None, 2),
        ],
        TEAMS,
    )
    session.close()
    assert _run(env, monkeypatch, expected=2) == 1


def test_completed_game_with_null_season_type_is_dropped_from_standings(
    env, monkeypatch
):
    """compute_standings skips a completed row whose season_type backfill never
    landed, and it has a winner so it is not remaining either."""
    session = env.get_session()
    _seed(
        session,
        [
            (
                "Washington Mystics",
                "Indiana Fever",
                "2026-09-10",
                "Washington Mystics",
                2,
            ),
            (
                "Washington Mystics",
                "Indiana Fever",
                "2026-09-12",
                "Indiana Fever",
                None,
            ),
            ("Washington Mystics", "Indiana Fever", "2026-09-30", None, 2),
        ],
        TEAMS,
    )
    session.close()
    assert _run(env, monkeypatch, expected=3) == 1
