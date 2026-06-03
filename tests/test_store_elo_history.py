import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.schema import Base, Team
from src.db.queries import get_elo_history, replace_elo_history
from src.scoring.elo import EloReplay
from scripts.daily_update import store_elo_history


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([Team(id=1, name="Aces"), Team(id=2, name="Storm")])
    s.commit()
    yield s
    s.close()


def _replay():
    return EloReplay(
        final_ratings={"Aces": 1615.0, "Storm": 1440.0},
        history=[
            {
                "team_a": "Aces",
                "team_b": "Storm",
                "pre_a": 1600.0,
                "pre_b": 1450.0,
                "winner": "Aces",
                "date": "2026-05-10",
            },
        ],
    )


def test_store_writes_resolved_team_points(session):
    store_elo_history(session, _replay(), 2026)
    stored = get_elo_history(session, 2026)
    assert {(r.team_id, r.rating) for r in stored} == {(1, 1600.0), (2, 1450.0)}


def test_store_raises_on_unknown_team_and_preserves_existing(session):
    # A prior complete season is already published.
    replace_elo_history(session, 2026, [(1, "2026-05-01", 1500.0)])
    replay = EloReplay(
        final_ratings={},
        history=[
            {
                "team_a": "Aces",
                "team_b": "Nonexistent",
                "pre_a": 1600.0,
                "pre_b": 1450.0,
                "winner": "Aces",
                "date": "2026-05-10",
            },
        ],
    )
    # An unresolved team must abort the whole rewrite rather than publish a
    # partial season — so the non-fatal probe in main() rolls back and the
    # previously stored complete season survives untouched.
    with pytest.raises(ValueError, match="Unresolved teams"):
        store_elo_history(session, replay, 2026)
    stored = get_elo_history(session, 2026)
    assert [(r.team_id, r.date, r.rating) for r in stored] == [
        (1, "2026-05-01", 1500.0)
    ]
