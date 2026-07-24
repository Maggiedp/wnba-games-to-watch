"""SQLAlchemy table definitions for WNBA Games to Watch."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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
    # Last time the daily loop tried to compute this game's win-probability
    # shape (its game_shapes row). NULL = never attempted. Ordering retries by
    # this prevents a permanently-failing game from starving others under the cap.
    game_shape_last_attempt_at = Column(DateTime, nullable=True)
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
    importance_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_ranking_date", "date"),
        UniqueConstraint("date", "team_a_id", "team_b_id", name="uq_daily_ranking"),
    )


class EloHistory(Base):
    """Per-team Elo rating entering each game, reconstructed from the
    deterministic replay each daily run and rewritten per season. Read-only
    source for the /transparency Elo-over-time chart. No unique constraint on
    (team_id, date): the rewrite is whole-season delete-and-insert, and a team
    could in principle appear twice on one date."""

    __tablename__ = "elo_history"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD game date
    rating = Column(Float, nullable=False)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (Index("idx_elo_history_date", "date"),)


class PlayoffProbability(Base):
    __tablename__ = "playoff_probabilities"

    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    probability = Column(Float, nullable=False)
    reach_semis_prob = Column(Float, nullable=True)
    reach_finals_prob = Column(Float, nullable=True)
    win_championship_prob = Column(Float, nullable=True)
    seed_distribution = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint("date", "team_id", name="uq_playoff_prob"),
        Index("idx_playoff_prob_date", "date"),
    )


class GameShape(Base):
    """Self-contained per-game win-probability shape for the Replay Value
    archive. Keyed by espn_id (NOT FK'd to games) so it holds 2024-2026
    uniformly — 2024/2025 games are never written to the games table (the Elo
    replay fetches them from ESPN at runtime). Correlates to 2026 games/live
    rows by shared espn_id."""

    __tablename__ = "game_shapes"

    id = Column(Integer, primary_key=True)
    espn_id = Column(String(20), nullable=False, unique=True)
    season = Column(Integer, nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    home_team = Column(String(64), nullable=False)
    away_team = Column(String(64), nullable=False)
    home_abbr = Column(String(16), nullable=False)
    away_abbr = Column(String(16), nullable=False)
    home_score = Column(Integer, nullable=False)
    away_score = Column(Integer, nullable=False)
    winner = Column(String(4), nullable=False)  # 'home' | 'away'
    excitement = Column(Float, nullable=False)
    tension = Column(Float, nullable=False)
    comeback = Column(Float, nullable=False)
    lead_changes = Column(Integer, nullable=False)
    winner_low_wp = Column(Float, nullable=False)
    curve = Column(Text, nullable=False)  # JSON [[t_sec, home_pct], ...]
    computed_at = Column(DateTime, default=func.now())

    __table_args__ = (Index("idx_game_shapes_season", "season"),)


class TeamStyle(Base):
    """Per-team season style metrics (raw, league-relative normalization done at
    read time). One row per (season, team_id). Populated daily from ESPN's
    byteam statistics; powers the /style fingerprint gallery. A brand-new table,
    so Base.metadata.create_all creates it — no ALTER needed in init_db."""

    __tablename__ = "team_style"

    id = Column(Integer, primary_key=True)
    season = Column(Integer, nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    pace = Column(Float, nullable=False)
    three_pa_rate = Column(Float, nullable=False)
    ft_rate = Column(Float, nullable=False)
    oreb_pct = Column(Float, nullable=False)
    assist_rate = Column(Float, nullable=False)
    def_pressure = Column(Float, nullable=False)
    opp_3pa_rate = Column(Float, nullable=False)
    games_played = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("season", "team_id", name="uq_team_style_season_team"),
        Index("idx_team_style_season", "season"),
    )


class Shot(Base):
    """One durable row per field-goal attempt (free throws excluded), parsed
    from ESPN play-by-play. Powers the /shot-making xPPS leaderboard; raw rows
    kept for a future shot-chart. Idempotent on (espn_game_id, play_id)."""

    __tablename__ = "shots"

    id = Column(Integer, primary_key=True)
    espn_game_id = Column(String(20), nullable=False)
    play_id = Column(String(24), nullable=False)
    season = Column(Integer, nullable=False)
    athlete_id = Column(String(20), nullable=False)
    athlete_name = Column(String(64), nullable=False)
    team_id = Column(String(20), nullable=False)
    shot_type = Column(String(64), nullable=False)
    distance_ft = Column(Float, nullable=True)
    coord_x = Column(Integer, nullable=True)
    coord_y = Column(Integer, nullable=True)
    points = Column(Integer, nullable=False)
    made = Column(Boolean, nullable=False)

    __table_args__ = (
        UniqueConstraint("espn_game_id", "play_id", name="uq_shot_play"),
        Index("idx_shot_season", "season"),
    )


class ShotMaking(Base):
    """Precomputed per-player shot-making leaderboard row for a season. Recomputed
    wholesale each daily run from `shots`; the /api/shot-making endpoint reads it
    (DB-only). One row per (season, athlete_id)."""

    __tablename__ = "shot_making"

    id = Column(Integer, primary_key=True)
    season = Column(Integer, nullable=False)
    athlete_id = Column(String(20), nullable=False)
    athlete_name = Column(String(64), nullable=False)
    team_id = Column(String(20), nullable=False)
    fga = Column(Integer, nullable=False)
    made = Column(Integer, nullable=False)
    actual_pts = Column(Float, nullable=False)
    expected_pts = Column(Float, nullable=False)
    points_added = Column(Float, nullable=False)
    points_added_per_100 = Column(Float, nullable=False)
    actual_pps = Column(Float, nullable=False)
    expected_pps = Column(Float, nullable=False)
    diet = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("season", "athlete_id", name="uq_shot_making"),
        Index("idx_shot_making_season", "season"),
    )


class ThrillerAlert(Base):
    """One row per game we've already pinged about — dedup for the live
    'tune in' alerts. Keyed by espn_id (globally unique per game); date/label
    are metadata (which ET night, and Close vs Thriller). A brand-new table, so
    Base.metadata.create_all creates it — no ALTER needed in init_db."""

    __tablename__ = "thriller_alerts"

    id = Column(Integer, primary_key=True)
    espn_id = Column(String(20), nullable=False, unique=True)
    date = Column(String(10), nullable=False)  # game's ET date, metadata
    label = Column(String(20), nullable=False)  # "Close game" | "Thriller"
    alerted_at = Column(DateTime, default=func.now())  # wall-clock UTC, exempt


def get_database_url() -> str:
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/games_to_watch.db")
    if db_url.startswith("sqlite:///./"):
        base_path = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        relative_part = db_url.replace("sqlite:///./", "")
        db_url = f"sqlite:///{os.path.join(base_path, relative_part)}"
    return db_url


def backfill_time_utc_from_legacy(session) -> int:
    """Derive `time_utc` for existing rows where it's NULL but `time` is known.

    The daily ingest only fetches yesterday-forward, so games completed
    before deploy keep NULL `time_utc` forever — leaving the completed-
    games archive mixing ET-only display (legacy rows via fallback) and
    localized display (new rows). This one-shot helper closes that gap
    by converting the stored ET string + ET-keyed date back to UTC via
    `zoneinfo` (DST-aware). Idempotent: skips rows already populated or
    with empty `time` (genuine TBD).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.data.espn_api import ET

    # Probe first so a steady-state boot doesn't pay full-scan ORM
    # hydration on every cold start. After backfill completes, every
    # subsequent call returns 0 rows via this single LIMIT 1 query.
    has_legacy = (
        session.query(Game.id)
        .filter(Game.time_utc.is_(None))
        .filter(Game.time != "")
        .limit(1)
        .first()
    )
    if has_legacy is None:
        return 0

    utc = ZoneInfo("UTC")
    rows = (
        session.query(Game)
        .filter(Game.time_utc.is_(None))
        .filter(Game.time != "")
        .all()
    )
    updated = 0
    for game in rows:
        time_str = (game.time or "").replace(" ET", "").strip()
        try:
            t = datetime.strptime(time_str, "%I:%M %p")
            d = datetime.strptime(game.date, "%Y-%m-%d")
        except ValueError:
            continue
        dt_et = d.replace(hour=t.hour, minute=t.minute, tzinfo=ET)
        game.time_utc = dt_et.astimezone(utc).isoformat()
        updated += 1
    if updated:
        session.commit()
    return updated


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
        url = get_database_url()
        # Ensure the parent dir of a sqlite file DB exists — a clean checkout has
        # no data/ dir (it's gitignored), so create_engine would fail to open it.
        if url.startswith("sqlite:///") and ":memory:" not in url:
            db_dir = os.path.dirname(url.replace("sqlite:///", "", 1))
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
        _engine = create_engine(url, echo=False)
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
                "ALTER TABLE games ADD COLUMN game_shape_last_attempt_at DATETIME",
                "ALTER TABLE games ADD COLUMN season_type INTEGER",
                "ALTER TABLE games ADD COLUMN time_utc VARCHAR(40)",
                "ALTER TABLE playoff_probabilities ADD COLUMN reach_semis_prob FLOAT",
                "ALTER TABLE playoff_probabilities ADD COLUMN reach_finals_prob FLOAT",
                "ALTER TABLE playoff_probabilities ADD COLUMN win_championship_prob FLOAT",
                "ALTER TABLE playoff_probabilities ADD COLUMN seed_distribution TEXT",
                "ALTER TABLE daily_rankings ADD COLUMN importance_detail TEXT",
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
                text(
                    "ALTER TABLE games ADD COLUMN IF NOT EXISTS "
                    "game_shape_last_attempt_at TIMESTAMP"
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
            conn.execute(
                text(
                    "ALTER TABLE playoff_probabilities ADD COLUMN IF NOT EXISTS "
                    "seed_distribution TEXT"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE daily_rankings ADD COLUMN IF NOT EXISTS "
                    "importance_detail TEXT"
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

    # One-shot backfill for the time_utc column on pre-deploy rows. The
    # daily ingest only sees yesterday-forward; without this, the
    # completed-games archive would mix ET-only and localized display.
    Session = sessionmaker(bind=engine)
    backfill_session = Session()
    try:
        backfill_time_utc_from_legacy(backfill_session)
    finally:
        backfill_session.close()
    return engine


def get_session():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=get_engine()
        )
    return _session_factory()
