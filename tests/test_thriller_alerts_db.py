from src.db.queries import has_alerted, record_alert


def test_record_and_check_roundtrip(env):
    session = env.get_session()
    try:
        assert has_alerted(session, "401700001") is False
        record_alert(session, "401700001", "2026-07-20", "Thriller")
        assert has_alerted(session, "401700001") is True
        # A different game is independent.
        assert has_alerted(session, "401700002") is False
    finally:
        session.close()


def test_dedup_is_by_espn_id_regardless_of_date(env):
    """espn_id is the true key — a game re-checked after the ET-midnight
    rollover (a different `date`) must still read as already-alerted."""
    session = env.get_session()
    try:
        record_alert(session, "401700003", "2026-07-19", "Close game")
        assert has_alerted(session, "401700003") is True
    finally:
        session.close()
