"""Tests for compute_standings query optimization.

Tests that the function doesn't issue per-game team lookups (N+1 query problem).
"""

from src.constants import CURRENT_SEASON
from src.db.queries import upsert_game, upsert_team


def test_compute_standings_query_count_is_constant(env):
    """compute_standings must not issue per-game team lookups: it already has
    every team in memory from get_all_teams. Pins the N+1 fix — the count must
    not grow with the number of completed games."""
    from sqlalchemy import event

    from src.scoring.sim_inputs import compute_standings

    session = env.get_session()

    # Create two teams.
    a = upsert_team(session, "Team A", bpi_rating=1.0, abbreviation="AAA")
    b = upsert_team(session, "Team B", bpi_rating=1.0, abbreviation="BBB")
    session.commit()

    counter = {"n": 0}
    engine = session.get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*args, **kwargs):
        counter["n"] += 1

    try:
        # Baseline: compute standings with no completed games.
        compute_standings(session, {})
        baseline = counter["n"]

        # Seed 10 completed regular-season games.
        for i in range(10):
            upsert_game(
                session,
                team_a_id=a.id,
                team_b_id=b.id,
                date=f"{CURRENT_SEASON}-06-{i + 1:02d}",
                time="7:00 PM ET",
                broadcaster="ESPN",
                winner_id=a.id,
                final_score_a=80,
                final_score_b=70,
                espn_id=f"G{i}",
                season_type=2,
            )
        session.commit()

        # Measure query count with 10 completed games.
        counter["n"] = 0
        compute_standings(session, {})
        with_ten_games = counter["n"]
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert with_ten_games <= baseline + 1, (
        f"query count grew with game count ({baseline} -> {with_ten_games}); "
        "compute_standings is issuing per-game lookups again"
    )


def test_compute_standings_excludes_non_standings_competition_types(env):
    """A Commissioner's Cup result must not reach the simulation's standings.

    This is the path that actually decides seeding: `compute_standings` feeds
    the Monte Carlo for both the daily run and the live overlay. Excluding the
    Cup from the displayed W-L record but not from here would leave the
    published seeding probabilities exactly as wrong as before.
    """
    from src.scoring.sim_inputs import compute_standings

    session = env.get_session()
    a = upsert_team(session, "Team A", bpi_rating=1.0, abbreviation="AAA")
    b = upsert_team(session, "Team B", bpi_rating=1.0, abbreviation="BBB")
    session.commit()

    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date=f"{CURRENT_SEASON}-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        winner_id=a.id,
        final_score_a=80,
        final_score_b=70,
        espn_id="STD1",
        season_type=2,
        competition_type="STD",
    )
    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date=f"{CURRENT_SEASON}-06-30",
        time="7:00 PM ET",
        broadcaster="ESPN",
        winner_id=a.id,
        final_score_a=93,
        final_score_b=85,
        espn_id="CUP1",
        season_type=2,
        competition_type="CC",
    )
    session.commit()

    standings = compute_standings(session, {})

    assert (standings["Team A"]["wins"], standings["Team A"]["losses"]) == (1, 0)
    assert (standings["Team B"]["wins"], standings["Team B"]["losses"]) == (0, 1)


def test_compute_standings_excludes_non_standings_games_from_head_to_head(env):
    """The tiebreaker record must not count the Cup either.

    `resolve_seeding` breaks ties on h2h, so a phantom Cup win is not just a
    record cosmetic — it can hand a team a seed it did not earn.
    """
    from src.scoring.sim_inputs import compute_standings

    session = env.get_session()
    a = upsert_team(session, "Team A", bpi_rating=1.0, abbreviation="AAA")
    b = upsert_team(session, "Team B", bpi_rating=1.0, abbreviation="BBB")
    session.commit()

    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date=f"{CURRENT_SEASON}-06-30",
        time="7:00 PM ET",
        broadcaster="ESPN",
        winner_id=a.id,
        final_score_a=93,
        final_score_b=85,
        espn_id="CUP1",
        season_type=2,
        competition_type="CC",
    )
    session.commit()

    standings = compute_standings(session, {})

    assert standings["Team A"]["h2h"] == {}
    assert standings["Team B"]["h2h"] == {}
