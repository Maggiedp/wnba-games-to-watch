"""One-shot backfill of game_shapes for completed 2024-2026 games.

Sources game IDs + metadata from fetch_games_for_range (the same ESPN feed the
Elo replay uses, so it covers 2024+), computes each shape via the shared
_build_and_store_shape helper, upserts by espn_id. Idempotent — skips espn_ids
already stored. Safe to re-run.

Pass --recompute to re-derive + overwrite shapes already stored (use after an
ingest change alters the computed curve/metrics, e.g. the play-ordering fix).
A stored row whose refetched FINAL feed is authoritatively unshapeable (fails
the coverage gate) is recorded as a miss and keeps recompute failing closed at
exit 1; add --purge-unshapeable to explicitly delete such rows instead —
destruction is an operator decision, never the default.

Usage:
    python -m scripts.backfill_game_shapes              # fill missing only
    python -m scripts.backfill_game_shapes --recompute  # re-derive + overwrite all
    python -m scripts.backfill_game_shapes --recompute --purge-unshapeable
"""

import logging
import sys
from datetime import date

from scripts.daily_update import ShapeResult, _build_and_store_shape
from src.constants import GameStatus
from src.data.espn_api import fetch_games_for_range
from src.db.queries import (
    delete_game_shape,
    get_existing_shape_espn_ids,
    get_team_abbrev_map,
)
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
    purge_unshapeable: bool = False,
) -> int:
    """Backfill one date range. Returns the count stored. Skipped source
    windows (transient ESPN failures) are appended to `failed_windows`;
    per-game recompute misses are appended to `failed_games` (so a
    `--recompute` run fails closed instead of silently leaving stale rows
    behind).

    `recompute=True` re-derives + overwrites shapes already stored (used after
    an ingest fix changes the computed curve/metrics); the default skips them.
    `purge_unshapeable=True` (recompute only) deletes a stored row on an
    UNSHAPEABLE result instead of recording a miss — see the module
    docstring."""
    events = fetch_games_for_range(start, end, failed_windows=failed_windows)
    completed = [e for e in events if e.get("event_id") and _is_completed(e)]
    existing = get_existing_shape_espn_ids(session, [e["event_id"] for e in completed])
    # recompute reprocesses everything; fill-missing skips already-stored games.
    already = set() if recompute else existing
    abbrev_map = get_team_abbrev_map(session)
    stored = 0
    for e in completed:
        espn_id = e["event_id"]
        if espn_id in already:
            continue
        try:
            result = _build_and_store_shape(
                session, espn_id, e["date"], abbrev_map, timeout=10
            )
        except Exception as ex:  # one bad game must not abort the batch
            logger.warning(f"Skipping espn_id={espn_id}: {ex}")
            result = ShapeResult.RETRY
        if result == ShapeResult.STORED:
            stored += 1
            if stored % 25 == 0:
                # upsert_game_shape commits per row; this is just progress.
                logger.info(f"  …{stored} stored")
        elif recompute and espn_id in existing:
            if result == ShapeResult.UNSHAPEABLE and purge_unshapeable:
                # Safe to get wrong: a healthy-feed game purged in error is
                # re-stored by the next fill-missing/recompute run (2026 rows
                # also by the daily populate).
                delete_game_shape(session, espn_id)
                logger.warning(f"Purged stale unshapeable row: espn_id={espn_id}")
            elif failed_games is not None:
                # The stale pre-existing row persists — record it to fail
                # closed. (A completed game with no existing row that can't be
                # shaped is a benign miss, not a stale-data integrity problem,
                # so it doesn't gate the exit code.)
                logger.warning(
                    f"Recompute miss (stale row kept): espn_id={espn_id} ({result})"
                )
                failed_games.append(espn_id)
    return stored


def main() -> int:
    args = sys.argv[1:]
    recompute = "--recompute" in args
    purge_unshapeable = "--purge-unshapeable" in args
    if purge_unshapeable and not recompute:
        logger.error("--purge-unshapeable requires --recompute")
        return 1
    mode = "RECOMPUTE (overwrite existing)" if recompute else "fill missing"
    if purge_unshapeable:
        mode += " + purge unshapeable"
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
                    purge_unshapeable=purge_unshapeable,
                )
            if failed_windows:
                logger.error(
                    f"INCOMPLETE: {len(failed_windows)} source window(s) failed to "
                    f"fetch and were skipped: {failed_windows}. Stored {total} so far; "
                    "re-run to fill the gaps (idempotent)."
                )
                return 1
            if failed_games:
                # Populated only in --recompute mode: existing rows that couldn't
                # be refreshed, so their stale pre-fix data persists. Fail closed
                # so the operator re-runs until the archive fully converges.
                logger.error(
                    f"Recompute INCOMPLETE: {len(failed_games)} stored game(s) "
                    f"could not be refreshed (stale rows kept): {failed_games}. "
                    "Re-run to retry; a game that stays 'unshapeable' needs "
                    "--recompute --purge-unshapeable to remove its row."
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
