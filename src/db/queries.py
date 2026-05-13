"""Database query helpers for WNBA Games to Watch."""

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
) -> Game:
    """Upsert a game (insert if not exists, update result if it has been played)."""
    game = (
        session.query(Game)
        .filter(
            Game.date == date, Game.team_a_id == team_a_id, Game.team_b_id == team_b_id
        )
        .first()
    )
    if game:
        if winner_id is not None:
            game.winner_id = winner_id
            game.final_score_a = final_score_a
            game.final_score_b = final_score_b
        if broadcaster:
            game.broadcaster = broadcaster
        if time:
            game.time = time
        if espn_id:
            game.espn_id = espn_id
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
    )
    session.add(game)
    session.commit()
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


def get_game_times(
    session: Session, keys: list[tuple[str, int, int]]
) -> dict[tuple[str, int, int], str]:
    """Look up game times for a set of (date, team_a_id, team_b_id) tuples."""
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
        (g.date, g.team_a_id, g.team_b_id): g.time or ""
        for g in games
        if (g.date, g.team_a_id, g.team_b_id) in wanted
    }


def get_espn_ids(
    session: Session, keys: list[tuple[str, int, int]]
) -> dict[tuple[str, int, int], str | None]:
    """Look up ESPN event IDs for (date, team_a_id, team_b_id) tuples."""
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
        (g.date, g.team_a_id, g.team_b_id): g.espn_id or None
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


def get_rankings_by_broadcaster(
    session: Session, start_date: str, broadcaster: str
) -> list[DailyRanking]:
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
