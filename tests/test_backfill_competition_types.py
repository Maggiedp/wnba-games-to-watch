"""The self-healing backfill for Game.competition_type.

Without it the fix depends on an operator remembering to run
scripts/refetch_games.py for 2026-06-30, and a forgotten manual step leaves
production wrong on the exact case the change exists to fix.
"""

from src.constants import CURRENT_SEASON
from src.db.queries import upsert_game, upsert_team


def _seed(session):
    a = upsert_team(session, "Team A", bpi_rating=1.0, abbreviation="AAA")
    b = upsert_team(session, "Team B", bpi_rating=1.0, abbreviation="BBB")
    session.commit()
    return a, b


def test_backfill_populates_competition_type_for_rows_outside_the_daily_window(
    env, monkeypatch
):
    import scripts.daily_update as du

    session = env.get_session()
    a, b = _seed(session)
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
        espn_id="401857321",
        season_type=2,
    )
    session.commit()

    monkeypatch.setattr(
        du,
        "fetch_games_for_range",
        lambda start, end: [
            {"event_id": "401857321", "season_type": 2, "competition_type": "CC"}
        ],
    )

    du.backfill_missing_competition_types(session)

    from src.db.schema import Game

    stored = session.query(Game).filter(Game.espn_id == "401857321").one()
    assert stored.competition_type == "CC"


def test_backfill_is_probed_on_its_own_column_not_season_type(env, monkeypatch):
    """It must run even when every row already has season_type.

    `backfill_missing_season_types` short-circuits on `season_type IS NULL`,
    which is 0 once its own backfill has drained — so extending that function
    would silently never run and leave competition_type NULL forever.
    """
    import scripts.daily_update as du

    session = env.get_session()
    a, b = _seed(session)
    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date=f"{CURRENT_SEASON}-06-30",
        time="7:00 PM ET",
        broadcaster="ESPN",
        winner_id=a.id,
        espn_id="401857321",
        season_type=2,  # already classified — the sibling backfill would skip
    )
    session.commit()

    called = {"n": 0}

    def fake_fetch(start, end):
        called["n"] += 1
        return [{"event_id": "401857321", "season_type": 2, "competition_type": "CC"}]

    monkeypatch.setattr(du, "fetch_games_for_range", fake_fetch)

    du.backfill_missing_competition_types(session)

    assert called["n"] == 1


def test_backfill_short_circuits_once_every_row_is_populated(env, monkeypatch):
    """Idempotent: a second run must not pay the full-season ESPN fetch."""
    import scripts.daily_update as du

    session = env.get_session()
    a, b = _seed(session)
    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date=f"{CURRENT_SEASON}-06-30",
        time="7:00 PM ET",
        broadcaster="ESPN",
        winner_id=a.id,
        espn_id="401857321",
        season_type=2,
        competition_type="CC",
    )
    session.commit()

    def must_not_fetch(start, end):
        raise AssertionError("short-circuit failed — ESPN must not be called")

    monkeypatch.setattr(du, "fetch_games_for_range", must_not_fetch)

    du.backfill_missing_competition_types(session)
