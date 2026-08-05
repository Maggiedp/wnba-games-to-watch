"""Database query helpers for WNBA Games to Watch."""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.schema import (
    DailyRanking,
    EloHistory,
    Game,
    GameShape,
    PlayoffProbability,
    Shot,
    ShotLeagueAvg,
    ShotMaking,
    Team,
    TeamStyle,
    ThrillerAlert,
)


# Reused predicate: include regular-season (2) + postseason (3) + legacy
# NULL season_type rows; exclude only known preseason (1). Used by the
# user-facing archive queries. `get_completed_games` (which feeds
# standings) sometimes uses a stricter IN (2, 3) filter — see its
# docstring for the conditional behavior.
_NOT_PRESEASON = or_(Game.season_type.is_(None), Game.season_type != 1)


def upsert_team(
    session: Session,
    name: str,
    bpi_rating: float,
    abbreviation: str = "",
    logo_url: str = "",
) -> Team:
    """Upsert a team (create if not exists, update if exists)."""
    team = session.query(Team).filter(Team.name == name).first()
    if team:
        team.bpi_rating = bpi_rating
        team.abbreviation = abbreviation
        team.logo_url = logo_url
    else:
        team = Team(
            name=name,
            bpi_rating=bpi_rating,
            abbreviation=abbreviation,
            logo_url=logo_url,
        )
        session.add(team)
    session.commit()
    return team


def get_team_by_name(session: Session, name: str) -> Team | None:
    """Get a team by name."""
    return session.query(Team).filter(Team.name == name).first()


def get_team_by_id(session: Session, team_id: int) -> Team | None:
    """Get a team by ID."""
    return session.query(Team).filter(Team.id == team_id).first()


def get_teams_by_ids(session: Session, team_ids: set[int]) -> dict[int, Team]:
    """Fetch all teams for the given IDs in one query."""
    teams = session.query(Team).filter(Team.id.in_(team_ids)).all()
    return {team.id: team for team in teams}


def get_all_teams(session: Session) -> list[Team]:
    """Get all teams."""
    return session.query(Team).all()


# Sentinel for upsert_game's time_utc kwarg so we can distinguish
# "caller omitted the field" (preserve) from "caller explicitly says
# TBD" (clear). ESPN's timeValid=False signal must clear a stale tip
# time, not silently keep yesterday's value.
class _Unset:
    pass


_UNSET = _Unset()


def upsert_game(
    session: Session,
    team_a_id: int,
    team_b_id: int,
    date: str,
    time: str,
    broadcaster: str,
    winner_id: int | None = None,
    final_score_a: int | None = None,
    final_score_b: int | None = None,
    espn_id: str | None = None,
    excitement_index: float | None = None,
    is_complete: bool | None = None,
    season_type: int | None = None,
    time_utc: str | None | _Unset = _UNSET,
    _retry: bool = False,
) -> Game:
    """Upsert a game (insert if not exists, update result if it has been played).

    `is_complete` only acts on False — pass it to clear a previously-
    stored completion when ESPN un-finalizes a game (postponed,
    canceled, rescheduled). True and None are equivalent: finalization
    is driven by `winner_id is not None`, not by the flag.
    """
    # Identity preference: espn_id (stable across reschedules) → fall back
    # to (date, team_a_id, team_b_id) for legacy rows without an espn_id.
    # Without this, an ESPN reschedule (same event, new date) would leave
    # the old completed row stale while inserting a fresh row at the new date.
    game = None
    if espn_id:
        game = session.query(Game).filter(Game.espn_id == espn_id).first()
    if game is None:
        game = (
            session.query(Game)
            .filter(
                Game.date == date,
                Game.team_a_id == team_a_id,
                Game.team_b_id == team_b_id,
            )
            .first()
        )
    if game:
        # If any source-of-truth field for excitement changes (espn_id,
        # winner_id, final scores), invalidate the cached excitement so
        # the backfill recomputes from the corrected PBP. Without this,
        # ESPN correcting a finalized game would leave the archive sorted
        # on stale data forever — beyond the bounded refresh window the
        # value would otherwise be locked.
        def _changed(old, new) -> bool:
            return new is not None and new != "" and new != old

        invalidate_excitement = game.excitement_index is not None and (
            _changed(game.espn_id, espn_id)
            or _changed(game.winner_id, winner_id)
            or _changed(game.final_score_a, final_score_a)
            or _changed(game.final_score_b, final_score_b)
            or _changed(game.team_a_id, team_a_id)
            or _changed(game.team_b_id, team_b_id)
        )
        # ESPN says this game is no longer final (postponed, protested, data
        # correction). Clear stored completion + cached excitement so the
        # archive doesn't keep showing the stale result.
        un_finalized = is_complete is False and game.winner_id is not None
        if winner_id is not None:
            game.winner_id = winner_id
            game.final_score_a = final_score_a
            game.final_score_b = final_score_b
        elif un_finalized:
            game.winner_id = None
            game.final_score_a = None
            game.final_score_b = None
        if broadcaster:
            game.broadcaster = broadcaster
        # Combined TBD signal: empty `time` + explicit `time_utc=None`.
        # `time` alone preserves (transient ESPN empties; CLAUDE.md gotcha).
        explicit_tbd = time_utc is not _UNSET and time_utc is None and time == ""
        if time:
            game.time = time
        elif explicit_tbd:
            game.time = ""
        if time_utc is not _UNSET:
            game.time_utc = time_utc
        if espn_id:
            game.espn_id = espn_id
        if season_type is not None:
            game.season_type = season_type
        # If we matched by espn_id, the row's date/teams may differ
        # (reschedule, or ESPN correcting the matchup itself).
        old_key = (game.date, game.team_a_id, game.team_b_id)
        if date and game.date != date:
            game.date = date
        if game.team_a_id != team_a_id:
            game.team_a_id = team_a_id
        if game.team_b_id != team_b_id:
            game.team_b_id = team_b_id
        new_key = (game.date, game.team_a_id, game.team_b_id)
        if new_key != old_key:
            # The DailyRanking at the old key is for THIS game (this is
            # the same espn_id, just moved). Re-key it to the new
            # (date, team_a_id, team_b_id) so the pre-game quality /
            # importance / overall scores follow the game. Deleting it
            # would leave a completed-archive entry permanently degraded
            # with None scores after the move. Fall back to delete only
            # when something already exists at the new key (the new-key
            # ranking takes precedence as more current).
            old_ranking = (
                session.query(DailyRanking)
                .filter(
                    DailyRanking.date == old_key[0],
                    DailyRanking.team_a_id == old_key[1],
                    DailyRanking.team_b_id == old_key[2],
                )
                .first()
            )
            if old_ranking is not None:
                conflict = (
                    session.query(DailyRanking)
                    .filter(
                        DailyRanking.date == new_key[0],
                        DailyRanking.team_a_id == new_key[1],
                        DailyRanking.team_b_id == new_key[2],
                    )
                    .first()
                )
                if conflict is None:
                    old_ranking.date = new_key[0]
                    old_ranking.team_a_id = new_key[1]
                    old_ranking.team_b_id = new_key[2]
                else:
                    session.delete(old_ranking)
        if excitement_index is not None:
            game.excitement_index = excitement_index
        elif invalidate_excitement or un_finalized:
            game.excitement_index = None
            game.excitement_computed_at = None
            game.excitement_last_attempt_at = None
        session.commit()
        return game

    # On insert, the sentinel means "caller didn't specify" — store NULL
    # (same as an explicit None). The retry path forwards `time_utc`
    # verbatim so the sentinel reaches us here for unspecified callers.
    insert_time_utc = None if time_utc is _UNSET else time_utc
    game = Game(
        team_a_id=team_a_id,
        team_b_id=team_b_id,
        date=date,
        time=time,
        time_utc=insert_time_utc,
        broadcaster=broadcaster,
        winner_id=winner_id,
        final_score_a=final_score_a,
        final_score_b=final_score_b,
        espn_id=espn_id,
        excitement_index=excitement_index,
        season_type=season_type,
    )
    session.add(game)
    try:
        session.commit()
    except IntegrityError:
        # Another writer inserted the same espn_id concurrently (overlapping
        # daily-update runs). Roll back and re-enter — the second pass will
        # match the row via espn_id and take the update path.
        session.rollback()
        if _retry:
            raise
        return upsert_game(
            session,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            date=date,
            time=time,
            broadcaster=broadcaster,
            winner_id=winner_id,
            final_score_a=final_score_a,
            final_score_b=final_score_b,
            espn_id=espn_id,
            excitement_index=excitement_index,
            is_complete=is_complete,
            season_type=season_type,
            time_utc=time_utc,
            _retry=True,
        )
    return game


