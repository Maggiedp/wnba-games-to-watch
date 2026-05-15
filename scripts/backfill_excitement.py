"""One-shot backfill of `excitement_index` for completed 2026 games.

Idempotent — re-running skips games that already have a value. Safe to run
multiple times. Run locally after the schema migration has applied, then
forget about it; daily_update keeps the trailing edge fresh from then on.

Usage:
    python -m scripts.backfill_excitement
"""

import logging
import sys

from scripts.daily_update import populate_excitement_for_recent_completions
from src.db.schema import get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("=== Backfilling excitement_index for 2026 games ===")
    try:
        init_db()
        session = get_session()
        try:
            populate_excitement_for_recent_completions(session)
            logger.info("=== Backfill complete ===")
            return 0
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
