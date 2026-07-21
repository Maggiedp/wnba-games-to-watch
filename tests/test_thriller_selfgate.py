from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.notify.thriller import filter_recent_tipoffs


def _g(time_utc):
    return SimpleNamespace(time_utc=time_utc)


def test_keeps_only_recent_tipoffs():
    now = datetime(2026, 7, 20, 23, 30, tzinfo=timezone.utc)
    tipped_1h_ago = _g((now - timedelta(hours=1)).isoformat())
    tipped_6h_ago = _g((now - timedelta(hours=6)).isoformat())  # surely over
    tips_in_2h = _g((now + timedelta(hours=2)).isoformat())  # not started
    no_time = _g(None)
    bad = _g("not-a-date")

    kept = filter_recent_tipoffs(
        [tipped_1h_ago, tipped_6h_ago, tips_in_2h, no_time, bad], now
    )
    assert kept == [tipped_1h_ago]


def test_empty_when_nothing_recent():
    now = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)
    assert (
        filter_recent_tipoffs([_g((now + timedelta(hours=1)).isoformat())], now) == []
    )
