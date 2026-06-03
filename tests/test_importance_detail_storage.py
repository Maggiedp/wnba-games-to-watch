import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.queries import upsert_daily_ranking, upsert_team
from src.db.schema import Base, DailyRanking


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_importance_detail_round_trips(session):
    a = upsert_team(
        session, name="Seattle Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0
    )
    b = upsert_team(
        session, name="Chicago Sky", abbreviation="CHI", logo_url="", bpi_rating=0.0
    )

    upsert_daily_ranking(
        session,
        date="2026-06-10",
        team_a_id=a.id,
        team_b_id=b.id,
        quality_score=50.0,
        importance_score=40.0,
        overall_score=46.0,
        broadcaster="ESPN",
        win_prob_a=0.6,
        importance_detail='{"metric":"playoffs","movers":[]}',
    )
    row = session.query(DailyRanking).filter(DailyRanking.date == "2026-06-10").first()
    assert row.importance_detail == '{"metric":"playoffs","movers":[]}'

    # Update path also writes the column (omitting the kwarg -> None).
    upsert_daily_ranking(
        session,
        date="2026-06-10",
        team_a_id=a.id,
        team_b_id=b.id,
        quality_score=50.0,
        importance_score=40.0,
        overall_score=46.0,
        broadcaster="ESPN",
        win_prob_a=0.6,
    )
    row = session.query(DailyRanking).filter(DailyRanking.date == "2026-06-10").first()
    assert row.importance_detail is None
