"""The current season is one constant, and the live paths derive from it.

Context: ~17 sites re-typed the literal 2026 instead of deriving from the
anchor. See src/constants.py for why the anchor is pinned rather than
clock-derived, and for the idioms that deliberately do NOT use it.
"""

import ast
import re
from pathlib import Path

import pytest

from src.constants import CURRENT_SEASON

_REPO = Path(__file__).resolve().parent.parent

# Live read/write paths only. The one-shot backfills are pinned to a
# historical season ON PURPOSE (they rewrite specific past rows), and
# compute_importance_ceiling is pinned to the PRIOR season by design —
# parameterizing either would silently retarget them.
_LIVE_PATH_FILES = [
    "src/db/queries.py",
    "src/api/app.py",
    "scripts/daily_update.py",
]

# Matches ANY four-digit year in a season position, never the derived forms
# (f"{CURRENT_SEASON}-" / date(CURRENT_SEASON,). Pinning this to 2026 would
# make the guard self-disable the year the constant moves — precisely the
# event it exists to police.
# A deliberate historical pin (an Elo replay start, a legacy backfill bound)
# opts out by saying so on the line. Better than narrowing the pattern: the
# guard stays broad, and every intentional pin is self-documenting.
_PIN_OK = "season-pin-ok"

_BARE_SEASON_LITERAL = re.compile(
    r"season_year\s*[:=]\s*(?:int\s*=\s*)?\d{4}\b"
    r'|like\(\s*f?["\']\d{4}-'
    r"|\bdate\(\s*\d{4}\s*,"
)


def test_season_end_derives_from_current_season():
    """One source of truth. These drifting apart is the 2027 bug."""
    from src.data.espn_api import _SEASON_END

    assert _SEASON_END.year == CURRENT_SEASON


def test_bpi_fetch_requests_the_anchored_season(monkeypatch):
    """The motivating failure: a stale year here silently returns last
    season's ratings rather than erroring.

    Moves the anchor before asserting. Two weaker versions pass on a
    re-hardcoded `season = 2026`: a module-wide "CURRENT_SEASON in source"
    substring (true as soon as _SEASON_END uses it), and a URL check against
    CURRENT_SEASON while the constant still equals the old literal.

    Patches espn_api's module global rather than reloading the module --
    fetch_bpi_ratings resolves CURRENT_SEASON at call time, and reloading
    espn_api rebinds its exception classes, which breaks `except
    ESPNAPIError` identity for every other test in the session.
    """
    import src.data.espn_api as espn_api

    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return {"items": []}

    monkeypatch.setattr(espn_api, "CURRENT_SEASON", 2027)
    monkeypatch.setattr(espn_api, "fetch_team_id_map", lambda: {})
    monkeypatch.setattr(espn_api, "_get", fake_get)
    espn_api.fetch_bpi_ratings()

    assert "/seasons/2027/powerindex" in seen["url"], seen["url"]


@pytest.mark.parametrize("relpath", _LIVE_PATH_FILES)
def test_live_paths_carry_no_bare_season_literal(relpath):
    """A hand-typed year in a season position is the bug this file exists for."""
    src = (_REPO / relpath).read_text()
    offenders = [
        f"{relpath}:{i}: {line.strip()}"
        for i, line in enumerate(src.splitlines(), 1)
        if _BARE_SEASON_LITERAL.search(line) and _PIN_OK not in line
    ]
    assert not offenders, "bare season literal(s) on a live path:\n" + "\n".join(
        offenders
    )


