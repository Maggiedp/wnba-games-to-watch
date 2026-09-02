"""The current season is one constant, and the live paths derive from it.

Context: ~17 sites re-typed the literal 2026 instead of deriving from the
anchor that `fetch_bpi_ratings` already depends on. A forgotten annual bump
used to break BPI; a re-typed literal broke everything else independently.
"""

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


def test_season_end_derives_from_current_season():
    """One source of truth. These drifted apart is exactly the 2027 bug."""
    from src.data.espn_api import _SEASON_END

    assert _SEASON_END.year == CURRENT_SEASON


def test_bpi_fetch_uses_the_shared_anchor():
    """fetch_bpi_ratings builds /seasons/{year}/powerindex — a stale year
    here silently returns last season's ratings rather than erroring."""
    src = (_REPO / "src/data/espn_api.py").read_text()
    assert "CURRENT_SEASON" in src, "espn_api must derive from the anchor"


@pytest.mark.parametrize("relpath", _LIVE_PATH_FILES)
def test_live_paths_carry_no_bare_season_literal(relpath):
    """A bare `2026` in a season position is the bug this file exists for.

    Scoped to live paths so the deliberately-pinned backfills don't trip it.
    Dates inside comments/docstrings are fine — we match season arguments
    and date filters, not prose.
    """
    src = (_REPO / relpath).read_text()
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(r"season_year\s*[:=]\s*(?:int\s*=\s*)?2026\b", code):
            offenders.append(f"{relpath}:{i}: {line.strip()}")
        # Game.date.like("2026-%") / date(2026, 4, 1) style season filters
        if re.search(r'like\(\s*f?["\']2026-', code) or re.search(
            r"\bdate\(\s*2026\s*,", code
        ):
            offenders.append(f"{relpath}:{i}: {line.strip()}")
    assert not offenders, "bare season literal(s) on a live path:\n" + "\n".join(
        offenders
    )


# Every live query whose default season must track the anchor.
_SEASON_DEFAULTED = [
    "get_completed_games",
    "get_team_records",
    "get_completed_postseason_games",
    "get_head_to_head",
    "get_completed_games_missing_excitement",
    "get_games_for_excitement_refresh",
    "get_completed_rankings",
    "get_calibration_pairs",
    "get_completed_games_missing_shape",
    "get_completed_games_missing_shots",
]


@pytest.fixture
def queries_at_2027(monkeypatch):
    """Re-import the query module with the anchor moved to 2027.

    Necessary because default arguments bind at IMPORT time: monkeypatching
    the constant alone changes nothing already bound, so a test that only
    compares `default == CURRENT_SEASON` passes on a hardcoded 2026 too --
    it is vacuous while the anchor happens to equal the old literal.
    Reloading is what makes the assertion able to fail.
    """
    import importlib

    import src.constants
    from src.db import queries

    monkeypatch.setattr(src.constants, "CURRENT_SEASON", 2027)
    reloaded = importlib.reload(queries)
    yield reloaded
    monkeypatch.undo()
    importlib.reload(queries)


@pytest.mark.parametrize("fname", _SEASON_DEFAULTED)
def test_season_default_follows_the_anchor_when_it_moves(fname, queries_at_2027):
    """The teeth: with the anchor at 2027, a leftover literal reads 2026.

    A rename that failed to wire through still passes the source-literal
    scan (no bare 2026 left to find) and fails here.
    """
    import inspect

    default = (
        inspect.signature(getattr(queries_at_2027, fname))
        .parameters["season_year"]
        .default
    )
    assert default == 2027, (
        f"{fname}'s season_year default stayed {default!r} after the anchor "
        f"moved to 2027 -- it is not derived from CURRENT_SEASON"
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
    with caplog.at_level(logging.ERROR):
        du._warn_if_season_anchor_is_stale()
    assert "bump CURRENT_SEASON" in caplog.text
    assert str(CURRENT_SEASON + 1) in caplog.text


def test_stale_anchor_detector_quiet_during_the_current_season(caplog, monkeypatch):
    """Must not cry wolf every day of a normal season."""
    import logging

    import scripts.daily_update as du

    monkeypatch.setattr(du, "today_et", lambda: f"{CURRENT_SEASON}-09-01")
    with caplog.at_level(logging.ERROR):
        du._warn_if_season_anchor_is_stale()
    assert caplog.text == ""


def test_stale_anchor_detector_cannot_read_the_schedule_fetch():
    """Regression: the first version of this detector inspected the fetched
    games and was DEAD CODE -- fetch_schedule_and_results caps its window at
    _SEASON_END, which derives from CURRENT_SEASON, so a game past the anchor
    can never appear there. Keep it reading the clock.
    """
    import inspect

    import scripts.daily_update as du

    src = inspect.getsource(du._warn_if_season_anchor_is_stale)
    assert "today_et" in src, "detector must read the clock, not the fetch"