def insert_game(
    session: Session,
    team_a_id: int,
    team_b_id: int,
    date: str,
    time: str,
    broadcaster: str,
    winner_id: int | None = None,
    final_score_a: int | None = None,
    final_score_b: int | None = None,
) -> Game:
    """Insert a new game."""
    game = Game(
        team_a_id=team_a_id,
        team_b_id=team_b_id,
        date=date,
        time=time,
        broadcaster=broadcaster,
        winner_id=winner_id,
        final_score_a=final_score_a,
        final_score_b=final_score_b,
    )
    session.add(game)
    session.commit()
    return game


def get_games_by_date(session: Session, date: str) -> list[Game]:
    """Get all games for a specific date."""
    return session.query(Game).filter(Game.date == date).order_by(Game.time).all()


def get_all_known_espn_ids(session: Session) -> set[str]:
    """Return every non-null espn_id in the games table.

    Used to gate /api/live-wp so the endpoint only fans out to ESPN for ids
    that exist in our database — otherwise an attacker enumerating numeric
    ids could force one outbound 10s ESPN call per id.
    """
    rows = session.query(Game.espn_id).filter(Game.espn_id.isnot(None)).all()
    return {r[0] for r in rows}


@dataclass(frozen=True)
class GameFields:
    """Per-game metadata joined into ranking responses. Broadcaster is
    sourced here (not from DailyRanking) because Game.broadcaster is
    refreshed by every daily run and reflects late corrections, while
    DailyRanking.broadcaster freezes at scoring time."""

    time: str
    time_utc: str | None
    espn_id: str | None
    final_score_a: int | None
    final_score_b: int | None
    excitement_index: float | None
    broadcaster: str


def get_game_fields(
    session: Session, keys: list[tuple[str, int, int]]
) -> dict[tuple[str, int, int], GameFields]:
    """Return {(date, team_a_id, team_b_id): GameFields} for the given keys."""
    if not keys:
        return {}
    dates = {k[0] for k in keys}
    team_ids = {k[1] for k in keys} | {k[2] for k in keys}
    games = (
        session.query(Game)
        .filter(
            Game.date.in_(dates),
            Game.team_a_id.in_(team_ids),
            Game.team_b_id.in_(team_ids),
        )
        .all()
    )
    wanted = set(keys)
    return {
        (g.date, g.team_a_id, g.team_b_id): GameFields(
            time=g.time or "",
            time_utc=g.time_utc,
            espn_id=g.espn_id,
            final_score_a=g.final_score_a,
            final_score_b=g.final_score_b,
            excitement_index=g.excitement_index,
            broadcaster=g.broadcaster or "",
        )
        for g in games
        if (g.date, g.team_a_id, g.team_b_id) in wanted
    }


def get_upcoming_games(session: Session, start_date: str) -> list[Game]:
    """Get upcoming games starting from a date."""
    return (
        session.query(Game)
        .filter(Game.date >= start_date)
        .filter(Game.winner_id.is_(None))  # Only unplayed games
        .order_by(Game.date, Game.time)
        .all()
    )


