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
    text,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
import os

Base = declarative_base()
_engine = None
_session_factory = None


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    abbreviation = Column(String(16), default="")
    logo_url = Column(String(500), default="")
    bpi_rating = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=func.now(), onupdate=func.now())


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    team_a_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team_b_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    time = Column(String(20), default="")
    time_utc = Column(String(40), nullable=True)
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    final_score_a = Column(Integer, nullable=True)
    final_score_b = Column(Integer, nullable=True)
    excitement_index = Column(Float, nullable=True)
    # Last time the backfill loop tried to compute excitement for this game.
    # NULL = never attempted. Ordering retries by this timestamp prevents
    # a permanently-failing head from starving older NULL rows under the cap.
    excitement_last_attempt_at = Column(DateTime, nullable=True)
    # When the currently-stored excitement_index was actually computed (only
    # set on a successful STATUS_FINAL persist). Drives a short freshness
    # window in which the daily job re-fetches and overwrites, in case ESPN
    # refined the PBP after our first final read. NULL once locked beyond
    # the window, or if no score has been stored.
    excitement_computed_at = Column(DateTime, nullable=True)
    broadcaster = Column(String(50), default="")
    espn_id = Column(String(20), nullable=True)
    # ESPN season type: 1=preseason, 2=regular, 3=postseason. NULL on
    # legacy rows ingested before this column existed; the completed
    # archive treats NULL as "not preseason" for backward compatibility.
    season_type = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_game_date", "date"),
        # NULLs are distinct in SQLite + Postgres UNIQUE semantics, so this
        # acts as a partial-unique-on-non-null without dialect-specific args.
        # Guards against concurrent inserts creating duplicate rows for the
        # same ESPN event during overlapping daily-update runs.
        Index("uq_game_espn_id", "espn_id", unique=True),
    )


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
    win_prob_a = Column(Float, nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_ranking_date", "date"),
        UniqueConstraint("date", "team_a_id", "team_b_id", name="uq_daily_ranking"),
    )


class PlayoffProbability(Base):
    __tablename__ = "playoff_probabilities"

    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    probability = Column(Float, nullable=False)
    reach_semis_prob = Column(Float, nullable=True)
    reach_finals_prob = Column(Float, nullable=True)
    win_championship_prob = Column(Float, nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint("date", "team_id", name="uq_playoff_prob"),
        Index("idx_playoff_prob_date", "date"),
    )


class SeasonConfig(Base):
    """Per-season calibration values computed once at season open."""

    __tablename__ = "season_config"

    season_year = Column(Integer, primary_key=True)
    importance_max_swing = Column(Float, nullable=False)
    created_at = Column(DateTime, default=func.now())


def get_database_url() -> str:
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/games_to_watch.db")
    if db_url.startswith("sqlite:///./"):
        base_path = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        relative_part = db_url.replace("sqlite:///./", "")
        db_url = f"sqlite:///{os.path.join(base_path, relative_part)}"
    return db_url


