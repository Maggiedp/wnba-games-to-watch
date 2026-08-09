"""The browser-smoke seed must populate every surface the overflow walk visits.

Guards the contract between tests/browser/smoke_server.py (seed) and
tests/browser/overflow.test.js (walk): if a seeded surface goes empty, the
walk's readySelector would time out in CI — this catches it at pytest speed.
"""

from tests.browser.smoke_server import (
    COMPLETED_DETAIL_ID,
    UPCOMING_DETAIL_ID,
    seed,
)


def test_seed_populates_walked_surfaces(env, client):
    seed(env.get_session())

    upcoming = client.get("/api/games/upcoming").json()
    assert len(upcoming) >= 14  # featured hero + a full week of rows

    odds = client.get("/api/playoff-odds").json()
    assert len(odds) == 15
    # Seeds view is gated on EVERY team having non-null seed_distribution
    # (seedsViewAvailable) and non-null round probs — a partial seed would
    # silently hide the Rounds|Seeds toggle and vacate the walk's Seeds state.
    assert all(o["seed_distribution"] is not None for o in odds)

    completed = client.get("/api/games/completed").json()
    assert len(completed) >= 25
    assert any(g.get("shape_curve") for g in completed)  # mini fever-lines
    # Thriller styling (Fraunces 900 + starburst) needs at least one >= 7.5.
    assert any((g.get("excitement_index") or 0) >= 7.5 for g in completed)

    cal = client.get("/api/calibration").json()
    assert cal["n"] >= 25  # MIN_CAL_GAMES gate → reliability diagram renders

    replay = client.get("/api/replay").json()
    assert len(replay["games"]) >= 8

    elo = client.get("/api/elo-history").json()
    assert len(elo["teams"]) == 15

    style = client.get("/api/team-style").json()
    assert len(style["teams"]) == 15

    # League-avg anchors must be present or the /shot-making + /player
    # vs-league chart takes the graceful no-bridge path and the browser
    # walk's `.bridge-mark.is-actual` readySelector times out.
    shot_making = client.get("/api/shot-making").json()
    assert shot_making["league_avg_xpps"] is not None
    assert shot_making["league_avg_pps"] is not None

    detail = client.get(f"/game/{UPCOMING_DETAIL_ID}")
    assert detail.status_code == 200
    assert "playoff odds" in detail.text  # "What's at stake" movers block

    assert client.get(f"/game/{COMPLETED_DETAIL_ID}").status_code == 200
