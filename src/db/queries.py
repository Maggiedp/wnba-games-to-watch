"""Database query helpers for WNBA Games to Watch."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.schema import DailyRanking, Game, PlayoffProbability, SeasonConfig, Team


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
    _retry: bool = False,
) -> Game:
    """Upsert a game (insert if not exists, update result if it has been played).

    `is_complete` is an explicit completion flag from the caller. Pass True
    to mark a game final (in which case winner_id + final scores must be
    set), False to clear a previously-stored completion (handles the rare
    case where ESPN un-finalizes a game — postponed, protested, or a data
    correction). None (default) means "don't touch completion fields" —
    used by legacy/partial-update callers.
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
        if time:
            game.time = time
        if espn_id:
            game.espn_id = espn_id
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
            # Old DailyRanking row is now orphaned — get_upcoming_rankings
            # returns rankings independently of Game, so leaving it would
            # render as a phantom upcoming matchup with no live data.
            session.query(DailyRanking).filter(
                DailyRanking.date == old_key[0],
                DailyRanking.team_a_id == old_key[1],
                DailyRanking.team_b_id == old_key[2],
            ).delete()
        if excitement_index is not None:
            game.excitement_index = excitement_index
        elif invalidate_excitement or un_finalized:
            game.excitement_index = None
            game.excitement_computed_at = None
            game.excitement_last_attempt_at = None
        session.commit()
        return game

    game = Game(
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


@dataclass(frozen=True)
class GameFields:
    """Per-game metadata joined into ranking responses. Broadcaster is
    sourced here (not from DailyRanking) because Game.broadcaster is
    refreshed by every daily run and reflects late corrections, while
    DailyRanking.broadcaster freezes at scoring time."""

    time: str
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
    """Get all completed games for a season."""
    return (
        session.query(Game)
        .filter(Game.date.like(f"{season_year}-%"))
        .filter(Game.winner_id.isnot(None))
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


def upsert_playoff_probability(
    session: Session,
    date: str,
    team_id: int,
    probability: float,
) -> PlayoffProbability:
    """Upsert a team's playoff probability for a given date."""
    record = (
        session.query(PlayoffProbability)
        .filter(PlayoffProbability.date == date, PlayoffProbability.team_id == team_id)
        .first()
    )
    if record:
        record.probability = probability
    else:
        record = PlayoffProbability(date=date, team_id=team_id, probability=probability)
        session.add(record)
    session.commit()
    return record


def get_playoff_probabilities(session: Session, date: str) -> dict[int, float]:
    """Return {team_id: probability} for all teams on the given date."""
    records = (
        session.query(PlayoffProbability).filter(PlayoffProbability.date == date).all()
    )
    return {r.team_id: r.probability for r in records}


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