def get_completed_games(session: Session, season_year: int = 2026) -> list[Game]:
    """Completed regular-season + postseason games for a season.

    Filter is conditional on backfill state:

    * If any 2026 row still has `season_type IS NULL`, we're mid-backfill
      (or backfill failed). Use the looser `IS NULL OR != 1` filter so
      legacy regular-season games stay in standings — losing them would
      corrupt wins/losses worse than a stray legacy preseason row would.
    * Once all rows have `season_type` populated, switch to the strict
      `IN (2, 3)` filter — fail-closed so a partial ESPN backfill that
      leaves a row NULL can't sneak a preseason game in.

    Archive queries (`get_completed_rankings` etc.) always use the
    looser filter — they're user-facing display, so prefer inclusivity.
    """
    null_count = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.season_type.is_(None))
        .count()
    )
    base = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
    )
    if null_count == 0:
        base = base.filter(Game.season_type.in_([2, 3]))
    else:
        base = base.filter(_NOT_PRESEASON)
    return base.order_by(Game.date, Game.time).all()


def get_team_records(
    session: Session, season_year: int = 2026
) -> dict[int, tuple[int, int]]:
    """Regular-season W-L record per team for a season, from completed games.

    Returns {team_id: (wins, losses)}; teams with no completed regular-season
    games are absent (callers default to 0-0).

    Counts the whole season-to-date — no as-of-date bound. A date cutoff can't
    be made consistent with a past odds snapshot anyway (the correct cutoff is
    which games were FINAL at snapshot time, unrecoverable from a date), so the
    `/api/playoff-odds` endpoint instead only attaches this record to a *today*
    request, where it's current and consistent with the live odds.

    Deliberately strict on `season_type == 2`: a W-L record is a precise
    standings claim, so any non-regular-season row is excluded. The 2026 DB
    carries NULL-`season_type` rows, but they're uncompleted preseason
    placeholders (no `winner_id`) — already dropped by the `winner_id` filter,
    and excluding them is correct even if one completed. This is intentionally
    NOT the looser null-count-conditional filter `get_completed_games` uses
    (which keeps NULL rows for the completed *archive*); for a W-L record a
    stray preseason game would be a miscount.
    """
    games = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(Game.season_type == 2)
        .all()
    )
    records: dict[int, list[int]] = {}
    for g in games:
        # winner_id is always team_a_id or team_b_id (enforced on write).
        loser_id = g.team_b_id if g.winner_id == g.team_a_id else g.team_a_id
        records.setdefault(g.winner_id, [0, 0])[0] += 1
        records.setdefault(loser_id, [0, 0])[1] += 1
    return {tid: (w, losses) for tid, (w, losses) in records.items()}


def get_completed_postseason_games(
    session: Session, season_year: int = 2026
) -> list[Game]:
    """Completed postseason games for a season (season_type=3 only).

    Used by `_build_current_bracket_state` to source the full playoff
    history from the DB rather than the rolling ESPN fetch window
    (which only covers yesterday-forward and would forget games >1 day
    old, e.g. Game 1 of a Bo3 played 4 days ago).
    """
    return (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(Game.season_type == 3)
        .order_by(Game.date, Game.time)
        .all()
    )


def get_head_to_head(
    session: Session, team_a_id: int, team_b_id: int, season_year: int = 2026
) -> list[Game]:
    """Completed regular-season + postseason games this season between exactly
    these two teams, chronological. Order-agnostic: matches both home/away
    arrangements. Excludes preseason (season_type=1) via `_NOT_PRESEASON`, like
    the other user-facing completed-game queries; legacy NULL season_type rows
    are still included.

    Used by the game detail page. Current-season only — the DB holds only
    the current season's games (Elo history is fetched fresh each run and
    not persisted), so multi-season H2H isn't available here.
    """
    pair_a = (Game.team_a_id == team_a_id) & (Game.team_b_id == team_b_id)
    pair_b = (Game.team_a_id == team_b_id) & (Game.team_b_id == team_a_id)
    return (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(
            _NOT_PRESEASON
        )  # exclude preseason (season_type=1), like the archive queries
        .filter(or_(pair_a, pair_b))
        .order_by(Game.date, Game.time)
        .all()
    )


def get_completed_games_missing_excitement(
    session: Session, season_year: int = 2026, limit: int | None = None
) -> list[Game]:
    """Completed games for `season_year` that still need excitement_index computed.

    A game is "completed" when winner_id is set. An espn_id is required because
    the computation needs ESPN play-by-play; games without one can't be backfilled.

    Order: least-recently-attempted first (NULL `excitement_last_attempt_at`
    counts as "never attempted" and comes first), then newer dates within
    each attempt bucket. Without this, a 50-cap + newest-first ordering would
    let a cluster of permanently-failing newest games starve older NULL rows
    indefinitely — the same head would be retried every run.

    Pass `limit` to bound retry work per run.
    """
    q = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(Game.excitement_index.is_(None))
        .filter(Game.espn_id.isnot(None))
        .filter(_NOT_PRESEASON)
        # `IS NOT NULL` returns 0 for NULL and 1 for set; ASC puts NULL
        # (never-attempted) ahead of any timestamp, portably across SQLite
        # and Postgres. Then by attempt timestamp ASC (oldest first), then
        # by date DESC so the most user-visible rows are surfaced first
        # within each bucket.
        .order_by(
            Game.excitement_last_attempt_at.isnot(None),
            Game.excitement_last_attempt_at.asc(),
            Game.date.desc(),
        )
    )
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def get_games_for_excitement_refresh(
    session: Session,
    cutoff: datetime | None,
    season_year: int = 2026,
    limit: int | None = None,
) -> list[Game]:
    """Games whose excitement_index was computed recently enough that ESPN
    may still revise the underlying PBP — bounded freshness window for
    handling late ESPN corrections to a STATUS_FINAL game.

    `cutoff` is a datetime; rows with `excitement_computed_at >= cutoff`
    are eligible. `cutoff=None` (the batch-recompute path) drops the
    freshness window entirely: every completed game with a stored
    excitement_index qualifies, including legacy rows whose
    excitement_computed_at is NULL (the column shipped 2026-05-16 with no
    backfill of earlier rows). An espn_id is required in every mode — a row
    without one can never be re-fetched, and selecting it would permanently
    wedge the recompute's fail-closed exit code.

    Order: least-recently-touched first (NULL
    `excitement_last_attempt_at` first, then ASC), so when more eligible
    rows share the same `excitement_computed_at` than the per-run cap
    allows — e.g. right after the one-shot backfill stamps many rows
    with the same `now` — the queue rotates through the full set
    instead of repeatedly hitting the same head until rows age out.
    """
    q = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(Game.excitement_index.isnot(None))
        .filter(Game.espn_id.isnot(None))
        .filter(_NOT_PRESEASON)
    )
    if cutoff is not None:
        q = q.filter(Game.excitement_computed_at.isnot(None)).filter(
            Game.excitement_computed_at >= cutoff
        )
    q = q.order_by(
        Game.excitement_last_attempt_at.isnot(None),
        Game.excitement_last_attempt_at.asc(),
        Game.excitement_computed_at.asc(),
    )
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def update_game_result(
    session: Session,
    game_id: int,
    winner_id: int,
    final_score_a: int,
    final_score_b: int,
) -> Game:
    """Update game result after it's been played."""
    game = session.query(Game).filter(Game.id == game_id).first()
    if game:
        game.winner_id = winner_id
        game.final_score_a = final_score_a
        game.final_score_b = final_score_b
        session.commit()
    return game


