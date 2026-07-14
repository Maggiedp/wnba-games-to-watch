"""Tests for the excitement batch recompute (backfill_excitement --recompute).

Query + refresh + script-level coverage lives together here, mirroring
tests/test_backfill_game_shapes.py. Uses the shared `env` fixture.
"""

from datetime import datetime, timedelta

from src.db.queries import get_games_for_excitement_refresh


def _seed_game(
    env,
    session,
    espn_id,
    excitement_index=None,
    excitement_computed_at=None,
    date="2026-05-10",
):
    """Completed 2026 game row with optional stored excitement state."""
    game = env.Game(
        team_a_id=1,
        team_b_id=2,
        date=date,
        time="",
        broadcaster="ION",
        winner_id=1,
        final_score_a=80,
        final_score_b=70,
        espn_id=espn_id,
        excitement_index=excitement_index,
        excitement_computed_at=excitement_computed_at,
    )
    session.add(game)
    return game


def test_refresh_query_cutoff_none_selects_all_stored_rows(env):
    """cutoff=None must reach every stored excitement value: legacy rows
    (excitement_computed_at NULL — the column shipped 2026-05-16 with no
    backfill) AND rows far older than any daily window. Rows with no
    stored excitement, or no espn_id, stay excluded."""
    session = env.get_session()
    try:
        now = datetime.now()
        _seed_game(env, session, "legacy", excitement_index=5.0)  # computed_at NULL
        _seed_game(
            env,
            session,
            "old",
            excitement_index=6.0,
            excitement_computed_at=now - timedelta(days=30),
        )
        _seed_game(
            env,
            session,
            "recent",
            excitement_index=7.0,
            excitement_computed_at=now - timedelta(days=1),
        )
        _seed_game(env, session, "unscored")  # excitement NULL -> never eligible
        _seed_game(
            env,
            session,
            None,
            excitement_index=4.0,
            excitement_computed_at=now - timedelta(days=1),
        )  # no espn_id -> can never be refreshed
        session.commit()

        got = get_games_for_excitement_refresh(session, cutoff=None)
        assert {g.espn_id for g in got} == {"legacy", "old", "recent"}
    finally:
        session.close()


def test_refresh_query_dated_cutoff_unchanged(env):
    """A dated cutoff keeps the existing bounded-window semantics: only rows
    computed inside the window, and legacy NULL-computed_at rows stay out."""
    session = env.get_session()
    try:
        now = datetime.now()
        _seed_game(env, session, "legacy", excitement_index=5.0)
        _seed_game(
            env,
            session,
            "old",
            excitement_index=6.0,
            excitement_computed_at=now - timedelta(days=30),
        )
        _seed_game(
            env,
            session,
            "recent",
            excitement_index=7.0,
            excitement_computed_at=now - timedelta(days=1),
        )
        session.commit()

        got = get_games_for_excitement_refresh(session, cutoff=now - timedelta(days=2))
        assert {g.espn_id for g in got} == {"recent"}
    finally:
        session.close()
