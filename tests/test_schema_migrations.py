"""Schema-migration tests.

The SQLite upgrade path must ADD columns to an existing (stale) database:
`Base.metadata.create_all` only creates missing *tables*, it never alters an
existing one. Fresh in-memory tests can't catch a missing migration entry — the
model itself creates the column — so these boot against a deliberately stale
schema and verify the ALTER runs.
"""

import json

from sqlalchemy import create_engine, inspect, text


def test_sqlite_migration_adds_seed_distribution_to_stale_playoff_probabilities(
    tmp_path, monkeypatch
):
    """A pre-existing SQLite playoff_probabilities table that predates the
    seed_distribution column must gain it on init_db(), so the daily write
    (store_playoff_probabilities -> upsert_playoff_probability) and the read
    (get_playoff_probabilities) don't hit a missing-column error.

    Guards SQLite/Postgres migration parity: seed_distribution was added to the
    Postgres block but initially missed in the SQLite one, and the repo's default
    DATABASE_URL is SQLite.
    """
    db_path = tmp_path / "stale.db"

    # Build the pre-seed_distribution schema: every column except the new one.
    raw = create_engine(f"sqlite:///{db_path}")
    with raw.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE playoff_probabilities ("
                "id INTEGER PRIMARY KEY, "
                "date VARCHAR(10) NOT NULL, "
                "team_id INTEGER NOT NULL, "
                "probability FLOAT NOT NULL, "
                "reach_semis_prob FLOAT, "
                "reach_finals_prob FLOAT, "
                "win_championship_prob FLOAT, "
                "created_at DATETIME)"
            )
        )
        conn.commit()
    raw.dispose()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from src.db import schema
    from src.db.queries import get_playoff_probabilities, upsert_playoff_probability

    schema._engine = None
    schema._session_factory = None
    try:
        schema.init_db()  # must ALTER-add seed_distribution to the stale table

        cols = {
            c["name"]
            for c in inspect(schema.get_engine()).get_columns("playoff_probabilities")
        }
        assert "seed_distribution" in cols

        # The migrated column is readable and writable end to end.
        session = schema.get_session()
        upsert_playoff_probability(
            session,
            date="2026-06-30",
            team_id=1,
            probability=0.9,
            seed_distribution=json.dumps({"1": 0.6, "2": 0.3}),
        )
        result = get_playoff_probabilities(session, "2026-06-30")
        session.close()
        assert result[1].seed_distribution == {1: 0.6, 2: 0.3}
    finally:
        schema._engine = None
        schema._session_factory = None
