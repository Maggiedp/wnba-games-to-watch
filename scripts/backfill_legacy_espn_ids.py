"""One-shot backfill of `espn_id` for legacy 2026 regular-season rows.

The `espn_id` column landed 2026-05-13. Regular-season games ingested
before that (the opener through 2026-05-12) carry NULL `espn_id`. Because
excitement scoring needs ESPN play-by-play — keyed by `espn_id` — those
rows can never get an `excitement_index` and show as em-dash in the
completed archive. They're also skipped by `compute_standings` (NULL
`season_type`), so their results don't count in the standings.

This script recovers each `espn_id` by matching the row to ESPN's
schedule on (date, {team_a, team_b}). Once set, the existing daily-update
pipeline heals the rest the same run: `backfill_missing_season_types`
event_id-joins them to set `season_type=2` (so they count in standings),
and `populate_excitement_for_recent_completions` fetches PBP and computes
the score.

Match criteria (intentionally narrow — verified-only):
    espn_id IS NULL
    AND winner_id IS NOT NULL              (completed games only)
    AND '2026-05-08' <= date <= '2026-05-12'

Bounds are the verified regular-season orphan window: the 2026 season
opens 2026-05-08 (per ESPN's season_type=2 classification) and the
espn_id column landed 2026-05-13, so 05-08 through 05-12 is exactly the
gap. Preseason rows (date <= 2026-05-03) are handled separately by
`backfill_legacy_preseason` and must keep their NULL espn_id — the lower
bound excludes them. The match key (date, unordered team pair) is unique
per date, so there's no cross-game ambiguity.

A row with no ESPN match (date/teams not found on the schedule) is left
NULL rather than guessed — same asymmetric-error philosophy as the
preseason backfill: a missed row stays a visible em-dash; a wrong espn_id
would silently attach the wrong game's play-by-play.

Idempotent — re-running matches zero rows once cleared. Safe to run
multiple times. Self-committing: commits if any row was updated; the
caller handles rollback on failure.

Usage:
    python -m scripts.backfill_legacy_espn_ids
"""

import logging
import sys
from datetime import date

from sqlalchemy import and_

from src.data.espn_api import _canonical_name, fetch_games_for_range
from src.db.queries import get_teams_by_ids
from src.db.schema import Game, get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LEGACY_ESPN_ID_START = "2026-05-08"  # inclusive; regular-season opener
LEGACY_ESPN_ID_END = "2026-05-12"  # inclusive; last pre-espn_id-column day


def _match_filter():
    return and_(
        Game.espn_id.is_(None),
        Game.winner_id.isnot(None),
        Game.date >= LEGACY_ESPN_ID_START,
        Game.date <= LEGACY_ESPN_ID_END,
    )


def backfill_legacy_espn_ids(session) -> int:
    """Recover `espn_id` for legacy regular-season rows and commit. Returns
    the count updated.

    Matches each NULL-espn_id completed row in the verified window to ESPN's
    schedule on (date, unordered team pair). Unmatched rows are left NULL.
    Self-committing: commits if any row was updated. Idempotent.
    """
    candidates = session.query(Game).filter(_match_filter()).all()
    if not candidates:
        return 0

    team_ids = {g.team_a_id for g in candidates} | {g.team_b_id for g in candidates}
    teams = get_teams_by_ids(session, team_ids)
    espn_games = fetch_games_for_range(
        date.fromisoformat(LEGACY_ESPN_ID_START),
        date.fromisoformat(LEGACY_ESPN_ID_END),
    )
    # (date, frozenset of canonical names) -> event_id. Unordered pair makes
    # the match independent of home/away orientation differences.
    lookup = {}
    for eg in espn_games:
        eid = eg.get("event_id")
        if not eid:
            continue
        key = (
            eg["date"],
            frozenset({_canonical_name(eg["team_a"]), _canonical_name(eg["team_b"])}),
        )
        lookup[key] = eid

    updated = 0
    for g in candidates:
        names = frozenset(
            {
                _canonical_name(teams[g.team_a_id].name),
                _canonical_name(teams[g.team_b_id].name),
            }
        )
        eid = lookup.get((g.date, names))
        if eid is None:
            logger.warning(
                f"No ESPN match for {g.date} | game_id={g.id} | "
                f"teams={sorted(names)} — leaving espn_id NULL"
            )
            continue
        logger.info(f"  {g.date} | game_id={g.id} | espn_id -> {eid}")
        g.espn_id = eid
        updated += 1

    if updated:
        session.commit()
    return updated


def main() -> int:
    logger.info("=== Backfilling espn_id for legacy 2026 regular-season rows ===")
    init_db()
    session = get_session()
    try:
        n = backfill_legacy_espn_ids(session)
        if n == 0:
            logger.info("Nothing to do.")
        else:
            logger.info(f"Recovered espn_id for {n} rows")
        return 0
    except Exception as e:
        session.rollback()
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