def insert_daily_ranking(
    session: Session,
    date: str,
    team_a_id: int,
    team_b_id: int,
    quality_score: float,
    importance_score: float,
    overall_score: float,
    broadcaster: str,
) -> DailyRanking:
    """Insert a daily game ranking."""
    ranking = DailyRanking(
        date=date,
        team_a_id=team_a_id,
        team_b_id=team_b_id,
        quality_score=quality_score,
        importance_score=importance_score,
        overall_score=overall_score,
        broadcaster=broadcaster,
    )
    session.add(ranking)
    session.commit()
    return ranking


def get_daily_rankings(session: Session, date: str) -> list[DailyRanking]:
    return (
        session.query(DailyRanking)
        .filter(DailyRanking.date == date)
        .order_by(DailyRanking.overall_score.desc())
        .all()
    )


def get_upcoming_rankings(session: Session, start_date: str) -> list[DailyRanking]:
    """Rankings for games not yet completed, from `start_date` onward.

    Outer-joins `Game` on the matchup key and excludes rows with a
    recorded winner, so a same-day final can't leak into the upcoming
    list (it previously relied on the frontend date-floor to stay
    hidden). Outer join: a ranking with no matching Game row is an
    anomaly but stays visible — only a positively-known winner excludes.
    """
    return (
        session.query(DailyRanking)
        .outerjoin(
            Game,
            (Game.date == DailyRanking.date)
            & (Game.team_a_id == DailyRanking.team_a_id)
            & (Game.team_b_id == DailyRanking.team_b_id),
        )
        .filter(DailyRanking.date >= start_date)
        .filter(Game.winner_id.is_(None))
        .order_by(DailyRanking.date, DailyRanking.overall_score.desc())
        .all()
    )


def get_completed_rankings(
    session: Session,
    season_year: int = 2026,
    broadcaster: str | None = None,
) -> list[DailyRanking]:
    """DailyRanking rows for completed games in `season_year`, sorted by
    excitement_index descending (NULLs last, ties broken by date descending).

    Sources from `Game` (not `DailyRanking`) so a completed game that
    somehow lacks a ranking row — e.g. a missed daily-update day —
    still appears in the archive. Missing rankings are filled with a
    transient `DailyRanking` carrying None for the scored fields.

    Games with `winner_id IS NULL` are excluded. Games with NULL
    excitement_index are *included* and sorted last, so a persistent
    ESPN PBP outage doesn't silently delete real completed games from
    the archive. NULL remains the retry signal in
    `get_completed_games_missing_excitement`.

    `broadcaster` (optional) filters at the `Game.broadcaster` level —
    the source of truth, refreshed every daily run — so late ESPN
    broadcaster corrections take effect immediately.
    """
    q = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(_NOT_PRESEASON)
    )
    if broadcaster is not None:
        q = q.filter(Game.broadcaster == broadcaster)
    # `excitement_index IS NULL` evaluates to 0/1 (SQLite) or false/true
    # (Postgres); ASC orders non-null first, NULL last, portably.
    games = q.order_by(
        Game.excitement_index.is_(None),
        Game.excitement_index.desc(),
        Game.date.desc(),
    ).all()
    if not games:
        return []
    # Only look up DailyRanking for the matched games (small set when a
    # broadcaster filter narrows the query), not the whole season.
    game_dates = {g.date for g in games}
    game_keys = {(g.date, g.team_a_id, g.team_b_id) for g in games}
    rankings_by_key = {
        (r.date, r.team_a_id, r.team_b_id): r
        for r in session.query(DailyRanking)
        .filter(DailyRanking.date.in_(game_dates))
        .all()
        if (r.date, r.team_a_id, r.team_b_id) in game_keys
    }
    result: list[DailyRanking] = []
    for g in games:
        ranking = rankings_by_key.get((g.date, g.team_a_id, g.team_b_id))
        if ranking is None:
            ranking = DailyRanking(
                date=g.date,
                team_a_id=g.team_a_id,
                team_b_id=g.team_b_id,
                quality_score=None,
                importance_score=None,
                overall_score=None,
                broadcaster=g.broadcaster or "",
                win_prob_a=None,
            )
        result.append(ranking)
    return result


