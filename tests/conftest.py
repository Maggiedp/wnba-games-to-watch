"""Shared test fixtures."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    """File-backed sqlite shared across the seed session and the request
    session. Yields the schema module so tests can call env.get_session() /
    env.Team."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    yield schema
    schema._engine = None
    schema._session_factory = None


@pytest.fixture
def client(env):
    """TestClient over the app, backed by the env sqlite database."""
    from src.api.app import app

    return TestClient(app)


def make_wp_plays(anchors: list[float], n: int = 41) -> list[dict]:
    """n WP plays evenly spanning t=0..2400s (periods 1-4), home_pct linearly
    interpolated through `anchors` (first at tipoff, last at the final whistle).
    Default n=41 puts a sample exactly at each quarter fraction of the anchor
    path, so anchor values are hit exactly (e.g. a 0.20 midpoint anchor)."""
    plays = []
    for i in range(n):
        frac = i / (n - 1)
        pos = frac * (len(anchors) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(anchors) - 1)
        pct = anchors[lo] + (anchors[hi] - anchors[lo]) * (pos - lo)
        t = frac * 2400
        period = min(int(t // 600) + 1, 4)
        remaining = 600 - (t - (period - 1) * 600)
        minutes, seconds = divmod(int(round(remaining)), 60)
        plays.append(
            {
                "period": period,
                "clock": f"{minutes}:{seconds:02d}",
                "home_pct": round(pct, 4),
            }
        )
    return plays


@pytest.fixture
def wp_plays():
    """Factory fixture for realistic full-game WP feeds (see make_wp_plays)."""
    return make_wp_plays


def frozen_datetime_class(fake_utc: datetime):
    """Return a datetime stand-in whose .now(tz) returns the frozen instant."""

    class _Frozen:
        @classmethod
        def now(cls, tz=None):
            return fake_utc.astimezone(tz) if tz else fake_utc.replace(tzinfo=None)

    return _Frozen


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("UTC"))
