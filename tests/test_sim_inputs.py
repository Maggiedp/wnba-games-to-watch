"""build_sim_inputs assembles every Monte Carlo input from the DB alone."""

from src.constants import CURRENT_SEASON


def test_build_sim_inputs_reads_elo_from_teams_and_indexes_remaining_by_espn_id(env):
    from src.db.queries import upsert_team, upsert_game, set_team_elo_ratings
    from src.scoring.sim_inputs import build_sim_inputs

    session = env.get_session()
    a = upsert_team(session, "Minnesota Lynx", bpi_rating=1.0, abbreviation="MIN")
    b = upsert_team(session, "Indiana Fever", bpi_rating=2.0, abbreviation="IND")
    set_team_elo_ratings(session, {"Minnesota Lynx": 1600.0, "Indiana Fever": 1400.0})

    upsert_game(
        session,
        a.id,
        b.id,
        f"{CURRENT_SEASON}-09-20",
        "19:00",
        "ESPN",
        espn_id="401999001",
        season_type=2,
    )

    inputs = build_sim_inputs(session, f"{CURRENT_SEASON}-09-20")

    assert inputs.standings["Minnesota Lynx"]["elo"] == 1600.0
    assert inputs.standings["Indiana Fever"]["elo"] == 1400.0
    assert inputs.remaining_games == [("Minnesota Lynx", "Indiana Fever")]
    assert inputs.remaining_index_by_espn_id == {"401999001": 0}
    assert inputs.elo_populated is True


def test_build_sim_inputs_reports_unpopulated_elo(env):
    """Between deploy and the first daily run, Team.elo_rating is NULL. Falling
    back to INITIAL_RATING would make every team equally strong and publish
    nonsense live odds, so the caller must be able to detect it and stand down."""
    from src.db.queries import upsert_team, upsert_game, set_team_elo_ratings
    from src.scoring.sim_inputs import build_sim_inputs

    session = env.get_session()
    a = upsert_team(session, "Minnesota Lynx", bpi_rating=1.0, abbreviation="MIN")
    b = upsert_team(session, "Indiana Fever", bpi_rating=2.0, abbreviation="IND")
    set_team_elo_ratings(session, {"Minnesota Lynx": 1600.0})  # Indiana Fever left NULL

    upsert_game(
        session,
        a.id,
        b.id,
        f"{CURRENT_SEASON}-09-20",
        "19:00",
        "ESPN",
        espn_id="401999004",
        season_type=2,
    )

    inputs = build_sim_inputs(session, f"{CURRENT_SEASON}-09-20")

    assert inputs.elo_populated is False
    # Still usable by the daily run, which passes its own freshly replayed Elo.
    assert inputs.standings["Indiana Fever"]["elo"] is not None


def test_build_sim_inputs_excludes_postseason_games_from_remaining(env):
    from src.db.queries import upsert_team, upsert_game, set_team_elo_ratings
    from src.scoring.sim_inputs import build_sim_inputs

    session = env.get_session()
    a = upsert_team(session, "Minnesota Lynx", bpi_rating=1.0, abbreviation="MIN")
    b = upsert_team(session, "Indiana Fever", bpi_rating=2.0, abbreviation="IND")
    set_team_elo_ratings(session, {"Minnesota Lynx": 1500.0, "Indiana Fever": 1500.0})

    upsert_game(
        session,
        a.id,
        b.id,
        f"{CURRENT_SEASON}-09-20",
        "19:00",
        "ESPN",
        espn_id="401999002",
        season_type=2,
    )
    upsert_game(
        session,
        a.id,
        b.id,
        f"{CURRENT_SEASON}-09-28",
        "19:00",
        "ESPN",
        espn_id="401999003",
        season_type=3,
    )

    inputs = build_sim_inputs(session, f"{CURRENT_SEASON}-09-20")

    # Only regular-season games drive seeding; the bracket sim handles the rest.
    assert inputs.remaining_index_by_espn_id == {"401999002": 0}


def test_build_sim_inputs_excludes_an_unplayed_non_standings_game_from_remaining(env):
    """A scheduled-but-unplayed Commissioner's Cup final is not a seeding game.

    It is `season_type == 2` and has no winner, so it looks exactly like a
    regular-season fixture. Simulating it would award a win that the real
    standings never record — live every season from the schedule dropping
    until the Cup final is played.
    """
    from src.db.queries import set_team_elo_ratings, upsert_game, upsert_team
    from src.scoring.sim_inputs import build_sim_inputs

    session = env.get_session()
    a = upsert_team(session, "Minnesota Lynx", bpi_rating=1.0, abbreviation="MIN")
    b = upsert_team(session, "Indiana Fever", bpi_rating=2.0, abbreviation="IND")
    set_team_elo_ratings(session, {"Minnesota Lynx": 1500.0, "Indiana Fever": 1500.0})

    upsert_game(
        session,
        a.id,
        b.id,
        f"{CURRENT_SEASON}-06-20",
        "19:00",
        "ESPN",
        espn_id="401999004",
        season_type=2,
        competition_type="STD",
    )
    upsert_game(
        session,
        a.id,
        b.id,
        f"{CURRENT_SEASON}-06-30",
        "19:00",
        "ESPN",
        espn_id="401999005",
        season_type=2,
        competition_type="CC",
    )

    inputs = build_sim_inputs(session, f"{CURRENT_SEASON}-06-20")

    assert inputs.remaining_index_by_espn_id == {"401999004": 0}
    assert inputs.remaining_games == [("Minnesota Lynx", "Indiana Fever")]