def get_calibration_pairs(
    session: Session, season_year: int = 2026
) -> list[tuple[float, bool]]:
    """(predicted win_prob_a, team_a_won) for completed regular/post-season
    games that have a frozen DailyRanking.win_prob_a. Rows without a stored
    win_prob_a are dropped. Used by /api/calibration and the audit script."""
    # Single inner JOIN on the matchup key: returns only completed games that
    # have a frozen win_prob_a, so there's no over-fetch and no Python-side
    # join. Calibration is from team_a's (the home team's) perspective — one
    # prediction per game; home teams are both favorites and underdogs, so
    # win_prob_a still spans the full 0..1 range.
    rows = (
        session.query(DailyRanking.win_prob_a, Game.winner_id, Game.team_a_id)
        .join(
            Game,
            (Game.date == DailyRanking.date)
            & (Game.team_a_id == DailyRanking.team_a_id)
            & (Game.team_b_id == DailyRanking.team_b_id),
        )
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(_NOT_PRESEASON)
        .filter(DailyRanking.win_prob_a.isnot(None))
        .all()
    )
    return [
        (win_prob_a, winner_id == team_a_id)
        for win_prob_a, winner_id, team_a_id in rows
    ]


def get_rankings_by_broadcaster(
    session: Session,
    start_date: str,
    broadcaster: str,
    mode: str = "upcoming",
) -> list[DailyRanking]:
    """Rankings filtered by broadcaster.

    mode="upcoming" (default): date >= start_date, sorted by date asc.
    mode="completed": 2026 completed games sorted by excitement desc.
                      `start_date` is ignored.
    """
    if mode == "completed":
        return get_completed_rankings(
            session, season_year=2026, broadcaster=broadcaster
        )
    return (
        session.query(DailyRanking)
        .filter(DailyRanking.date >= start_date)
        .filter(DailyRanking.broadcaster == broadcaster)
        .order_by(DailyRanking.date, DailyRanking.overall_score.desc())
        .all()
    )


def upsert_daily_ranking(
    session: Session,
    date: str,
    team_a_id: int,
    team_b_id: int,
    quality_score: float,
    importance_score: float | None,
    overall_score: float,
    broadcaster: str,
    win_prob_a: float | None = None,
    importance_detail: str | None = None,
) -> DailyRanking:
    ranking = (
        session.query(DailyRanking)
        .filter(
            DailyRanking.date == date,
            DailyRanking.team_a_id == team_a_id,
            DailyRanking.team_b_id == team_b_id,
        )
        .first()
    )
    if ranking:
        ranking.quality_score = quality_score
        ranking.importance_score = importance_score
        ranking.overall_score = overall_score
        ranking.broadcaster = broadcaster
        ranking.win_prob_a = win_prob_a
        ranking.importance_detail = importance_detail
    else:
        ranking = DailyRanking(
            date=date,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            quality_score=quality_score,
            importance_score=importance_score,
            overall_score=overall_score,
            broadcaster=broadcaster,
            win_prob_a=win_prob_a,
            importance_detail=importance_detail,
        )
        session.add(ranking)
    session.commit()
    return ranking


# Arbitrary namespace paired with the season year for the per-season
# elo_history advisory lock, so it can't collide with other advisory locks.
_ELO_HISTORY_LOCK_NS = 8412


def _acquire_elo_history_lock(session: Session, season_year: int) -> None:
    """Take a transaction-scoped Postgres advisory lock for one season's
    elo_history rewrite so two overlapping daily-update runs serialize instead
    of interleaving the delete-and-reinsert (the table has no unique key, so an
    interleave could duplicate or wipe rows). Auto-released at COMMIT/ROLLBACK.
    No-op on SQLite (tests), which has no cross-connection write concurrency."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :yr)"),
        {"ns": _ELO_HISTORY_LOCK_NS, "yr": season_year},
    )


def replace_elo_history(
    session: Session,
    season_year: int,
    rows: list[tuple[int, str, float]],
) -> None:
    """Delete-and-rewrite all elo_history rows for one season.

    `rows` is (team_id, date, rating). The trajectory is a deterministic replay,
    so rewriting the whole season each run is idempotent and avoids stale rows.
    The delete is scoped by year so other seasons are untouched.

    Serialized per season via an advisory lock (see `_acquire_elo_history_lock`)
    so overlapping daily-update runs can't interleave and corrupt the rewrite.
    """
    _acquire_elo_history_lock(session, season_year)
    session.query(EloHistory).filter(EloHistory.date.like(f"{season_year}-%")).delete(
        synchronize_session=False
    )
    for team_id, d, rating in rows:
        session.add(EloHistory(team_id=team_id, date=d, rating=rating))
    session.commit()


def get_elo_history(session: Session, season_year: int) -> list[EloHistory]:
    """All elo_history rows for a season, ordered by (date, team_id).

    The team_id secondary sort makes per-team chronological order explicit
    rather than relying on the global date sort happening to interleave teams
    correctly (e.g. two rows sharing a date)."""
    return (
        session.query(EloHistory)
        .filter(EloHistory.date.like(f"{season_year}-%"))
        .order_by(EloHistory.date, EloHistory.team_id)
        .all()
    )


@dataclass
class PlayoffProbabilityRecord:
    """In-memory record of one team's per-round playoff probabilities."""

    make_playoffs_prob: float
    reach_semis_prob: float | None
    reach_finals_prob: float | None
    win_championship_prob: float | None
    seed_distribution: dict[int, float] | None = None