def _season_year_defaults(relpath):
    """(function name, default AST node) for every season_year default."""
    tree = ast.parse((_REPO / relpath).read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        posonly = args.posonlyargs + args.args
        for arg, default in zip(
            posonly[len(posonly) - len(args.defaults) :], args.defaults
        ):
            if arg.arg == "season_year":
                out.append((node.name, default))
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if arg.arg == "season_year" and default is not None:
                out.append((node.name, default))
    return out


def test_every_season_default_is_the_anchor_by_name():
    """The teeth, checked statically.

    Asserts each default IS the name CURRENT_SEASON, not merely that it
    equals it -- comparing values is vacuous while the constant still equals
    the literal it replaced, which is how the first draft of this test passed
    on a hardcoded 2026.

    Static on purpose. The earlier version reloaded src.db.queries under a
    patched constant to defeat import-time default binding; that reload
    rebinds the module's objects and broke 25 unrelated tests in
    tests/test_routes.py whenever this file ran first. The suite was green
    only because test_season_year sorts alphabetically after test_routes.
    """
    found = _season_year_defaults("src/db/queries.py")
    offenders = [
        f"{name}: {ast.unparse(default)}"
        for name, default in found
        if not (isinstance(default, ast.Name) and default.id == "CURRENT_SEASON")
    ]
    assert not offenders, (
        "season_year default(s) not derived from CURRENT_SEASON: "
        + ", ".join(offenders)
    )
    assert len(found) >= 10, (
        f"expected the season-scoped queries, found {len(found)} — the sweep "
        "may have stopped matching, which would pass vacuously"
    )


def test_default_season_query_excludes_the_following_season(env):
    """End-to-end: the default must actually filter, not merely be named."""
    from src.db.queries import get_completed_games, upsert_game, upsert_team

    session = env.get_session()
    a = upsert_team(session, "Alpha", 0.0, "ALP", "")
    b = upsert_team(session, "Beta", 0.0, "BET", "")
    for year, espn_id in ((CURRENT_SEASON, "700001"), (CURRENT_SEASON + 1, "700002")):
        upsert_game(
            session,
            team_a_id=a.id,
            team_b_id=b.id,
            date=f"{year}-06-01",
            time="7:00 PM ET",
            broadcaster="ESPN",
            espn_id=espn_id,
            season_type=2,
            winner_id=a.id,
            final_score_a=90,
            final_score_b=80,
        )
    session.commit()

    got = {g.espn_id for g in get_completed_games(session)}
    assert "700001" in got, "current-season game missing from the default query"
    assert "700002" not in got, "next season leaked into the default query"


def test_stale_anchor_detector_fires_once_the_calendar_passes_it(caplog, monkeypatch):
    """A forgotten bump is silent by design (the site just serves last
    season), so this log line is the only thing that announces it."""
    import logging

    import scripts.daily_update as du
    import src.data.espn_api as espn_api

    monkeypatch.setattr(espn_api, "today_et", lambda: f"{CURRENT_SEASON + 1}-01-01")
    with caplog.at_level(logging.ERROR, logger="scripts.daily_update"):
        du._warn_if_season_anchor_is_stale()
    assert "bump it in src/constants.py" in caplog.text
    assert str(CURRENT_SEASON + 1) in caplog.text


def test_stale_anchor_detector_quiet_during_the_current_season(caplog, monkeypatch):
    """Must not cry wolf every day of a normal season."""
    import logging

    import scripts.daily_update as du
    import src.data.espn_api as espn_api

    monkeypatch.setattr(espn_api, "today_et", lambda: f"{CURRENT_SEASON}-09-01")
    with caplog.at_level(logging.ERROR, logger="scripts.daily_update"):
        du._warn_if_season_anchor_is_stale()
    assert caplog.text == ""


# --- The OTHER season: the wall clock, and which surfaces may follow it ------
#
# CURRENT_SEASON above is the pinned anchor. clock_season() is the calendar
# year. The tests below pin down the third thing a read surface can do: follow
# neither, and serve the newest season that actually HAS data.


def _seed_completed_season(env, year: int, n: int = 30):
    """n completed games + their frozen win_prob_a + elo_history, all in `year`.

    n defaults above transparency.html's MIN_CAL_GAMES (25) so the seeded season
    is one the calibration panel would actually render, not one it would collapse
    to the "not enough games yet" line.
    """
    from src.db.queries import (
        upsert_daily_ranking,
        upsert_game,
        upsert_team,
    )

    session = env.get_session()
    a = upsert_team(session, f"Alpha {year}", 1.0, "ALP", "")
    b = upsert_team(session, f"Beta {year}", -1.0, "BET", "")
    for i in range(n):
        # Distinct dates: the (date, team_a, team_b) upsert key would dedupe a
        # repeat, quietly seeding fewer games than n.
        date = f"{year}-{6 + i // 28:02d}-{i % 28 + 1:02d}"
        upsert_game(
            session,
            team_a_id=a.id,
            team_b_id=b.id,
            date=date,
            time="7:00 PM ET",
            broadcaster="ESPN",
            espn_id=f"{year}{i:04d}",
            season_type=2,
            winner_id=a.id if i % 2 else b.id,
            final_score_a=90,
            final_score_b=80,
        )
        upsert_daily_ranking(
            session,
            date=date,
            team_a_id=a.id,
            team_b_id=b.id,
            quality_score=50.0,
            importance_score=50.0,
            overall_score=50.0,
            broadcaster="ESPN",
            win_prob_a=0.6,
        )
        session.add(env.EloHistory(team_id=a.id, date=date, rating=1500.0 + i))
        session.add(env.EloHistory(team_id=b.id, date=date, rating=1450.0 - i))
    session.commit()
    session.close()


@pytest.fixture
def rolled_over_clock(monkeypatch):
    """Pretend it is January of the season AFTER the seeded one.

    Patches espn_api.today_et only. That is sufficient *because* clock_season()
    resolves today_et in espn_api's globals at call time -- the documented
    "patch BOTH bound names" footgun applied to `from ... import today_et`, and
    routing the year through one helper is what retires it. If a future refactor
    inlines int(today_et()[:4]) back into app.py, this fixture silently stops
    working and the two tests below pass vacuously.
    """
    import src.data.espn_api as espn_api

    monkeypatch.setattr(espn_api, "today_et", lambda: f"{CURRENT_SEASON + 1}-01-15")


def test_clock_season_follows_the_calendar_not_the_anchor(rolled_over_clock):
    """The whole point of the second name: it is allowed to disagree."""
    from src.data.espn_api import clock_season

    assert clock_season() == CURRENT_SEASON + 1
    assert clock_season() != CURRENT_SEASON


def test_elo_history_serves_the_newest_populated_season_in_the_offseason(
    env, client, rolled_over_clock
):
    """January must show last season's completed trajectory, not an empty grid."""
    _seed_completed_season(env, CURRENT_SEASON)

    data = client.get("/api/elo-history").json()

    assert data["season"] == CURRENT_SEASON, (
        "offseason request fell through to the empty calendar year"
    )
    assert data["teams"], "fell back to the right season but returned no rows"


def test_calibration_serves_the_newest_populated_season_in_the_offseason(
    env, client, rolled_over_clock
):
    """/transparency is the honesty page; a blank panel every January is the
    worst surface on the site to go dark."""
    _seed_completed_season(env, CURRENT_SEASON)

    data = client.get("/api/calibration").json()

    assert data["season"] == CURRENT_SEASON, (
        "offseason request fell through to the empty calendar year"
    )
    assert data["n"] == 30, f"fell back to the right season but scored {data['n']} games"


def test_explicit_season_still_wins_over_the_populated_default(
    env, client, rolled_over_clock
):
    """The fallback is a DEFAULT, not a redirect: an explicit empty season must
    still report itself empty rather than being helpfully rewritten."""
    _seed_completed_season(env, CURRENT_SEASON)

    elo = client.get(f"/api/elo-history?season={CURRENT_SEASON + 1}").json()
    cal = client.get(f"/api/calibration?season={CURRENT_SEASON + 1}").json()

    assert elo["season"] == CURRENT_SEASON + 1 and not elo["teams"]
    assert cal["season"] == CURRENT_SEASON + 1 and cal["n"] == 0


# The deliberate OTHER half of the split -- /api/shot-making and /player/{id}
# stay on the clock, so an offseason request gets an empty board rather than a
# finished season relabeled as current. That property is already pinned by
# tests/test_shot_making_endpoint.py::test_endpoint_does_not_fall_back_to_prior_season;
# not duplicated here, only cross-referenced so the split reads as a decision.


def test_populated_default_never_runs_ahead_of_the_calendar(env, client, monkeypatch):
    """A future-dated row must not become the site default.

    Codex adversarial review, and the half of it that was a real regression:
    the lookups are MAX() over a date column, so one row in a future season
    would carry both pages there. The clock-derived code this replaces could not
    do that, so the bound closes a hole the change itself opened rather than a
    pre-existing one.

    Unreachable from ESPN today (both lookups need a COMPLETED game), so what is
    seeded here is a repair script or a hand-edited row -- which is exactly the
    class of thing that reaches a database and not an API.
    """
    _seed_completed_season(env, CURRENT_SEASON)
    _seed_completed_season(env, CURRENT_SEASON + 5, n=1)

    elo = client.get("/api/elo-history").json()
    cal = client.get("/api/calibration").json()

    assert elo["season"] == CURRENT_SEASON, (
        f"a future-dated row dragged /rankings to {elo['season']}"
    )
    assert cal["season"] == CURRENT_SEASON, (
        f"a future-dated row dragged /transparency to {cal['season']}"
    )
    assert elo["teams"] and cal["n"], "landed on the right season but served nothing"


def test_future_row_does_not_reblank_the_pages_in_the_offseason(
    env, client, rolled_over_clock
):
    """The two conditions must be tested TOGETHER, which is what this branch's
    first attempt got wrong (Codex adversarial review round 2).

    That attempt clamped a future MAX(date) down to clock_season(). With the
    real clock that lands on a season holding data, so the test passed -- but in
    the OFFSEASON the clock year is empty by definition, so a single stray
    future row turned the fallback back into the blank page this whole branch
    exists to remove. Seeding the stray row and rolling the clock are each
    harmless alone; only together do they express the property.

    The season to serve is the newest populated season NOT AFTER the clock --
    which is a filter on the lookup, not a clamp on its result.
    """
    _seed_completed_season(env, CURRENT_SEASON)
    _seed_completed_season(env, CURRENT_SEASON + 5, n=1)

    elo = client.get("/api/elo-history").json()
    cal = client.get("/api/calibration").json()

    assert elo["season"] == CURRENT_SEASON, (
        f"offseason + stray future row served {elo['season']}, not the last real season"
    )
    assert cal["season"] == CURRENT_SEASON, (
        f"offseason + stray future row served {cal['season']}, not the last real season"
    )
    assert elo["teams"], "/rankings went blank again"
    assert cal["n"] == 30, f"/transparency went blank again (n={cal['n']})"


def test_populated_default_still_follows_a_sparse_CURRENT_season(
    env, client, monkeypatch
):
    """The deliberate NON-fix, pinned so it is not "hardened" into a bug.

    On opening night the newest populated season is the current one with a
    couple of games in it, and that is the honest thing to show -- the
    clock-derived code served exactly this, and /transparency has a
    MIN_CAL_GAMES branch built to render it. A completeness threshold here
    (the shape /api/team-style uses) would instead keep showing the FINISHED
    season deep into the live one, because these tables accumulate rows all
    season while team_style holds a fixed row per team.
    """
    import src.data.espn_api as espn_api

    _seed_completed_season(env, CURRENT_SEASON)
    _seed_completed_season(env, CURRENT_SEASON + 1, n=2)
    monkeypatch.setattr(
        espn_api, "today_et", lambda: f"{CURRENT_SEASON + 1}-05-20"
    )

    elo = client.get("/api/elo-history").json()
    cal = client.get("/api/calibration").json()

    assert elo["season"] == CURRENT_SEASON + 1, (
        "a live season with few games was suppressed in favour of the finished one"
    )
    assert cal["season"] == CURRENT_SEASON + 1 and cal["n"] == 2
