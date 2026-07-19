from datetime import date

from src.constants import GameStatus
from src.db.queries import upsert_game_shape, upsert_team


def _seed_stale_shape(session, espn_id="STALE"):
    """Seed an existing game_shapes row so a failed recompute leaves it stale."""
    upsert_game_shape(
        session,
        espn_id=espn_id,
        season=2024,
        date="2024-08-15",
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=86,
        winner="home",
        excitement=9.0,
        tension=0.8,
        comeback=0.3,
        lead_changes=5,
        winner_low_wp=0.3,
        curve=[[0.0, 0.5], [1.0, 0.9]],
    )


def test_backfill_stores_completed_and_skips_existing(env, monkeypatch, wp_plays):
    session = env.get_session()
    upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    upsert_team(session, name="New York Liberty", bpi_rating=0.0, abbreviation="NY")
    session.commit()

    import scripts.backfill_game_shapes as bf
    import scripts.daily_update as du

    events = [
        {
            "event_id": "24A",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        },
        {
            "event_id": "",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        },  # no event_id -> skipped
    ]
    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda s, e, failed_windows=None: events
    )

    def fake_wp(espn_id, timeout=10):
        return {
            "status": GameStatus.FINAL,
            "home_team": "Las Vegas Aces",
            "away_team": "New York Liberty",
            "home_score": "80",
            "away_score": "70",
            "plays": wp_plays([0.55, 0.9]),
        }

    # _build_and_store_shape (imported from daily_update) resolves
    # fetch_live_win_probability in the daily_update module's namespace, so the
    # stub must be applied there — patching bf would not intercept it.
    monkeypatch.setattr(du, "fetch_live_win_probability", fake_wp)

    n = bf.backfill_range(session, date(2024, 5, 1), date(2024, 10, 31))
    assert n == 1  # only the event with a usable event_id + final + plays
    assert session.query(env.GameShape).filter_by(espn_id="24A").count() == 1

    # re-running stores nothing new (idempotent skip)
    assert bf.backfill_range(session, date(2024, 5, 1), date(2024, 10, 31)) == 0
    session.close()


def test_backfill_main_fails_closed_on_skipped_window(env, monkeypatch):
    import scripts.backfill_game_shapes as bf

    def fake_range(start, end, failed_windows=None):
        # simulate a transient ESPN outage that skips this source window
        if failed_windows is not None:
            failed_windows.append(f"{start:%Y%m%d}-{end:%Y%m%d}")
        return []

    monkeypatch.setattr(bf, "fetch_games_for_range", fake_range)

    # A skipped source window -> main() reports incomplete and exits non-zero
    # instead of logging "complete" over a partial archive.
    assert bf.main() == 1


def test_backfill_recompute_reprocesses_existing(env, monkeypatch, wp_plays):
    session = env.get_session()
    upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    upsert_team(session, name="New York Liberty", bpi_rating=0.0, abbreviation="NY")
    session.commit()

    import scripts.backfill_game_shapes as bf
    import scripts.daily_update as du

    events = [
        {
            "event_id": "24A",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        }
    ]
    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda s, e, failed_windows=None: events
    )

    calls = {"n": 0}

    def fake_wp(espn_id, timeout=10):
        calls["n"] += 1
        pct = 0.9 if calls["n"] == 1 else 0.95  # 2nd derive yields a new value
        return {
            "status": GameStatus.FINAL,
            "home_team": "Las Vegas Aces",
            "away_team": "New York Liberty",
            "home_score": "80",
            "away_score": "70",
            "plays": wp_plays([0.55, pct]),
        }

    monkeypatch.setattr(du, "fetch_live_win_probability", fake_wp)

    assert bf.backfill_range(session, date(2024, 5, 1), date(2024, 10, 31)) == 1
    first = session.query(env.GameShape).filter_by(espn_id="24A").one().excitement

    # default re-run skips the already-stored row (idempotent)
    assert bf.backfill_range(session, date(2024, 5, 1), date(2024, 10, 31)) == 0

    # recompute=True reprocesses + overwrites it in place (not a duplicate)
    assert (
        bf.backfill_range(session, date(2024, 5, 1), date(2024, 10, 31), recompute=True)
        == 1
    )
    assert session.query(env.GameShape).filter_by(espn_id="24A").count() == 1
    second = session.query(env.GameShape).filter_by(espn_id="24A").one().excitement
    assert second != first  # the re-derived feed produced a new stored value
    session.close()