def upsert_playoff_probability(
    session: Session,
    date: str,
    team_id: int,
    probability: float,
    reach_semis_prob: float | None = None,
    reach_finals_prob: float | None = None,
    win_championship_prob: float | None = None,
    seed_distribution: str | None = None,
) -> PlayoffProbability:
    """Upsert a team's playoff probabilities for a given date.

    `probability` retains its original meaning: "make playoffs" probability.
    Round-by-round columns are nullable; pass None to leave a column untouched
    on update (only specified columns are written).
    """
    record = (
        session.query(PlayoffProbability)
        .filter(PlayoffProbability.date == date, PlayoffProbability.team_id == team_id)
        .first()
    )
    if record:
        record.probability = probability
        if reach_semis_prob is not None:
            record.reach_semis_prob = reach_semis_prob
        if reach_finals_prob is not None:
            record.reach_finals_prob = reach_finals_prob
        if win_championship_prob is not None:
            record.win_championship_prob = win_championship_prob
        if seed_distribution is not None:
            record.seed_distribution = seed_distribution
    else:
        record = PlayoffProbability(
            date=date,
            team_id=team_id,
            probability=probability,
            reach_semis_prob=reach_semis_prob,
            reach_finals_prob=reach_finals_prob,
            win_championship_prob=win_championship_prob,
            seed_distribution=seed_distribution,
        )
        session.add(record)
    session.commit()
    return record


def _parse_seed_distribution(raw: str | None) -> dict[int, float] | None:
    """Parse a stored seed_distribution JSON ({"1": p1, ...}) into
    {seed_int: prob}. Returns None for NULL/empty or any malformed payload so a
    single bad row can't 500 /api/playoff-odds."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return {int(k): float(v) for k, v in data.items()}
    except (ValueError, TypeError):
        return None


def get_playoff_probabilities(
    session: Session, date: str
) -> dict[int, PlayoffProbabilityRecord]:
    """Return {team_id: PlayoffProbabilityRecord} for all teams on the given date."""
    records = (
        session.query(PlayoffProbability).filter(PlayoffProbability.date == date).all()
    )
    return {
        r.team_id: PlayoffProbabilityRecord(
            make_playoffs_prob=r.probability,
            reach_semis_prob=r.reach_semis_prob,
            reach_finals_prob=r.reach_finals_prob,
            win_championship_prob=r.win_championship_prob,
            seed_distribution=_parse_seed_distribution(r.seed_distribution),
        )
        for r in records
    }


def get_latest_playoff_probability_date(
    session: Session, on_or_before: str
) -> str | None:
    """Most recent date (<= on_or_before) with a POPULATED playoff snapshot, or None.

    "Populated" = at least one row with a non-NULL win_championship_prob, matching
    the /api/playoff-odds endpoint's own row filter (legacy pre-round-prob rows are
    skipped there, so they must not count as a snapshot here either). Lets the
    endpoint fall back to the freshest available day when today's rows aren't
    written yet (the pre-6AM daily-run window / a missed run), so the dedicated
    /playoff-odds page shows recent odds instead of a blank shell.
    """
    return (
        session.query(func.max(PlayoffProbability.date))
        .filter(PlayoffProbability.date <= on_or_before)
        .filter(PlayoffProbability.win_championship_prob.isnot(None))
        .scalar()
    )


def upsert_game_shape(
    session: Session,
    *,
    espn_id: str,
    season: int,
    date: str,
    home_team: str,
    away_team: str,
    home_abbr: str,
    away_abbr: str,
    home_score: int,
    away_score: int,
    winner: str,
    excitement: float,
    tension: float,
    comeback: float,
    lead_changes: int,
    winner_low_wp: float,
    curve: list,
    _retry: bool = False,
) -> GameShape:
    """Insert or update the game_shapes row for `espn_id` (idempotent). `curve`
    is a Python list, serialized to JSON here.

    Commits per row and, like `upsert_game`, retries once on `IntegrityError`
    so a concurrent insert of the same `espn_id` (an overlapping daily run / the
    one-shot backfill racing it) is contained to this row — the retry re-queries
    and takes the update path — instead of failing the caller's whole batch."""
    row = session.query(GameShape).filter(GameShape.espn_id == espn_id).first()
    if row is None:
        row = GameShape(espn_id=espn_id)
        session.add(row)
    row.season = season
    row.date = date
    row.home_team = home_team
    row.away_team = away_team
    row.home_abbr = home_abbr
    row.away_abbr = away_abbr
    row.home_score = home_score
    row.away_score = away_score
    row.winner = winner
    row.excitement = excitement
    row.tension = tension
    row.comeback = comeback
    row.lead_changes = lead_changes
    row.winner_low_wp = winner_low_wp
    row.curve = json.dumps(curve)
    row.computed_at = datetime.now()
    try:
        session.commit()
    except IntegrityError:
        # Another writer inserted this espn_id concurrently — roll back and
        # re-enter; the second pass matches the row via espn_id and updates.
        session.rollback()
        if _retry:
            raise
        return upsert_game_shape(
            session,
            espn_id=espn_id,
            season=season,
            date=date,
            home_team=home_team,
            away_team=away_team,
            home_abbr=home_abbr,
            away_abbr=away_abbr,
            home_score=home_score,
            away_score=away_score,
            winner=winner,
            excitement=excitement,
            tension=tension,
            comeback=comeback,
            lead_changes=lead_changes,
            winner_low_wp=winner_low_wp,
            curve=curve,
            _retry=True,
        )
    return row


def delete_game_shape(session: Session, espn_id: str) -> bool:
    """Delete the game_shapes row for `espn_id` (commits). True if a row was
    removed. Caller: the backfill's --recompute --purge-unshapeable mode."""
    deleted = session.query(GameShape).filter(GameShape.espn_id == espn_id).delete()
    session.commit()
    return bool(deleted)


def get_existing_shape_espn_ids(session: Session, espn_ids: list[str]) -> set[str]:
    """The subset of `espn_ids` that already have a game_shapes row (backfill
    skip-set). Empty input -> empty set (avoids an empty IN clause)."""
    if not espn_ids:
        return set()
    rows = (
        session.query(GameShape.espn_id).filter(GameShape.espn_id.in_(espn_ids)).all()
    )
    return {r[0] for r in rows}


def get_team_abbrev_map(session: Session) -> dict[str, str]:
    """Canonical team name -> abbreviation, for compact card display."""
    return {t.name: t.abbreviation for t in get_all_teams(session)}


