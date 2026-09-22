"""Check that every team's season adds up: games counted + games left == a full slate.

The Monte Carlo builds each simulated season by taking today's standings and
playing out the remaining schedule. That only lands on a real final table if,
for every team, counted + remaining equals the season length. When a game falls
out of BOTH buckets the team is frozen below the field: it can never reach the
win totals its rivals reach, and seeds it can really finish at come back as a
hard 0% in the seed distribution.

Games go missing in both buckets at once because the two are built by different
filters, each individually reasonable:

  * `compute_standings` counts a game only with a `winner_id` AND a non-NULL
    `season_type`. A completed row whose `season_type` backfill never landed is
    skipped (it logs a warning) — not counted.
  * `build_sim_inputs` treats a game as remaining only when it has no winner,
    its date is >= the window floor, and `season_type == 2`. A NULL-`season_type`
    row is skipped here too — not remaining.
  * A past-dated row with no winner (a result that never got ingested, e.g. the
    2026-09-16 ESPN outage) is not counted and, once the floor moves past its
    date, not remaining either.

So a row can be excluded by both sides and silently shrink a team's season.
This script reports the arithmetic per team and names the rows responsible.

NOTE: this checks the DB view of the remaining schedule — what the LIVE overlay
simulates. The nightly run builds `remaining_games` from the ESPN payload it
already has in flight (status != FINAL) while still taking standings from the
DB, so it can disagree in one more way: a game ESPN calls FINAL whose result was
never written to our DB is remaining to neither. Those rows show up below under
"no winner, already in the past".

Usage:
    DATABASE_URL=... python -m scripts.check_schedule_completeness
    DATABASE_URL=... python -m scripts.check_schedule_completeness --expected 44

Exit code is 1 when any team's season does not add up, so it can gate a cron.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict

from src.constants import CURRENT_SEASON
from src.data.espn_api import today_et
from src.db.queries import get_all_teams, get_upcoming_games
from src.db.schema import Game, get_session

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SEASON_LENGTH = 44


def _counts(session, season: int, today: str) -> tuple[dict, dict, list, list, list]:
    """Per-team counted/remaining games, plus the rows excluded from both."""
    teams = get_all_teams(session)
    name_by_id = {t.id: t.name for t in teams}

    counted: dict[str, int] = defaultdict(int)
    remaining: dict[str, int] = defaultdict(int)
    orphan_past: list[Game] = []
    orphan_null_type: list[Game] = []
    orphan_upcoming_type: list[Game] = []

    rows = session.query(Game).filter(Game.date.like(f"{season}-%")).all()
    for g in rows:
        a, b = name_by_id.get(g.team_a_id), name_by_id.get(g.team_b_id)
        if a is None or b is None:
            continue
        if g.winner_id is not None:
            # compute_standings: postseason excluded by design, NULL skipped.
            if g.season_type == 2:
                counted[a] += 1
                counted[b] += 1
            elif g.season_type is None:
                orphan_null_type.append(g)
        elif g.date < today:
            # No result and already past: neither counted nor simulated.
            orphan_past.append(g)

    # The remaining schedule exactly as build_sim_inputs derives it.
    for g in get_upcoming_games(session, today):
        a, b = name_by_id.get(g.team_a_id), name_by_id.get(g.team_b_id)
        if a is None or b is None:
            continue
        if g.season_type != 2:
            if not str(g.date).startswith(str(season)):
                continue
            orphan_upcoming_type.append(g)
            continue
        remaining[a] += 1
        remaining[b] += 1

    return counted, remaining, orphan_past, orphan_null_type, orphan_upcoming_type


def _describe(session, games: list[Game], name_by_id: dict[int, str]) -> list[str]:
    return [
        f"    {g.date}  {name_by_id.get(g.team_a_id, '?')} vs "
        f"{name_by_id.get(g.team_b_id, '?')}  "
        f"(espn_id={g.espn_id}, season_type={g.season_type}, "
        f"winner={'set' if g.winner_id else 'NULL'})"
        for g in sorted(games, key=lambda g: g.date)
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=CURRENT_SEASON)
    ap.add_argument(
        "--expected",
        type=int,
        default=DEFAULT_SEASON_LENGTH,
        help=f"games per team in a full season (default {DEFAULT_SEASON_LENGTH})",
    )
    args = ap.parse_args()

    today = today_et()
    session = get_session()
    try:
        teams = get_all_teams(session)
        name_by_id = {t.id: t.name for t in teams}
        counted, remaining, past, null_type, upcoming_type = _counts(
            session, args.season, today
        )

        logger.info(f"Season {args.season}, as of {today} ET\n")
        header = f"{'Team':<26}{'counted':>9}{'remaining':>11}{'total':>8}{'':>4}"
        logger.info(header)
        logger.info("-" * len(header))

        short = []
        for t in sorted(teams, key=lambda t: t.name):
            c, r = counted.get(t.name, 0), remaining.get(t.name, 0)
            total = c + r
            flag = "" if total == args.expected else f"  <-- {total - args.expected:+d}"
            if total != args.expected:
                short.append((t.name, total))
            logger.info(f"{t.name:<26}{c:>9}{r:>11}{total:>8}{flag}")

        logger.info("")
        if past:
            logger.info(f"{len(past)} game(s) with no winner, already in the past —")
            logger.info("  counted by nothing, simulated by nothing:")
            for line in _describe(session, past, name_by_id):
                logger.info(line)
            logger.info("")
        if null_type:
            logger.info(f"{len(null_type)} completed game(s) with NULL season_type —")
            logger.info("  dropped from standings by compute_standings:")
            for line in _describe(session, null_type, name_by_id):
                logger.info(line)
            logger.info("")
        if upcoming_type:
            logger.info(
                f"{len(upcoming_type)} upcoming game(s) with season_type != 2 —"
            )
            logger.info("  dropped from the remaining schedule (postseason is normal):")
            for line in _describe(session, upcoming_type, name_by_id):
                logger.info(line)
            logger.info("")

        if short:
            logger.error(
                f"INCOMPLETE: {len(short)} team(s) do not add up to "
                f"{args.expected} games: " + ", ".join(f"{n} ({t})" for n, t in short)
            )
            logger.error(
                "A team short of the field cannot reach the win totals its "
                "rivals reach, so its seed distribution will carry false zeros."
            )
            return 1

        logger.info(f"OK: every team adds up to {args.expected} games.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
