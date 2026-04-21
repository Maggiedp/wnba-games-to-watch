"""SQLAlchemy table definitions for WNBA Games to Watch."""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    create_engine,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

Base = declarative_base()


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
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


class DailyRanking(Base):
    __tablename__ = "daily_rankings"

    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    team_a_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team_b_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    quality_score = Column(Float, default=0.0)
    importance_score = Column(Float, default=0.0)
    overall_score = Column(Float, default=0.0)
    broadcaster = Column(String(50), default="")
    created_at = Column(DateTime, default=func.now())


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


def init_db():
    """Initialize the database and create tables if they don't exist."""
    db_url = get_database_url()
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session():
    """Create a new database session."""
    db_url = get_database_url()
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()
