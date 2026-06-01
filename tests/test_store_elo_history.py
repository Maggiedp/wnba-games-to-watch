import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.schema import Base, Team
from src.db.queries import get_elo_history
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
    store_elo_history(session, _replay(), "2026")
    stored = get_elo_history(session, "2026")
    assert {(r.team_id, r.rating) for r in stored} == {(1, 1600.0), (2, 1450.0)}


def test_store_skips_unknown_team(session, caplog):
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
    store_elo_history(session, replay, "2026")
    stored = get_elo_history(session, "2026")
    # Aces stored, unknown team skipped.
    assert {r.team_id for r in stored} == {1}
