"""One-shot backfill of 2026 `daily_rankings.importance_score` (+ overall_score)
onto the round-reached-fate scale.

The game-importance metric's fate variable changed from binary "makes
playoffs" to a five-level "how far did each team go"
(FATE_MISSED/FATE_LOST_QF/FATE_LOST_SF/FATE_LOST_FINALS/FATE_CHAMPION), and
REGULAR_SEASON_MAX_SWING (src/scoring/importance.py) was re-derived for the
new scale. Every regular-season `daily_rankings` row stored before that
change carries an `importance_score` computed under the OLD metric/scale, so
completed games in the archive currently show a mixed old/new distribution.
This script re-derives `importance_score` (and the `overall_score` it feeds)
for every 2026 date that has stored rankings.

Only regular-season (season_type == 2) rows are touched. Preseason rows
(season_type == 1) are pinned at importance 0 in production and unaffected.
Postseason rows (season_type == 3) use `compute_postseason_swing_from_matrix`
/ POSTSEASON_MAX_SWING — a separate metric that doesn't consume fate_levels
at all, so it was never on the old scale. Both are left exactly as stored.

As-of-date standings: `compute_standings` (daily_update.py) has no as-of-date
parameter — it reads ALL completed games, so it can't reproduce what
standings looked like on an earlier ranked date. This script instead mirrors
scripts/compute_importance_ceiling.py: for each ranked date, seed every known
team at 0-0 and apply only the regular-season results strictly BEFORE that
date (with `increment_h2h`, required by resolve_seeding's tiebreakers). Elo
ratings come from `replay_games` over the same "strictly before" slice of
non-preseason history (mirrors daily_update.compute_elo_ratings, which also
spans postseason results, just re-clipped to the as-of date). The remaining-
games universe for the Monte Carlo is every 2026 regular-season game with
date >= the ranked date — completed by now or not, since from the
perspective of that morning's daily job none of them had been decided yet.

quality_score is READ from the existing row, never recomputed — per-date BPI
history isn't stored, so there's nothing to recompute it from.
overall_score IS rewritten in the same pass (`quality * 0.6 + importance *
0.4`), mirroring compute_daily_scores exactly, else the stored overall would
silently stop agreeing with its own components.

A regular-season game that can't be located in the remaining-games universe
(e.g. a schedule mismatch) gets importance_score=None, exactly like
compute_daily_scores' "not simulated" case — and `_impute_missing_importance`
is reused verbatim so its contribution to `overall` blends in at that date's
mean importance rather than deflating to 0. The mean is taken over ALL of
that date's rankings (untouched preseason/postseason values included, same
as production), not just the regular-season ones being rewritten.

Each date is rewritten in ONE transaction, so a mid-run failure can't leave
a date half-updated. Fails closed via scripts._recompute_gate.recompute_gate:
exit 1 lists dates that raised, stale values kept, re-run to retry.

Usage:
    python -m scripts.backfill_importance              # list dates, change nothing
    python -m scripts.backfill_importance --recompute   # rewrite importance + overall
"""

from __future__ import annotations

import logging
import random
import sys

from scripts._recompute_gate import recompute_gate
from scripts.daily_update import _ELO_HISTORY_START, _impute_missing_importance
from src.data.espn_api import _SEASON_END, fetch_games_for_range
from src.db.queries import get_all_teams, get_daily_rankings
from src.db.schema import DailyRanking, Game, get_session, init_db
from src.scoring.elo import INITIAL_RATING, replay_games
from src.scoring.importance import REGULAR_SEASON_MAX_SWING, normalize_importance_score
from src.scoring.monte_carlo import (
    compute_importance_from_matrix,
    run_monte_carlo_simulation,
)
from src.scoring.tiebreakers import increment_h2h

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SEASON_YEAR = 2026
NUM_SIMULATIONS = 10000


def _standings_as_of(
    date_str: str, teams, elo_games: list[dict], regular_season_games: list[dict]
) -> dict[str, dict]:
    """Standings as of the morning of `date_str`: every known team seeded
    0-0, then regular-season results strictly before `date_str` applied on
    top — mirrors daily_update.compute_standings, minus the as-of-date
    restriction it lacks."""
    prior_elo_games = [g for g in elo_games if g.get("date", "") < date_str]
    elo = replay_games(prior_elo_games).final_ratings
    standings: dict[str, dict] = {
        t.name: {
            "wins": 0,
            "losses": 0,
            "h2h": {},
            "elo": elo.get(t.name, INITIAL_RATING),
        }
        for t in teams
    }
    for g in regular_season_games:
        if g.get("date", "") >= date_str:
            continue
        winner = g.get("winner_team")
        team_a, team_b = g.get("team_a"), g.get("team_b")
        loser = team_b if winner == team_a else team_a
        if not winner or winner not in standings or loser not in standings:
            continue
        standings[winner]["wins"] += 1
        standings[loser]["losses"] += 1
        increment_h2h(standings[winner]["h2h"], loser, won=True)
        increment_h2h(standings[loser]["h2h"], winner, won=False)
    return standings


