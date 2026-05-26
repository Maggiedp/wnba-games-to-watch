"""One-shot backfill of `season_type = 1` for legacy 2026 preseason rows.

Before the `espn_id` column landed (2026-05-13), preseason games were
ingested without `espn_id` *or* `season_type`. The user-facing archive
filter `_NOT_PRESEASON` excludes `season_type = 1` but lets NULL through,
so those 25 rows leak into "Completed games" with overall_score in the
Excitement column.

This script reclassifies them as preseason. The existing filter then hides
them from the archive. Preferred over DELETE: reversible (set back to NULL
if a row turns out to be regular-season), and `compute_standings` already
skips both NULL and `season_type == 1`, so there's no standings impact.

Match criteria (intentionally narrow — verified-only):
    espn_id IS NULL
    AND season_type IS NULL
    AND '2026-04-01' <= date <= '2026-05-03'

Bounds are set to dates ESPN explicitly classifies as season_type=1.
Verified against the live ESPN scoreboard API (fetch_games_for_range):
preseason games exist on 2026-04-25, 04-29, 04-30, 05-01, 05-03 with
season_type=1; regular-season opens 2026-05-08 with season_type=2;
2026-05-04 through 2026-05-07 has no games on ESPN's calendar.

The upper bound is the LAST verified preseason date, not the day before
the opener — anything in the unverified May 4-7 window stays NULL rather
than risk silently misclassifying a regular-season game that lands there
(e.g. via reschedule, partial migration). Asymmetric error mode by
design: a missed preseason row keeps a leak (visible as em-dash in
Excitement column); a misclassified regular-season row corrupts standings
silently — the leak is the safer failure.

Idempotent — re-running matches zero rows. Safe to run multiple times.
Self-committing: the helper calls session.commit() if any row was
mutated. Caller handles rollback on failure.

Usage:
    python -m scripts.backfill_preseason_season_type
"""

import logging
import sys

from sqlalchemy import and_

from src.db.schema import Game, get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LEGACY_PRESEASON_START = "2026-04-01"  # inclusive lower bound
LEGACY_PRESEASON_END = (
    "2026-05-03"  # inclusive upper bound; last verified preseason date
)


def _match_filter():
    return and_(
        Game.espn_id.is_(None),
        Game.season_type.is_(None),
        Game.date >= LEGACY_PRESEASON_START,
        Game.date <= LEGACY_PRESEASON_END,
    )


def backfill_legacy_preseason(session) -> int:
    """Set season_type=1 on legacy preseason rows and commit. Returns the
    count updated.

    Self-committing: if any row matched, calls session.commit() before
    returning. Eliminates the caller-commits contract so an interleaved
    query can't autoflush staged mutations into a downstream read.
    Idempotent: re-running matches zero rows.
    """
    candidates = session.query(Game).filter(_match_filter()).all()
    for g in candidates:
        logger.info(
            f"  {g.date} | game_id={g.id} | final={g.final_score_a}-{g.final_score_b}"
        )
        g.season_type = 1
    if candidates:
        session.commit()
    return len(candidates)


def main() -> int:
    logger.info("=== Backfilling season_type=1 for legacy 2026 preseason rows ===")
    init_db()
    session = get_session()
    try:
        n = backfill_legacy_preseason(session)
        if n == 0:
            logger.info("Nothing to do.")
        else:
            logger.info(f"Updated {n} rows to season_type=1")
        return 0
    except Exception as e:
        session.rollback()
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