def get_completed_games_missing_shape(
    session: Session, season_year: int = 2026, limit: int | None = None
) -> list[Game]:
    """Completed `season_year` games (in the games table) with an espn_id but no
    game_shapes row yet - the daily-update candidate set.

    Ordered least-recently-attempted first (NULL `game_shape_last_attempt_at` =
    never attempted, surfaced first), then by attempt time, then date — the same
    rotation `get_completed_games_missing_excitement` uses, so a permanently-
    failing game rotates to the back instead of monopolizing the capped queue."""
    existing = session.query(GameShape.espn_id)
    q = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(Game.espn_id.isnot(None))
        .filter(Game.espn_id.notin_(existing))
        .filter(_NOT_PRESEASON)
        .order_by(
            Game.game_shape_last_attempt_at.isnot(None),
            Game.game_shape_last_attempt_at.asc(),
            Game.date.desc(),
        )
    )
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def get_game_shapes(session: Session, season: int) -> list[GameShape]:
    """All stored game shapes for a season, newest first. DB-only reader for the
    /replay archive — the page re-sorts client-side by the active metric, so the
    order here is only the fallback."""
    return (
        session.query(GameShape)
        .filter(GameShape.season == season)
        .order_by(GameShape.date.desc())
        .all()
    )


def get_shape_seasons(session: Session) -> list[int]:
    """Distinct seasons present in game_shapes, newest first — powers the
    /replay season selector so it never offers an empty season."""
    rows = (
        session.query(GameShape.season)
        .distinct()
        .order_by(GameShape.season.desc())
        .all()
    )
    return [r[0] for r in rows]


def get_shape_by_espn_id(session: Session, espn_id: str) -> GameShape | None:
    """The stored game shape for one game, or None. DB-only reader for the
    detail-page 'Game shape' panel; looked up by espn_id (the table's key)."""
    return session.query(GameShape).filter(GameShape.espn_id == espn_id).first()


def get_shapes_by_espn_ids(
    session: Session, espn_ids: list[str] | set[str]
) -> dict[str, GameShape]:
    """Game shapes for many games in one query, keyed by espn_id. DB-only
    reader for the homepage completed-section mini fever-lines; ids with no
    stored shape are simply absent from the returned dict (caller renders no
    mini for them). GameShape is self-contained — no join needed."""
    if not espn_ids:
        return {}
    rows = session.query(GameShape).filter(GameShape.espn_id.in_(list(espn_ids))).all()
    return {row.espn_id: row for row in rows}


def upsert_team_style(
    session: Session,
    *,
    season: int,
    team_id: int,
    pace: float,
    three_pa_rate: float,
    ft_rate: float,
    oreb_pct: float,
    assist_rate: float,
    def_pressure: float,
    opp_3pa_rate: float,
    games_played: int,
    commit: bool = True,
) -> TeamStyle:
    """Insert or update the team_style row for (season, team_id). Idempotent.
    Commits by default (mirrors upsert_team); pass commit=False to stage the row
    in the caller's transaction so a whole-season refresh can commit atomically."""
    row = (
        session.query(TeamStyle)
        .filter(TeamStyle.season == season, TeamStyle.team_id == team_id)
        .first()
    )
    if row is None:
        row = TeamStyle(season=season, team_id=team_id)
        session.add(row)
    row.pace = pace
    row.three_pa_rate = three_pa_rate
    row.ft_rate = ft_rate
    row.oreb_pct = oreb_pct
    row.assist_rate = assist_rate
    row.def_pressure = def_pressure
    row.opp_3pa_rate = opp_3pa_rate
    row.games_played = games_played
    if commit:
        session.commit()
    return row


def get_team_styles(session: Session, season: int) -> list[TeamStyle]:
    """All team_style rows for a season (DB-only reader for /api/team-style)."""
    return session.query(TeamStyle).filter(TeamStyle.season == season).all()


def get_team_style_seasons(session: Session) -> list[int]:
    """Distinct seasons present in team_style, newest first — lets the endpoint
    default to the newest POPULATED season (mirrors get_shape_seasons)."""
    rows = (
        session.query(TeamStyle.season)
        .distinct()
        .order_by(TeamStyle.season.desc())
        .all()
    )
    return [r[0] for r in rows]


def get_team_style_season_counts(session: Session) -> dict[int, int]:
    """Row (team) count per season in team_style — a completeness proxy. The
    daily refresh fails closed when a batch covers fewer teams than the prior
    snapshot, and the endpoint avoids defaulting to a sparse newest season."""
    counts: dict[int, int] = {}
    for (season,) in session.query(TeamStyle.season).all():
        counts[season] = counts.get(season, 0) + 1
    return counts


def delete_team_style_season(session: Session, season: int) -> None:
    """Delete all team_style rows for a season (no commit). The daily refresh
    replaces the whole season in one transaction so a team absent from the feed
    (a rename/remap/drop) can't leave a stale row poisoning the league view."""
    session.query(TeamStyle).filter(TeamStyle.season == season).delete(
        synchronize_session=False
    )


def delete_shot_making_season(session: Session, season: int) -> None:
    """Delete all shot_making rows for a season (no commit). The daily recompute
    replaces the whole board in one transaction so a player who drops below the
    eligibility cutoff (or is removed by a corrected re-ingest) can't leave a
    stale row the DB-only /api/shot-making endpoint keeps serving."""
    session.query(ShotMaking).filter(ShotMaking.season == season).delete(
        synchronize_session=False
    )


def delete_shot_league_avg_season(session: Session, season: int) -> None:
    """Delete the season's all-league anchor row (no commit). Called alongside
    delete_shot_making_season so the anchors are replaced wholesale with the
    board they describe: a season that loses every shot must not keep serving
    the previous run's league averages under an empty leaderboard."""
    session.query(ShotLeagueAvg).filter(ShotLeagueAvg.season == season).delete(
        synchronize_session=False
    )


