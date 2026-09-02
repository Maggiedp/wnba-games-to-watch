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
/ POSTSEASON_MAX_SWING — the same five-level fate as the regular season, but
summed over the two teams playing and normalized by a structural ceiling.
Both are left exactly as stored.

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

importance_detail (the "What's at stake" movers JSON) is ALSO rewritten in
the same pass, by reusing daily_update._importance_detail_for_game verbatim
against this run's own outcome_matrix/fate_levels — not duplicated. Its
existing semantics carry over unchanged: importance == 0.0 stores NULL
(raw movers would show spurious stakes on a no-signal game), importance is
None does NOT suppress (an unmatched row's game_index lookup inside that
helper also misses, so it naturally returns None anyway), preseason rows
never reach this code path at all, and postseason stays untouched.

A regular-season game that can't be located in the remaining-games universe
(e.g. a schedule mismatch) gets importance_score=None, exactly like
compute_daily_scores' "not simulated" case — and `_impute_missing_importance`
is reused verbatim so its contribution to `overall` blends in at the mean
importance production would have imputed, rather than deflating to 0. That
mean is NOT taken over just date_str's own games — compute_daily_scores
scores every `upcoming_game` (every remaining regular-season game of the
season, any future date) in ONE MC run each morning, and imputes over that
whole run's `partial`. So the reconstructed pool here is every game in the
SAME remaining-games universe (date >= date_str) fed to this date's MC run,
normalized from that run's own `raw_swings` — not other dates' currently-
stored `daily_rankings` rows, which would depend on this script's processing
order and go stale/wrong-scale for dates not yet rebuilt.

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
from scripts.daily_update import (
    _ELO_HISTORY_START,
    _impute_missing_importance,
    _importance_detail_for_game,
)
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
    restriction it lacks.

    A game counts toward prior standings only when its CURRENT `date` is
    strictly before `date_str` AND it has a `winner_team`. A not-yet-played
    game can never be pulled in by mistake: the `if not winner: continue`
    guard below covers that regardless of what its `date` says.

    This reconstructs the retrospectively ACCURATE history — not the
    schedule as production believed it live on that morning. `upsert_game`
    is reschedule-aware and re-keys a game's `date` by `espn_id` (see root
    CLAUDE.md), so a game that later moved carries its NEW date here too:
    moved later, it correctly still counts as remaining/unplayed as of
    `date_str`; moved earlier and played, it correctly counts as a
    completed prior result. This can only diverge from what production
    actually computed live in the narrow window between a reschedule
    landing in ESPN's feed and the next daily run picking it up — and in
    that window this reconstruction's version is the MORE accurate one.
    Deliberate: an archive recompute should reflect what actually
    happened, not the schedule's transient in-flight state.

    One real, unclosed gap: a game that is STILL unplayed while its
    stored `date` is already in the past (a postponement ESPN hasn't
    re-dated yet, or a lingering TBD) falls out of BOTH sides of this
    reconstruction — no `winner_team`, so it's not counted into prior
    standings; `date < date_str`, so it's also excluded from
    `rebuild_date`'s remaining-games universe (see the matching `>=`
    split there). It simply vanishes from that date's simulation,
    producing slightly-off importance for the dates it touches. Bounded to
    the specific games affected, not season-wide, and not fixed here —
    see scripts/CLAUDE.md for the pre-run audit query that checks whether
    any such game exists before a production run.
    """
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
    """Recompute importance_score + overall_score + importance_detail for
    every regular-season daily_rankings row on `date_str`, from standings
    as of that date.

    `elo_games`: completed non-preseason games (any season_type != 1) across
    the full Elo replay window, used to derive Elo ratings as of `date_str`.
    `regular_season_games`: the full 2026 regular-season (season_type == 2)
    schedule, completed or not, used both for prior W-L/h2h and as the
    remaining-games simulation universe.

    Returns the number of rows whose stored state actually changed (any of
    importance_score/overall_score/importance_detail). Runs in one
    transaction: any fault raises and rolls back, so a mid-run failure
    can't leave this date half-rewritten.

    Raises RuntimeError if a REGULAR-SEASON ranking row can't be re-matched
    to both its teams and a Game row — that combination should be
    unreachable in a healthy DB (every DailyRanking row's
    (date, team_a_id, team_b_id) has a matching Game row written the same
    morning; see scripts/CLAUDE.md), but "unreachable today" is not "fails
    closed": silently skipping such a row would leave its stale, possibly
    old-scale importance_score/overall_score in place with nothing visible
    to the operator, contradicting this script's own fail-closed contract.
    Preseason/postseason rows are a legitimate, expected skip and never
    raise.
    """
    rankings = get_daily_rankings(session, date_str)
    if not rankings:
        return 0

    teams = get_all_teams(session)
    team_by_id = {t.id: t for t in teams}
    # One query per date rather than one per ranking row: the per-row lookup
    # below is a point read, but at ~10-15 rows x ~116 dates that was ~1,500
    # round-trips for a season. `DailyRanking` has a (date, team_a_id,
    # team_b_id) unique constraint, so this key is unique within a date.
    game_by_teams = {
        (g.team_a_id, g.team_b_id): g
        for g in session.query(Game).filter_by(date=date_str).all()
    }

    try:
        standings = _standings_as_of(date_str, teams, elo_games, regular_season_games)

        # The `>=` here is the other half of _standings_as_of's `<` split —
        # together they partition regular_season_games by CURRENT date, not
        # by what was known live on date_str's morning (see that docstring
        # for the reschedule-accuracy tradeoff and the one real gap: a
        # still-unplayed game whose date is already in the past falls out
        # of both halves and is silently missing from this date's sim).
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

        # Map each remaining game to its index in the swing list by espn_id
        # — mirrors daily_update.compute_daily_scores' remaining_event_index
        # exactly (and its own `_importance_for_game` lookup). NOT a
        # team-name frozenset: that degrades silently to "unmatched" on any
        # team-name drift (rename, encoding difference), where an espn_id is
        # immune. `game_row.espn_id` is already queried below.
        remaining_event_index: dict[str, int] = {
            g["event_id"]: i for i, g in enumerate(remaining_rows) if g.get("event_id")
        }

        # First pass: new importance (or None if unmatched IN THE SIM
        # UNIVERSE — the designed "not simulated" path, see the imputation
        # note below) for regular-season rows only. Preseason/postseason
        # rows are a LEGITIMATE, expected skip (their swing metric never
        # changed scale) — but that's only provable by actually finding the
        # Game row and reading an EXPLICIT season_type of 1 or 3.
        # season_type IS None is a known degraded state ("the season-type
        # backfill hasn't classified this row yet" — see
        # compute_standings, which separately skips + warns on it), NOT
        # proof the game is preseason/postseason, so it must NOT be treated
        # as a legitimate skip. A row whose Game row can't be found at all,
        # whose season_type is still None, or whose team_a_id/team_b_id
        # don't resolve to real Team rows, can NOT be assumed
        # preseason/postseason — every DailyRanking row's (date,
        # team_a_id, team_b_id) has a matching, classified Game row written
        # the same morning in a healthy DB, so any of these failures means
        # this row IS (or would be) regular-season and its stale
        # importance_score/overall_score would otherwise survive untouched
        # with nothing visible to the operator. Collected here and raised
        # below rather than silently skipped, so a mid-run crash still
        # means "no writes for this date" (one transaction per date).
        new_importance: dict[int, float | None] = {}
        new_detail: dict[int, str | None] = {}
        unmatchable: list[str] = []
        for ranking in rankings:
            game_row = game_by_teams.get((ranking.team_a_id, ranking.team_b_id))
            if (
                game_row is not None
                and game_row.season_type is not None
                and game_row.season_type != 2
            ):
                continue  # preseason/postseason: legitimate, expected skip
            team_a = team_by_id.get(ranking.team_a_id)
            team_b = team_by_id.get(ranking.team_b_id)
            if (
                team_a is None
                or team_b is None
                or game_row is None
                or game_row.season_type is None
            ):
                unmatchable.append(
                    f"ranking_id={ranking.id} date={date_str} "
                    f"team_a_id={ranking.team_a_id} team_b_id={ranking.team_b_id} "
                    f"(team_a_resolved={team_a is not None}, "
                    f"team_b_resolved={team_b is not None}, "
                    f"game_row_found={game_row is not None}, "
                    f"season_type={game_row.season_type if game_row else None})"
                )
                continue
            idx = remaining_event_index.get(game_row.espn_id)
            new_val = (
                normalize_importance_score(
                    raw_swings[idx], max_swing=REGULAR_SEASON_MAX_SWING
                )
                if idx is not None
                else None
            )
            new_importance[ranking.id] = new_val
            # Reuse daily_update's own detail builder verbatim rather than
            # duplicating its payload construction/guards — passing the
            # SAME importance value preserves its zero-gate (importance ==
            # 0.0 -> NULL detail, since raw movers would show spurious
            # stakes on a no-signal game) and its None-does-not-suppress
            # semantics (an unmatched row's game_index lookup inside the
            # helper also misses, so it naturally returns None too — no
            # special-casing needed here). Postseason is never reached
            # (this loop only ever adds season_type==2 rows past this
            # point); preseason rows never reach this loop at all.
            new_detail[ranking.id] = _importance_detail_for_game(
                {
                    "team_a": team_a.name,
                    "team_b": team_b.name,
                    "event_id": game_row.espn_id,
                    "season_type": 2,
                },
                outcome_matrix,
                fate_levels,
                remaining_event_index,
                team_names=team_names,
                importance=new_val,
            )

        if unmatchable:
            raise RuntimeError(
                f"{len(unmatchable)} regular-season daily_rankings row(s) on "
                f"{date_str} could not be re-matched to both teams and a "
                f"Game row (stale importance_score/overall_score would "
                f"otherwise be left in place unnoticed): {unmatchable}"
            )

        if not new_importance:
            return 0

        # Impute exactly as compute_daily_scores does: the mean importance
        # over the SAME population that morning's one MC run actually
        # scored — every remaining regular-season game (date >= date_str),
        # not just date_str's own games. Reconstructed straight from this
        # run's raw_swings (index-aligned with `remaining`), not from other
        # dates' currently-stored daily_rankings rows — those depend on
        # this script's processing order and may still be old-scale/stale
        # for dates not yet rebuilt. (Known scope limit: unlike production,
        # this doesn't fold in any preseason/postseason games that were
        # also "upcoming" as of date_str — structurally absent from the
        # regular-season stretch this backfill covers; see scripts/CLAUDE.md.)
        remaining_importances = [
            normalize_importance_score(s, max_swing=REGULAR_SEASON_MAX_SWING)
            for s in raw_swings
        ]
        imputed = _impute_missing_importance(remaining_importances)

        changed = 0
        for ranking in rankings:
            if ranking.id not in new_importance:
                continue
            new_val = new_importance[ranking.id]
            new_detail_val = new_detail[ranking.id]
            # Compute the FULL target state (all three fields) before
            # deciding whether to write. A row that stays unmatched keeps
            # importance_score=None on both sides of this update — but its
            # overall_score must still move if the imputed mean did (every
            # other regular-season row on this date just moved to the new
            # scale). Gating the write on `new_val == ranking.importance_score`
            # alone short-circuits on None == None and leaves such a row's
            # overall_score frozen on the OLD-scale imputed mean while every
            # sibling row moves — exactly the cross-field inconsistency this
            # backfill exists to eliminate. Write whenever ANY of the three
            # differs.
            importance_for_overall = new_val if new_val is not None else imputed
            new_overall = ranking.quality_score * 0.6 + importance_for_overall * 0.4
            if (
                new_val == ranking.importance_score
                and new_overall == ranking.overall_score
                and new_detail_val == ranking.importance_detail
            ):
                continue
            ranking.importance_score = new_val
            ranking.overall_score = new_overall
            ranking.importance_detail = new_detail_val
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
            # rebuild_date's per-row unmatchable check does NOT cover an
            # empty fetch here: it keys off the DB's own Game table +
            # get_all_teams(session), both independent of this ESPN fetch.
            # An empty `history` only empties regular_season_games/
            # elo_games, not the DB — so a normal, healthy row (real
            # classified Game row, resolvable teams) is NOT unmatchable; it
            # falls into the "not in sim universe" branch, gets
            # importance=None, and _impute_missing_importance([]) returns
            # 0.0 rather than raising. Net effect without the guard below:
            # every regular-season row on every date would silently get
            # importance_score=None / overall_score=quality*0.6, the
            # commit would succeed, and the run would exit 0 — silently
            # wiping the archive's importance signal. Hence the explicit
            # guard immediately below, checked before any date-level
            # recompute begins (was wrongly believed redundant in an
            # earlier round; a re-review traced the false premise).
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

            # Fail closed BEFORE any date-level recompute begins if the
            # fetch looks incomplete for a season that already has ranked
            # rows to rewrite: see the comment above the fetch call for why
            # rebuild_date's own per-row unmatchable check does not catch
            # this (it keys off DB state, not this fetch's output).
            if ranked_dates and not regular_season_games:
                logger.error(
                    f"INCOMPLETE: fetched 0 {_SEASON_YEAR} regular-season games "
                    f"from ESPN, but {len(ranked_dates)} date(s) have stored "
                    "daily_rankings rows to rewrite. The fetch looks incomplete "
                    "(a transient ESPN issue, or a range that returned no "
                    "events) — refusing to recompute against an empty sim "
                    "universe, which would silently blank out every "
                    "regular-season row's importance signal. Nothing was "
                    "rewritten; re-run once the fetch is healthy."
                )
                return 1

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
