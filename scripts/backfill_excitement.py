"""One-shot backfill / recompute of `excitement_index` for completed 2026 games.

Default mode fills NULLs only. Idempotent — re-running skips games that
already have a value. Safe to run multiple times; daily_update keeps the
trailing edge fresh from then on.

Pass --recompute to ALSO re-derive + overwrite every stored value (use after
an ingest change alters the computed series — e.g. the PR #97 play-time sort).
Fails closed: exits 1 listing espn_ids whose stored (stale) value could not
be refreshed — re-run to converge. NULL rows that still can't be computed
stay the populate's NULL-retry problem and do not gate the exit code.

Usage:
    python -m scripts.backfill_excitement              # fill missing only
    python -m scripts.backfill_excitement --recompute  # + overwrite stored values
"""

import logging
import sys

from scripts.daily_update import (
    populate_excitement_for_recent_completions,
    refresh_recent_excitement_scores,
)
from src.db.schema import get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    recompute = "--recompute" in sys.argv[1:]
    mode = "RECOMPUTE (overwrite stored values)" if recompute else "fill missing"
    logger.info(f"=== Backfilling excitement_index for 2026 games — {mode} ===")
    try:
        init_db()
        session = get_session()
        try:
            # No retry cap and the live-WP timeout — one-shot script,
            # we want every backlog row attempted.
            populate_excitement_for_recent_completions(session, limit=None, timeout=10)
            if recompute:
                failed = refresh_recent_excitement_scores(
                    session, window_days=None, limit=None, timeout=10
                )
                if failed:
                    logger.error(
                        f"Recompute INCOMPLETE: {len(failed)} stored game(s) could "
                        f"not be refreshed (stale values kept): {failed}. "
                        "Re-run to retry."
                    )
                    return 1
            logger.info("=== Backfill complete ===")
            return 0
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
