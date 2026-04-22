"""SQLAlchemy table definitions for WNBA Games to Watch."""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

Base = declarative_base()
_engine = None


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    abbreviation = Column(String(8), default="")
    logo_url = Column(String(500), default="")
    bpi_rating = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=func.now(), onupdate=func.now())


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    team_a_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team_b_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    time = Column(String(5), default="")  # HH:MM
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    final_score_a = Column(Integer, nullable=True)
    final_score_b = Column(Integer, nullable=True)
    broadcaster = Column(String(50), default="")
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (Index("idx_game_date", "date"),)


class DailyRanking(Base):
    __tablename__ = "daily_rankings"

    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    team_a_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team_b_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    quality_score = Column(Float, default=0.0)
    importance_score = Column(Float, nullable=True)
    overall_score = Column(Float, default=0.0)
    broadcaster = Column(String(50), default="")
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_ranking_date", "date"),
        UniqueConstraint("date", "team_a_id", "team_b_id", name="uq_daily_ranking"),
    )


def get_database_url() -> str:
    """Get the database URL from environment or use default."""
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/games_to_watch.db")
    # For SQLite, convert to absolute path if relative
    if db_url.startswith("sqlite:///./"):
        base_path = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        relative_part = db_url.replace("sqlite:///./", "")
        db_url = f"sqlite:///{os.path.join(base_path, relative_part)}"
    return db_url


def get_engine():
    """Get or create the database engine (singleton pattern)."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), echo=False)
    return _engine


def init_db():
    """Initialize the database and create tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_team_columns(engine)
    return engine


def _add_missing_team_columns(engine) -> None:
    """Add columns added after initial schema. Idempotent."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "teams" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("teams")}
    with engine.begin() as conn:
        if "abbreviation" not in existing:
            conn.execute(
                text("ALTER TABLE teams ADD COLUMN abbreviation VARCHAR(8) DEFAULT ''")
            )
        if "logo_url" not in existing:
            conn.execute(
                text("ALTER TABLE teams ADD COLUMN logo_url VARCHAR(500) DEFAULT ''")
            )


def get_session():
    """Create a new database session."""
    engine = get_engine()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()