def rebuild_date(
    session,
    date_str: str,
    elo_games: list[dict],
    regular_season_games: list[dict],
) -> int:
    """Recompute importance_score + overall_score for every regular-season
    daily_rankings row on `date_str`, from standings as of that date.

    `elo_games`: completed non-preseason games (any season_type != 1) across
    the full Elo replay window, used to derive Elo ratings as of `date_str`.
    `regular_season_games`: the full 2026 regular-season (season_type == 2)
    schedule, completed or not, used both for prior W-L/h2h and as the
    remaining-games simulation universe.

    Returns the number of rows whose importance_score actually changed.
    Runs in one transaction: any fault raises and rolls back, so a mid-run
    failure can't leave this date half-rewritten.
    """
    rankings = get_daily_rankings(session, date_str)
    if not rankings:
        return 0

    teams = get_all_teams(session)
    team_by_id = {t.id: t for t in teams}

    try:
        standings = _standings_as_of(date_str, teams, elo_games, regular_season_games)

        remaining_rows = [
            g for g in regular_season_games if g.get("date", "") >= date_str
        ]
        remaining = [(g["team_a"], g["team_b"]) for g in remaining_rows]

        # Deterministic per-date seed so a re-run reproduces the same result
        # (mirrors compute_importance_ceiling.py; not production's live seed).
        random.seed(int(date_str.replace("-", "")))
        _, outcome_matrix, _, _, _, fate_levels = run_monte_carlo_simulation(
            standings, remaining, num_simulations=NUM_SIMULATIONS, return_matrix=True
        )
        team_names = list(standings.keys())
        raw_swings = compute_importance_from_matrix(
            outcome_matrix, fate_levels, remaining, team_names
        )

        # Map today's game(s) (unordered team pair, since home/away order in
        # the ranking row isn't guaranteed to match the fetched schedule's)
        # to their index in the swing list.
        index_by_teams: dict[frozenset, int] = {
            frozenset({g["team_a"], g["team_b"]}): i
            for i, g in enumerate(remaining_rows)
            if g.get("date") == date_str
        }

        # First pass: new importance (or None if unmatched) for regular-
        # season rows only; preseason/postseason rows are left alone
        # entirely (their swing metric never changed scale).
        new_importance: dict[int, float | None] = {}
        for ranking in rankings:
            team_a = team_by_id.get(ranking.team_a_id)
            team_b = team_by_id.get(ranking.team_b_id)
            if team_a is None or team_b is None:
                continue
            game_row = (
                session.query(Game)
                .filter_by(
                    date=date_str,
                    team_a_id=ranking.team_a_id,
                    team_b_id=ranking.team_b_id,
                )
                .first()
            )
            if game_row is None or game_row.season_type != 2:
                continue
            idx = index_by_teams.get(frozenset({team_a.name, team_b.name}))
            new_importance[ranking.id] = (
                normalize_importance_score(
                    raw_swings[idx], max_swing=REGULAR_SEASON_MAX_SWING
                )
                if idx is not None
                else None
            )

        if not new_importance:
            return 0

        # Impute exactly as compute_daily_scores does: the mean importance of
        # every game ranked that day (untouched preseason/postseason values
        # included), not just the regular-season games being rewritten.
        all_importances = [
            new_importance[r.id] if r.id in new_importance else r.importance_score
            for r in rankings
        ]
        imputed = _impute_missing_importance(all_importances)

        changed = 0
        for ranking in rankings:
            if ranking.id not in new_importance:
                continue
            new_val = new_importance[ranking.id]
            if new_val == ranking.importance_score:
                continue
            importance_for_overall = new_val if new_val is not None else imputed
            ranking.importance_score = new_val
            ranking.overall_score = (
                ranking.quality_score * 0.6 + importance_for_overall * 0.4
            )
            changed += 1

        session.commit()
        return changed
    except Exception:
        session.rollback()
        raise


def main() -> int:
    recompute = "--recompute" in sys.argv[1:]
    mode = "RECOMPUTE (rewrite importance + overall)" if recompute else "list only"
    logger.info(f"=== Backfilling {_SEASON_YEAR} importance/overall — {mode} ===")
    try:
        init_db()
        session = get_session()
        try:
            failed_windows: list[str] = []
            history = fetch_games_for_range(
                _ELO_HISTORY_START, _SEASON_END, failed_windows=failed_windows
            )
            if failed_windows:
                logger.error(
                    f"INCOMPLETE: {len(failed_windows)} source window(s) failed to "
                    f"fetch and were skipped: {failed_windows}. Standings would be "
                    "built on an incomplete history; re-run instead of proceeding."
                )
                return 1

            elo_games = [
                g
                for g in history
                if g.get("winner_team") and g.get("season_type", 2) != 1
            ]
            elo_games.sort(key=lambda g: (g.get("date", ""), g.get("time", "")))
            regular_season_games = [
                g
                for g in history
                if g.get("season_type", 2) == 2
                and g.get("date", "").startswith(str(_SEASON_YEAR))
            ]
            regular_season_games.sort(
                key=lambda g: (g.get("date", ""), g.get("time", ""))
            )

            ranked_dates = sorted(
                {
                    row.date
                    for row in session.query(DailyRanking.date)
                    .filter(DailyRanking.date.like(f"{_SEASON_YEAR}-%"))
                    .distinct()
                }
            )
            logger.info(f"{len(ranked_dates)} ranked date(s) in {_SEASON_YEAR}")

            if not recompute:
                logger.info(f"Would recompute: {ranked_dates}")
                logger.info("Pass --recompute to write changes.")
                return 0

            failed_dates: list[str] = []
            total_changed = 0
            for d in ranked_dates:
                try:
                    changed = rebuild_date(session, d, elo_games, regular_season_games)
                    total_changed += changed
                    logger.info(f"{d}: {changed} row(s) updated")
                except Exception as e:
                    logger.error(f"Failed to rebuild {d}: {e}", exc_info=True)
                    failed_dates.append(d)

            gate = recompute_gate(failed_dates, "date")
            if gate:
                return gate
            logger.info(
                f"=== Backfill complete: {total_changed} row(s) updated across "
                f"{len(ranked_dates)} date(s) ==="
            )
            return 0
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