def test_backfill_recompute_purges_row_rejected_by_coverage_gate(env, monkeypatch):
    # A stored row whose refetched FINAL feed is authoritatively unshapeable
    # (the coverage gate rejects it) is kept + recorded as a miss by default
    # (fail closed — recompute must never destroy rows unprompted), and PURGED
    # only under the explicit --purge-unshapeable operator flag — the escape
    # for a permanently degenerate feed (the 2025-05-02 DAL@LV case) that
    # would otherwise wedge every future --recompute run at exit 1.
    session = env.get_session()
    upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    upsert_team(session, name="New York Liberty", bpi_rating=0.0, abbreviation="NY")
    _seed_stale_shape(session)
    session.commit()

    import scripts.backfill_game_shapes as bf
    import scripts.daily_update as du

    events = [
        {
            "event_id": "STALE",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        }
    ]
    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda s, e, failed_windows=None: events
    )

    def fake_wp(espn_id, timeout=10):
        return {
            "status": GameStatus.FINAL,
            "home_team": "Las Vegas Aces",
            "away_team": "New York Liberty",
            "home_score": "80",
            "away_score": "70",
            # Degenerate clustered feed: valid samples, fails the coverage gate.
            "plays": [
                {"period": 2, "clock": "0:02", "home_pct": 0.0},
                {"period": 2, "clock": "0:00", "home_pct": 0.0},
                {"period": 2, "clock": "0:00", "home_pct": 0.0},
            ],
        }

    monkeypatch.setattr(du, "fetch_live_win_probability", fake_wp)

    # Default recompute: fail closed — row kept, miss recorded.
    failed_games: list[str] = []
    stored = bf.backfill_range(
        session,
        date(2024, 5, 1),
        date(2024, 10, 31),
        recompute=True,
        failed_games=failed_games,
    )
    assert stored == 0
    assert session.query(env.GameShape).filter_by(espn_id="STALE").count() == 1
    assert failed_games == ["STALE"]

    # Explicit purge flag: the stale row is removed, and the purge is
    # convergence, not a failure — recompute must not exit 1 on this game.
    failed_games = []
    stored = bf.backfill_range(
        session,
        date(2024, 5, 1),
        date(2024, 10, 31),
        recompute=True,
        failed_games=failed_games,
        purge_unshapeable=True,
    )
    assert stored == 0
    assert session.query(env.GameShape).filter_by(espn_id="STALE").count() == 0
    assert failed_games == []
    session.close()


def test_backfill_recompute_records_existing_row_misses_only(
    env, monkeypatch, wp_plays
):
    session = env.get_session()
    upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    upsert_team(session, name="New York Liberty", bpi_rating=0.0, abbreviation="NY")
    # STALE already has a stored shape -> if recompute can't refresh it, the
    # stale pre-fix row persists (a real miss to surface).
    _seed_stale_shape(session)
    session.commit()

    import scripts.backfill_game_shapes as bf
    import scripts.daily_update as du

    events = [
        {
            "event_id": "GOOD",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        },
        {
            "event_id": "STALE",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        },
        {
            "event_id": "NEW_BAD",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        },
    ]
    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda s, e, failed_windows=None: events
    )

    def fake_wp(espn_id, timeout=10):
        if espn_id == "GOOD":
            return {
                "status": GameStatus.FINAL,
                "home_team": "Las Vegas Aces",
                "away_team": "New York Liberty",
                "home_score": "80",
                "away_score": "70",
                "plays": wp_plays([0.55, 0.9]),
            }
        # STALE + NEW_BAD: a non-final feed -> _build_and_store_shape returns
        # False (no exception), exercising the False-return miss path.
        return {
            "status": "STATUS_IN_PROGRESS",
            "home_team": "Las Vegas Aces",
            "away_team": "New York Liberty",
            "home_score": "",
            "away_score": "",
            "plays": [],
        }

    monkeypatch.setattr(du, "fetch_live_win_probability", fake_wp)

    failed_games: list[str] = []
    stored = bf.backfill_range(
        session,
        date(2024, 5, 1),
        date(2024, 10, 31),
        recompute=True,
        failed_games=failed_games,
    )
    assert stored == 1  # only GOOD rebuilt
    # STALE (existing row, False return) is a stale-row miss; NEW_BAD (no
    # existing row, also False) is a benign miss -> NOT recorded.
    assert failed_games == ["STALE"]
    session.close()


def test_backfill_main_recompute_fails_closed_on_per_game_error(env, monkeypatch):
    session = env.get_session()
    upsert_team(session, name="Las Vegas Aces", bpi_rating=0.0, abbreviation="LV")
    upsert_team(session, name="New York Liberty", bpi_rating=0.0, abbreviation="NY")
    # STALE has a stored row, so a failed recompute leaves stale data behind.
    _seed_stale_shape(session)
    session.commit()
    session.close()

    import scripts.backfill_game_shapes as bf
    import scripts.daily_update as du

    events = [
        {
            "event_id": "STALE",
            "date": "2024-08-15",
            "status": GameStatus.FINAL,
            "winner_team": "Las Vegas Aces",
        }
    ]
    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda s, e, failed_windows=None: events
    )

    def fake_wp(espn_id, timeout=10):
        raise RuntimeError("ESPN summary drift")

    monkeypatch.setattr(du, "fetch_live_win_probability", fake_wp)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_game_shapes", "--recompute"])

    # recompute can't refresh STALE's existing row -> must exit non-zero, not a
    # silent exit 0 that leaves the row holding stale pre-fix data.
    assert bf.main() == 1
