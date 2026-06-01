import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.schema import Base, Team
from src.db.queries import replace_elo_history, get_elo_history


def test_elo_history_table_registered():
    assert "elo_history" in Base.metadata.tables
    cols = Base.metadata.tables["elo_history"].columns
    assert {"id", "team_id", "date", "rating", "created_at"} <= set(cols.keys())


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([Team(id=1, name="Aces"), Team(id=2, name="Storm")])
    s.commit()
    yield s
    s.close()


def test_replace_is_idempotent(session):
    rows = [(1, "2026-05-10", 1600.0), (2, "2026-05-10", 1450.0)]
    replace_elo_history(session, "2026", rows)
    replace_elo_history(session, "2026", rows)  # run twice
    stored = get_elo_history(session, "2026")
    assert len(stored) == 2  # not 4 — delete-and-rewrite


def test_replace_scopes_delete_to_season(session):
    replace_elo_history(session, "2025", [(1, "2025-09-01", 1500.0)])
    replace_elo_history(session, "2026", [(1, "2026-05-10", 1600.0)])
    # Rewriting 2026 must not touch 2025 rows.
    assert len(get_elo_history(session, "2025")) == 1
    assert len(get_elo_history(session, "2026")) == 1


def test_get_returns_date_ascending(session):
    replace_elo_history(
        session,
        "2026",
        [(1, "2026-05-15", 1615.0), (1, "2026-05-10", 1600.0)],
    )
    dates = [r.date for r in get_elo_history(session, "2026")]
    assert dates == ["2026-05-10", "2026-05-15"]
