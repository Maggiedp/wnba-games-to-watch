"""Shared test fixtures."""

import threading
from collections.abc import Callable
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


def run_threads_concurrently(n: int, fn: Callable[[], object]) -> None:
    """Run `fn` on `n` threads, started together and all joined before returning.

    Results are side-effect-only: callers close over their own list, since what
    they capture differs (return values, caught exceptions, a shared exception's
    identity). A worker that raises fails the test through pytest.ini's filter
    (pinned by tests/test_pytest_ini.py); tests/test_conftest_helpers.py pins
    the overlap.
    """
    # Raw threads, not ThreadPoolExecutor: the pool reuses IDLE workers, so N
    # submits of a fast callable ran on 2-3 threads at peak concurrency 1
    # (measured). A serial run makes every single-flight test pass vacuously.
    threads = [threading.Thread(target=fn) for _ in range(n)]
    for t in threads:
        t.start()
    # No join timeout: a deadlocked build should hang visibly, not let the
    # assertions run against half-finished state.
    for t in threads:
        t.join()


@pytest.fixture
def run_concurrently():
    """Factory fixture for the thread fan-out (see run_threads_concurrently)."""
    return run_threads_concurrently


@pytest.fixture
def wp_plays():
    """Factory fixture for realistic full-game WP feeds (see make_wp_plays)."""
    return make_wp_plays


@pytest.fixture
def degenerate_wp_plays():
    """Replica of ESPN's broken feed for 2025-05-02 DAL@LV (401761558): three
    valid samples clustered in ~2 seconds, all 0.0 — passes per-sample
    sanitization but must fail the shape coverage gate."""
    return [
        {"period": 2, "clock": "0:02", "home_pct": 0.0},
        {"period": 2, "clock": "0:00", "home_pct": 0.0},
        {"period": 2, "clock": "0:00", "home_pct": 0.0},
    ]


def frozen_datetime_class(fake_utc: datetime):
    """Return a datetime stand-in whose .now(tz) returns the frozen instant."""

    class _Frozen:
        @classmethod
        def now(cls, tz=None):
            return fake_utc.astimezone(tz) if tz else fake_utc.replace(tzinfo=None)

    return _Frozen


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("UTC"))


def seed_shots_for_recompute(session, *, n: int = 120, athlete_id: str = "10"):
    """Seed `n` rim attempts for one athlete so `recompute_shot_making` has a
    qualifying board to build. No Game/Team rows needed — recompute only reads
    the `shots` table. Shared by the daily-job tests and the endpoint tests."""
    from src.db import queries as q

    for i in range(n):
        q.upsert_shots(
            session,
            "G1",
            2026,
            [
                {
                    "play_id": str(i),
                    "athlete_id": athlete_id,
                    "athlete_name": "X",
                    "team_id": "1",
                    "team_abbr": "LV",
                    "shot_type": "Layup Shot",
                    "distance_ft": 2.0,
                    "coord_x": None,
                    "coord_y": None,
                    "points": 2 if i < 80 else 0,
                    "point_value": 2,
                    "made": i < 80,
                }
            ],
            commit=False,
        )
    session.commit()


def seed_two_teams(session, *, full_names: bool = False) -> tuple[int, int]:
    """Seed the Aces (LV) and the Liberty (NY) at BPI 0.0; return their ids,
    Aces first. `full_names` uses ESPN's display names ("Las Vegas Aces" /
    "New York Liberty") for tests that resolve teams the way ingest does."""
    from src.db.queries import upsert_team

    a, b = ("Las Vegas Aces", "New York Liberty") if full_names else ("Aces", "Liberty")
    return (
        upsert_team(session, name=a, abbreviation="LV", bpi_rating=0.0).id,
        upsert_team(session, name=b, abbreviation="NY", bpi_rating=0.0).id,
    )