def upsert_shots(
    session: Session,
    espn_game_id: str,
    season: int,
    shots: list[dict],
    *,
    commit: bool = True,
) -> int:
    """Insert FGA rows for a game, idempotent on (espn_game_id, play_id). Returns
    the count newly inserted (already-present play_ids are skipped). Dedups WITHIN
    the incoming batch too, so a duplicate play_id in one ESPN payload can't hit
    the uq_shot_play constraint and abort the game's transaction."""
    existing = {
        r[0]
        for r in session.query(Shot.play_id)
        .filter(Shot.espn_game_id == espn_game_id)
        .all()
    }
    inserted = 0
    for s in shots:
        if s["play_id"] in existing:
            continue
        existing.add(s["play_id"])  # guard against dupes within this batch
        session.add(
            Shot(
                espn_game_id=espn_game_id,
                play_id=s["play_id"],
                season=season,
                athlete_id=s["athlete_id"],
                athlete_name=s["athlete_name"],
                team_id=s["team_id"],
                team_abbr=s.get("team_abbr", ""),
                shot_type=s["shot_type"],
                distance_ft=s["distance_ft"],
                coord_x=s["coord_x"],
                coord_y=s["coord_y"],
                points=s["points"],
                point_value=s["point_value"],
                made=s["made"],
            )
        )
        inserted += 1
    if commit:
        session.commit()
    return inserted


def get_shots_for_season(session: Session, season: int) -> list[Shot]:
    """All shots for a season."""
    return session.query(Shot).filter(Shot.season == season).all()


def get_shots_for_player(session: Session, season: int, athlete_id: str) -> list[Shot]:
    """All of one player's shots for a season (backs /api/player-shots)."""
    return (
        session.query(Shot)
        .filter(Shot.season == season, Shot.athlete_id == athlete_id)
        .all()
    )


def shot_row_to_dict(row: Shot) -> dict:
    """A `shots` row → the plain dict the pure scoring helpers consume
    (build_baseline / compute_leaderboard / compute_player_shot_chart). Includes
    coords for the shot chart; the leaderboard path ignores the extra keys. One
    converter for both callers (the /api/player-shots baseline + the daily
    recompute) so a new Shot field is threaded in one place, not two."""
    return {
        "athlete_id": row.athlete_id,
        "athlete_name": row.athlete_name,
        "team_id": row.team_id,
        "team_abbr": row.team_abbr,
        "shot_type": row.shot_type,
        "distance_ft": row.distance_ft,
        "coord_x": row.coord_x,
        "coord_y": row.coord_y,
        "points": row.points,
        "point_value": row.point_value,
        "made": row.made,
    }


def get_completed_games_missing_shots(
    session: Session, season_year: int = 2026, limit: int | None = None
) -> list[Game]:
    """Completed `season_year` games with an espn_id but no shots rows yet — the
    daily-update candidate set. Ordered newest-first."""
    ingested = session.query(Shot.espn_game_id).distinct()
    query = (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
        .filter(Game.espn_id.isnot(None))
        .filter(Game.espn_id.notin_(ingested))
        .filter(_NOT_PRESEASON)
        .order_by(Game.date.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def upsert_shot_making(
    session: Session,
    season: int,
    athlete_id: str,
    *,
    athlete_name: str,
    team_id: str,
    team_abbr: str = "",
    fga: int,
    made: int,
    actual_pts: float,
    expected_pts: float,
    points_added: float,
    points_added_per_100: float,
    actual_pps: float,
    expected_pps: float,
    diet: str,
    commit: bool = True,
) -> ShotMaking:
    """Upsert a shot_making row for (season, athlete_id). Idempotent."""
    row = (
        session.query(ShotMaking)
        .filter(ShotMaking.season == season, ShotMaking.athlete_id == athlete_id)
        .first()
    )
    if row is None:
        row = ShotMaking(season=season, athlete_id=athlete_id)
        session.add(row)
    row.athlete_name = athlete_name
    row.team_id = team_id
    row.team_abbr = team_abbr
    row.fga = fga
    row.made = made
    row.actual_pts = actual_pts
    row.expected_pts = expected_pts
    row.points_added = points_added
    row.points_added_per_100 = points_added_per_100
    row.actual_pps = actual_pps
    row.expected_pps = expected_pps
    row.diet = diet
    if commit:
        session.commit()
    return row


def get_shot_making(session: Session, season: int) -> list[ShotMaking]:
    """All shot_making rows for a season."""
    return session.query(ShotMaking).filter(ShotMaking.season == season).all()


def upsert_shot_league_avg(
    session: Session,
    season: int,
    *,
    avg_xpps: float,
    avg_pps: float,
    fga: int,
    commit: bool = True,
) -> ShotLeagueAvg:
    """Upsert the single all-league anchor row for a season. Idempotent.
    Defaults to commit=False usage inside the daily recompute's transaction."""
    row = session.query(ShotLeagueAvg).filter(ShotLeagueAvg.season == season).first()
    if row is None:
        row = ShotLeagueAvg(season=season)
        session.add(row)
    row.avg_xpps = avg_xpps
    row.avg_pps = avg_pps
    row.fga = fga
    if commit:
        session.commit()
    return row


def get_shot_league_avg(session: Session, season: int) -> ShotLeagueAvg | None:
    """The all-league anchor row for a season, or None before the first daily run."""
    return session.query(ShotLeagueAvg).filter(ShotLeagueAvg.season == season).first()


def has_alerted(session: Session, espn_id: str) -> bool:
    """True if we've already pinged about this game (any date)."""
    return session.query(ThrillerAlert).filter_by(espn_id=espn_id).first() is not None


def record_alert(session: Session, espn_id: str, date: str, label: str) -> None:
    """Persist that we alerted about `espn_id` (game's ET `date`, `label`).
    Idempotent: a duplicate insert (concurrent-poll race) is rolled back and
    swallowed rather than raised, so a loser thread can't 500 the poll."""
    session.add(ThrillerAlert(espn_id=espn_id, date=date, label=label))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
