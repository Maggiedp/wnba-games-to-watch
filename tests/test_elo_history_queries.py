from src.db.schema import Base


def test_elo_history_table_registered():
    assert "elo_history" in Base.metadata.tables
    cols = Base.metadata.tables["elo_history"].columns
    assert {"id", "team_id", "date", "rating", "created_at"} <= set(cols.keys())
