"""Database query helpers for WNBA Games to Watch."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.schema import (
    DailyRanking,
    EloHistory,
    Game,
    PlayoffProbability,
    SeasonConfig,
    Team,
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
    cutoff: datetime,
    season_year: int = 2026,
    limit: int | None = None,
) -> list[Game]:
    """Games whose excitement_index was computed recently enough that ESPN
    may still revise the underlying PBP — bounded freshness window for
    handling late ESPN corrections to a STATUS_FINAL game.

    `cutoff` is a datetime; rows with `excitement_computed_at >= cutoff`
    are eligible. Order: least-recently-touched first (NULL
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
        .filter(Game.excitement_computed_at.isnot(None))
        .filter(Game.excitement_computed_at >= cutoff)
        .filter(_NOT_PRESEASON)
        .order_by(
            Game.excitement_last_attempt_at.isnot(None),
            Game.excitement_last_attempt_at.asc(),
            Game.excitement_computed_at.asc(),
        )
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
    return (
        session.query(DailyRanking)
        .filter(DailyRanking.date >= start_date)
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


def upsert_playoff_probability(
    session: Session,
    date: str,
    team_id: int,
    probability: float,
    reach_semis_prob: float | None = None,
    reach_finals_prob: float | None = None,
    win_championship_prob: float | None = None,
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
    else:
        record = PlayoffProbability(
            date=date,
            team_id=team_id,
            probability=probability,
            reach_semis_prob=reach_semis_prob,
            reach_finals_prob=reach_finals_prob,
            win_championship_prob=win_championship_prob,
        )
        session.add(record)
    session.commit()
    return record


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
        )
        for r in records
    }


def get_importance_max_swing(session: Session, season_year: int) -> float | None:
    """Return the season-start importance ceiling, or None if not yet computed."""
    cfg = session.get(SeasonConfig, season_year)
    return cfg.importance_max_swing if cfg else None


def save_importance_max_swing(
    session: Session, season_year: int, max_swing: float
) -> None:
    """Persist the season-start importance ceiling (upsert by year)."""
    cfg = session.get(SeasonConfig, season_year)
    if cfg:
        cfg.importance_max_swing = max_swing
    else:
        session.add(
            SeasonConfig(season_year=season_year, importance_max_swing=max_swing)
        )
    session.commit()
