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

    monkeypatch.setattr(du, "today_et", lambda: f"{CURRENT_SEASON + 1}-01-01")
    with caplog.at_level(logging.ERROR, logger="scripts.daily_update"):
        du._warn_if_season_anchor_is_stale()
    assert "bump it in src/constants.py" in caplog.text
    assert str(CURRENT_SEASON + 1) in caplog.text


def test_stale_anchor_detector_quiet_during_the_current_season(caplog, monkeypatch):
    """Must not cry wolf every day of a normal season."""
    import logging

    import scripts.daily_update as du

    monkeypatch.setattr(du, "today_et", lambda: f"{CURRENT_SEASON}-09-01")
    with caplog.at_level(logging.ERROR, logger="scripts.daily_update"):
        du._warn_if_season_anchor_is_stale()
    assert caplog.text == ""
