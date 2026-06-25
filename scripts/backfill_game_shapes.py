"""One-shot backfill of game_shapes for completed 2024-2026 games.

Sources game IDs + metadata from fetch_games_for_range (the same ESPN feed the
Elo replay uses, so it covers 2024+), computes each shape via the shared
_build_and_store_shape helper, upserts by espn_id. Idempotent — skips espn_ids
already stored. Safe to re-run.

Pass --recompute to re-derive + overwrite shapes already stored (use after an
ingest change alters the computed curve/metrics, e.g. the play-ordering fix).

Usage:
    python -m scripts.backfill_game_shapes              # fill missing only
    python -m scripts.backfill_game_shapes --recompute  # re-derive + overwrite all
"""

import logging
import sys
from datetime import date

from scripts.daily_update import _build_and_store_shape
from src.constants import GameStatus
from src.data.espn_api import fetch_games_for_range
from src.db.queries import get_existing_shape_espn_ids, get_team_abbrev_map
from src.db.schema import get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Season windows (inclusive). Mirrors the Elo replay horizon (2024-05-01).
SEASON_RANGES = [
    (2024, date(2024, 5, 1), date(2024, 10, 31)),
    (2025, date(2025, 5, 1), date(2025, 10, 31)),
    (2026, date(2026, 5, 1), date(2026, 10, 31)),
]


def _is_completed(event: dict) -> bool:
    """A parsed event is completed when ESPN marks it final."""
    return event.get("status") == GameStatus.FINAL or bool(event.get("winner_team"))


def backfill_range(
    session,
    start: date,
    end: date,
    failed_windows: list[str] | None = None,
    recompute: bool = False,
    failed_games: list[str] | None = None,
) -> int:
    """Backfill one date range. Returns the count stored. Skipped source
    windows (transient ESPN failures) are appended to `failed_windows`; per-game
    rebuild failures are appended to `failed_games` (so a `--recompute` run can
    fail closed instead of silently leaving stale rows behind).

    `recompute=True` re-derives + overwrites shapes already stored (used after
    an ingest fix changes the computed curve/metrics); the default skips them."""
    events = fetch_games_for_range(start, end, failed_windows=failed_windows)
    completed = [e for e in events if e.get("event_id") and _is_completed(e)]
    already = (
        set()
        if recompute
        else get_existing_shape_espn_ids(session, [e["event_id"] for e in completed])
    )
    abbrev_map = get_team_abbrev_map(session)
    stored = 0
    for e in completed:
        espn_id = e["event_id"]
        if espn_id in already:
            continue
        try:
            if _build_and_store_shape(
                session, espn_id, e["date"], abbrev_map, timeout=10
            ):
                stored += 1
                if stored % 25 == 0:
                    # upsert_game_shape commits per row; this is just progress.
                    logger.info(f"  …{stored} stored")
        except Exception as ex:  # one bad game must not abort the batch
            logger.warning(f"Skipping espn_id={espn_id}: {ex}")
            if failed_games is not None:
                failed_games.append(espn_id)
    return stored


def main() -> int:
    recompute = "--recompute" in sys.argv[1:]
    mode = "RECOMPUTE (overwrite existing)" if recompute else "fill missing"
    logger.info(f"=== Backfilling game_shapes (2024-2026) — {mode} ===")
    try:
        init_db()
        session = get_session()
        try:
            total = 0
            failed_windows: list[str] = []
            failed_games: list[str] = []
            for season, start, end in SEASON_RANGES:
                logger.info(f"Season {season}: {start}..{end}")
                total += backfill_range(
                    session,
                    start,
                    end,
                    failed_windows,
                    recompute=recompute,
                    failed_games=failed_games,
                )
            if failed_windows:
                logger.error(
                    f"INCOMPLETE: {len(failed_windows)} source window(s) failed to "
                    f"fetch and were skipped: {failed_windows}. Stored {total} so far; "
                    "re-run to fill the gaps (idempotent)."
                )
                return 1
            if failed_games:
                # Per-game rebuild misses. In --recompute mode those rows keep
                # stale pre-fix data, so fail closed (re-run to converge). In
                # fill-missing mode they're simply not stored yet and self-heal
                # on the next run, so surface but don't fail.
                (logger.error if recompute else logger.warning)(
                    f"{len(failed_games)} game(s) could not be rebuilt: {failed_games}"
                )
                if recompute:
                    logger.error(
                        "Recompute INCOMPLETE — those rows keep stale pre-fix "
                        "data; re-run to retry."
                    )
                    return 1
            logger.info(f"=== Backfill complete: {total} stored ===")
            return 0
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
