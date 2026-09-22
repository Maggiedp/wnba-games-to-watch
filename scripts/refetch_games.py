"""Re-ingest an explicit date window that the daily job can no longer reach.

`daily_fetch_window` is `[yesterday, season_end]`. That one-day lower bound
means a night ESPN failed to serve is out of range from the next morning
onward, so no later daily run can finalize those rows — they stay in the DB
forever with a NULL `winner_id`, invisible to every "completed" query and
absent from every team's W-L record.

That is not hypothetical. ESPN began rejecting the `dates=YYYYMMDD-YYYYMMDD`
scoreboard form on 2026-09-16 (fixed in PR #139) and the morning after, the
06:00 run died on a dropped Cloud SQL connection. Between them, the five
games of 2026-09-17 ET were never finalized, and by the time anyone looked
the window had moved past them. Ten of fifteen teams carried a wrong record
into the seeding simulation for five days.

Also the way to re-ingest a game whose ESPN metadata changed outside the
window — `competition_type` landed after the 2026 Commissioner's Cup
Championship (2026-06-30) was already stored, and only a refetch of that date
can populate it.

Idempotent: re-running upserts the same values. Safe to run repeatedly.

Usage:
    python -m scripts.refetch_games --start 2026-09-17 --end 2026-09-17
"""

import argparse
import logging
import sys
from datetime import date

from scripts.daily_update import fetch_and_store_games
from src.db.schema import get_session, init_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="First ET date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last ET date, YYYY-MM-DD")
    args = parser.parse_args(argv)

    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as e:
        logger.error("Bad date: %s", e)
        return 2
    # An inverted range fetches zero months and would exit 0, reading as a
    # successful repair that did nothing.
    if end < start:
        logger.error("--end (%s) is before --start (%s)", end, start)
        return 2

    logger.info("=== Re-ingesting games for %s..%s ===", start, end)
    init_db()
    session = get_session()
    try:
        games = fetch_and_store_games(session, window=(start, end))
        logger.info("Re-ingested %d games", len(games))
        return 0
    except Exception as e:
        session.rollback()
        logger.error("Refetch failed: %s", e, exc_info=True)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
