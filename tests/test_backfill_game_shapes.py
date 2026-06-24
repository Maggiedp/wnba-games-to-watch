from datetime import date

from src.constants import GameStatus
from src.db.queries import upsert_team


def test_backfill_stores_completed_and_skips_existing(env, monkeypatch):
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
            "plays": [
                {"period": 1, "clock": "10:00", "home_pct": 0.5},
                {"period": 4, "clock": "0:00", "home_pct": 0.9},
            ],
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
