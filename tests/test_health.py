"""GET /api/health — snapshot-freshness signal for the uptime check.

The 2026-09-17 incident: ESPN dropped the `dates=YYYYMMDD-YYYYMMDD` scoreboard
form, `compute_daily_scores` correctly refused to overwrite good rows with an
empty fetch, the job returned 0, and two days passed with nobody told. Neither
existing monitoring layer could see it — the log alert keys on a crash that
never happened, and the uptime check passed on stale rows that still contained
`overall_score`. The signal that would have caught it on day one is snapshot
AGE, which is what this endpoint publishes.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db.queries import upsert_game, upsert_playoff_probability, upsert_team

ET = ZoneInfo("America/New_York")


def _at(hour: int, day_offset: int = 0) -> datetime:
    """A fixed ET instant: 2026-09-17 at `hour`, shifted by `day_offset` days."""
    return datetime(2026, 9, 17, hour, 0, tzinfo=ET) + timedelta(days=day_offset)


def _freeze(monkeypatch, now: datetime) -> None:
    """Pin the endpoint's clock.

    Patches the app-local name: `now_et` is imported by value at module import,
    and this moves a DATE, not a season, so `clock_season()`'s
    patch-the-one-name-in-espn_api rule does not apply here.
    """
    import src.api.app as app_module

    monkeypatch.setattr(app_module, "now_et", lambda: now)


def _seed_teams(session, env) -> tuple[int, int]:
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    return (
        session.query(env.Team).filter_by(name="Aces").one().id,
        session.query(env.Team).filter_by(name="Liberty").one().id,
    )


def _seed_game(session, a_id: int, b_id: int, date: str) -> None:
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="",
    )


def _seed_snapshot(session, team_id: int, date: str) -> None:
    upsert_playoff_probability(
        session,
        date=date,
        team_id=team_id,
        probability=0.85,
        reach_semis_prob=0.60,
        reach_finals_prob=0.40,
        win_championship_prob=0.25,
    )


def test_todays_snapshot_after_the_run_window_is_ok(env, client, monkeypatch):
    """The healthy case: the 6 AM job wrote today, and it is past 08:00 ET."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-17")
    _seed_snapshot(session, a_id, "2026-09-17")
    session.close()
    _freeze(monkeypatch, _at(9))

    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["armed"] is True
    assert body["latest_snapshot"] == "2026-09-17"
    assert body["expected"] == "2026-09-17"


def test_missing_todays_snapshot_after_the_run_window_is_stale(
    env, client, monkeypatch
):
    """The incident, day one: the job ran at 6 AM and wrote nothing."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-17")
    _seed_snapshot(session, a_id, "2026-09-16")
    session.close()
    _freeze(monkeypatch, _at(9))

    body = client.get("/api/health").json()

    assert body["status"] == "stale"
    assert body["latest_snapshot"] == "2026-09-16"
    assert body["expected"] == "2026-09-17"


def test_yesterdays_snapshot_before_the_run_window_is_ok(env, client, monkeypatch):
    """Pre-06:00 ET, yesterday's row IS the freshest possible answer."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-17")
    _seed_snapshot(session, a_id, "2026-09-16")
    session.close()
    _freeze(monkeypatch, _at(5))

    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["expected"] == "2026-09-16"


def test_two_day_old_snapshot_before_the_run_window_is_stale(env, client, monkeypatch):
    """The incident, day two: even the pre-run tolerance is exhausted."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-17")
    _seed_snapshot(session, a_id, "2026-09-15")
    session.close()
    _freeze(monkeypatch, _at(5))

    body = client.get("/api/health").json()

    assert body["status"] == "stale"
    assert body["latest_snapshot"] == "2026-09-15"


def test_no_snapshot_at_all_while_armed_is_stale(env, client, monkeypatch):
    """An empty table must not read as healthy — absence is the worst case."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-17")
    session.close()
    _freeze(monkeypatch, _at(9))

    body = client.get("/api/health").json()

    assert body["status"] == "stale"
    assert body["latest_snapshot"] is None


def test_offseason_disarms_the_check(env, client, monkeypatch):
    """No game near today: the daily job writes nothing BY DESIGN once the
    fetch window inverts past _SEASON_END, so a months-old snapshot is the
    correct steady state, not an outage. Without this the alert would fire
    every day from November until the next schedule is published."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-17")
    _seed_snapshot(session, a_id, "2026-09-17")
    session.close()
    # Four months later — the stored schedule is long past.
    _freeze(monkeypatch, _at(9, day_offset=120))

    body = client.get("/api/health").json()

    assert body["armed"] is False
    assert body["status"] == "ok"
    assert body["latest_snapshot"] == "2026-09-17"


def test_a_mid_season_break_stays_armed(env, client, monkeypatch):
    """A BREAK is not the offseason. 2026 really ran 08-30 -> 09-17 with no
    games, and the daily job wrote a snapshot on every one of those 17 days --
    future games were still inside its fetch window, so `games` was non-empty
    and compute_daily_scores ran. Arming on proximity to a game went blind for
    11 straight days mid-season, which is the exact silence this endpoint
    exists to break.
    """
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-08-30")  # last before the break
    _seed_game(session, a_id, b_id, "2026-09-17")  # first after it
    _seed_snapshot(session, a_id, "2026-09-06")
    session.close()
    _freeze(monkeypatch, _at(9, day_offset=-9))  # 2026-09-08, mid-break

    body = client.get("/api/health").json()

    assert body["armed"] is True
    assert body["status"] == "stale"


def test_no_games_left_in_the_season_disarms_the_check(env, client, monkeypatch):
    """Post-Finals but still inside the _SEASON_END calendar window. No game
    remains in the fetch range, so the job writes nothing and there is nothing
    to be stale about -- the other side of the break case above."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-10-20")
    _seed_snapshot(session, a_id, "2026-10-20")
    session.close()
    _freeze(monkeypatch, _at(9, day_offset=38))  # 2026-10-25

    body = client.get("/api/health").json()

    assert body["armed"] is False
    assert body["status"] == "ok"


def test_an_upcoming_game_arms_the_check_before_it_is_played(env, client, monkeypatch):
    """Arming reads the SCHEDULE, not the snapshot — a scheduled game two days
    out keeps the check live even though nothing has been played yet. This is
    what kept it armed during the incident: the stale rows still knew games
    were coming."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-19")
    _seed_snapshot(session, a_id, "2026-09-15")
    session.close()
    _freeze(monkeypatch, _at(9))

    body = client.get("/api/health").json()

    assert body["armed"] is True
    assert body["status"] == "stale"


def test_health_always_returns_200(env, client, monkeypatch):
    """The uptime check matches on CONTENT, not status: a stale snapshot is a
    data problem, not a server error, and a 5xx here would pollute Cloud Run's
    own error metrics."""
    session = env.get_session()
    a_id, b_id = _seed_teams(session, env)
    _seed_game(session, a_id, b_id, "2026-09-17")
    session.close()
    _freeze(monkeypatch, _at(9))

    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "stale"