def _dedupe_games_by_espn_id(conn) -> int:
    """Merge duplicate non-null espn_id rows into a single survivor.

    Realistic scenario: a pre-this-branch reschedule where ESPN moved
    an event. The old upsert_game matched by (date, team_a, team_b), so
    it inserted a fresh row at the new date — same espn_id — while the
    old row sat at the original date.

    The NEWER row (max id) is authoritative for *schedule identity*
    (date, teams, season_type) — it represents the corrected event.
    Completion fields (winner_id, scores, excitement_*) are only kept
    from the survivor itself; we don't merge stale completion in from
    older rows, because if the most recent upsert is non-final that
    IS the truth (the game un-finalized on reschedule). If the survivor
    IS final and missing nullable completion fields, we backfill from
    older rows so we don't lose excitement_index that was computed
    before the reschedule.

    DailyRanking rows keyed by a doomed row's (date, team_a, team_b)
    are re-keyed to the survivor's key when no ranking already exists
    there, so the pre-game quality / importance / overall scores
    follow the game. They're deleted only when the survivor key
    already has a ranking (the more-current row takes precedence).
    """
    dup_ids = conn.execute(
        text(
            "SELECT espn_id FROM games WHERE espn_id IS NOT NULL "
            "GROUP BY espn_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if not dup_ids:
        return 0
    deleted = 0
    nullable_completion_fields = (
        "excitement_index",
        "excitement_computed_at",
        "excitement_last_attempt_at",
    )
    for (espn_id,) in dup_ids:
        rows = conn.execute(
            text(
                "SELECT id, team_a_id, team_b_id, date, time, broadcaster, "
                "winner_id, final_score_a, final_score_b, excitement_index, "
                "excitement_computed_at, excitement_last_attempt_at "
                "FROM games WHERE espn_id = :espn_id ORDER BY id DESC"
            ),
            {"espn_id": espn_id},
        ).fetchall()
        survivor = rows[0]  # authoritative schedule identity
        survivor_is_final = survivor.winner_id is not None
        # Only merge completion-adjacent fields from older rows if the
        # survivor still represents a final game; otherwise the schedule
        # has un-finalized and stale completion must be discarded.
        if survivor_is_final:
            merged = {f: getattr(survivor, f) for f in nullable_completion_fields}
            for r in rows[1:]:
                for f in nullable_completion_fields:
                    if merged[f] is None and getattr(r, f) is not None:
                        merged[f] = getattr(r, f)
            if any(
                merged[f] != getattr(survivor, f) for f in nullable_completion_fields
            ):
                conn.execute(
                    text(
                        "UPDATE games SET "
                        "excitement_index = :excitement_index, "
                        "excitement_computed_at = :excitement_computed_at, "
                        "excitement_last_attempt_at = :excitement_last_attempt_at "
                        "WHERE id = :id"
                    ),
                    {**merged, "id": survivor.id},
                )

        # Re-key (or delete) doomed rows' DailyRankings, then drop the row.
        survivor_key = (survivor.date, survivor.team_a_id, survivor.team_b_id)
        for r in rows[1:]:
            r_key = (r.date, r.team_a_id, r.team_b_id)
            if r_key != survivor_key:
                target_exists = conn.execute(
                    text(
                        "SELECT 1 FROM daily_rankings WHERE date = :d "
                        "AND team_a_id = :a AND team_b_id = :b"
                    ),
                    {"d": survivor_key[0], "a": survivor_key[1], "b": survivor_key[2]},
                ).fetchone()
                if target_exists:
                    conn.execute(
                        text(
                            "DELETE FROM daily_rankings WHERE date = :d "
                            "AND team_a_id = :a AND team_b_id = :b"
                        ),
                        {"d": r_key[0], "a": r_key[1], "b": r_key[2]},
                    )
                else:
                    conn.execute(
                        text(
                            "UPDATE daily_rankings SET date = :nd, "
                            "team_a_id = :na, team_b_id = :nb "
                            "WHERE date = :od AND team_a_id = :oa "
                            "AND team_b_id = :ob"
                        ),
                        {
                            "nd": survivor_key[0],
                            "na": survivor_key[1],
                            "nb": survivor_key[2],
                            "od": r_key[0],
                            "oa": r_key[1],
                            "ob": r_key[2],
                        },
                    )
            conn.execute(
                text("DELETE FROM games WHERE id = :id"),
                {"id": r.id},
            )
            deleted += 1
    return deleted


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), echo=False)
    return _engine


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    # SQLite: add columns that may be missing from stale DBs (no IF NOT EXISTS support).
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            for stmt in [
                "ALTER TABLE games ADD COLUMN espn_id VARCHAR(20)",
                "ALTER TABLE daily_rankings ADD COLUMN win_prob_a FLOAT",
                "ALTER TABLE games ADD COLUMN excitement_index FLOAT",
                "ALTER TABLE games ADD COLUMN excitement_last_attempt_at DATETIME",
                "ALTER TABLE games ADD COLUMN excitement_computed_at DATETIME",
                "ALTER TABLE games ADD COLUMN season_type INTEGER",
                "ALTER TABLE games ADD COLUMN time_utc VARCHAR(40)",
                "ALTER TABLE playoff_probabilities ADD COLUMN reach_semis_prob FLOAT",
                "ALTER TABLE playoff_probabilities ADD COLUMN reach_finals_prob FLOAT",
                "ALTER TABLE playoff_probabilities ADD COLUMN win_championship_prob FLOAT",
            ]:
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                except Exception:
                    pass  # column already exists
            # Dedupe pre-existing duplicate espn_id rows before creating
            # the unique index. See _dedupe_games_by_espn_id for the
            # survivor-selection / merge logic — a blind MAX(id) would
            # silently drop completed rows that happened to be older.
            _dedupe_games_by_espn_id(conn)
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_game_espn_id "
                    "ON games (espn_id)"
                )
            )
            conn.commit()

    # PostgreSQL: supports ALTER COLUMN TYPE, DO blocks, and IF NOT EXISTS.
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            # Widen abbreviation column if it's still VARCHAR(8) from old schema.
            # ESPN started sending longer abbreviations (e.g. "CONNECTICU") in 2026.
            conn.execute(
                text("ALTER TABLE teams ALTER COLUMN abbreviation TYPE VARCHAR(16)")
            )
            # Merge stale pre-normalization team name aliases into their canonical forms.
            # Before _canonical_name() was applied consistently, ESPN's all-caps variant
            # "Connecticut SUN" could end up stored as a separate row alongside the
            # correct "Connecticut Sun", causing assert_all_teams_have_conferences to fail.
            conn.execute(
                text("""
                DO $$
                DECLARE
                    old_id INTEGER;
                    new_id INTEGER;
                    alias_pairs TEXT[][] := ARRAY[ARRAY['Connecticut SUN', 'Connecticut Sun']];
                    pair TEXT[];
                BEGIN
                    FOREACH pair SLICE 1 IN ARRAY alias_pairs LOOP
                        SELECT id INTO old_id FROM teams WHERE name = pair[1];
                        SELECT id INTO new_id FROM teams WHERE name = pair[2];
                        IF old_id IS NOT NULL AND new_id IS NOT NULL THEN
                            UPDATE games SET team_a_id = new_id WHERE team_a_id = old_id;
                            UPDATE games SET team_b_id = new_id WHERE team_b_id = old_id;
                            UPDATE games SET winner_id = new_id WHERE winner_id = old_id;
                            UPDATE daily_rankings SET team_a_id = new_id WHERE team_a_id = old_id;
                            UPDATE daily_rankings SET team_b_id = new_id WHERE team_b_id = old_id;
                            UPDATE playoff_probabilities SET team_id = new_id WHERE team_id = old_id;
                            DELETE FROM teams WHERE id = old_id;
                        ELSIF old_id IS NOT NULL THEN
                            UPDATE teams SET name = pair[2] WHERE id = old_id;
                        END IF;
                    END LOOP;
                END $$;
                """)
            )
            conn.execute(
                text(
                    "ALTER TABLE daily_rankings ADD COLUMN IF NOT EXISTS win_prob_a FLOAT"
                )
            )
            conn.execute(
                text("ALTER TABLE games ADD COLUMN IF NOT EXISTS espn_id VARCHAR(20)")
            )
            conn.execute(
                text(
                    "ALTER TABLE games ADD COLUMN IF NOT EXISTS excitement_index FLOAT"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE games ADD COLUMN IF NOT EXISTS "
                    "excitement_last_attempt_at TIMESTAMP"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE games ADD COLUMN IF NOT EXISTS "
                    "excitement_computed_at TIMESTAMP"
                )
            )
            conn.execute(
                text("ALTER TABLE games ADD COLUMN IF NOT EXISTS season_type INTEGER")
            )
            conn.execute(
                text("ALTER TABLE games ADD COLUMN IF NOT EXISTS time_utc VARCHAR(40)")
            )
            conn.execute(
                text(
                    "ALTER TABLE playoff_probabilities ADD COLUMN IF NOT EXISTS "
                    "reach_semis_prob FLOAT"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE playoff_probabilities ADD COLUMN IF NOT EXISTS "
                    "reach_finals_prob FLOAT"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE playoff_probabilities ADD COLUMN IF NOT EXISTS "
                    "win_championship_prob FLOAT"
                )
            )
            _dedupe_games_by_espn_id(conn)
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_game_espn_id "
                    "ON games (espn_id)"
                )
            )
            conn.commit()
    return engine


def get_session():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=get_engine()
        )
    return _session_factory()
