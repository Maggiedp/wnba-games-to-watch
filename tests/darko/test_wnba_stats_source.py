"""Integration test for the pbpstats-backed WNBA possession/box source.

Hits data.wnba.com over the network; skips cleanly when offline. The
reconciliation assertion (possession points sum == box final score total) is the
gate: it proves the points-per-possession accessor is correct.
"""

import tempfile

import pytest


def _skip_if_offline(exc):
    import requests

    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        pytest.skip(f"network unavailable: {exc}")
    raise exc


def test_import_does_not_globally_patch_requests():
    """Importing the module must NOT leave a global UA patch on Session.request.

    The UA injection is scoped to a `_wnba_ua_session` context manager; outside
    it, the global `requests.sessions.Session.request` must be the unpatched one.
    """
    import contextlib

    import requests

    import src.darko.wnba_stats_source as src

    # The patched callable is the inner `_patched`; at module top level the global
    # must NOT be it.
    assert requests.sessions.Session.request.__name__ != "_patched"
    # And the scoping helper is exposed as a context manager.
    assert isinstance(src._wnba_ua_session(), contextlib._GeneratorContextManager)


def test_stint_rows_reconcile_to_final_score():
    from src.darko.wnba_stats_source import game_box, stint_rows_for_game

    try:
        rows = stint_rows_for_game("1012400006", tempfile.mkdtemp())
        box = game_box("1012400006", 2024)
    except Exception as exc:  # noqa: BLE001 - re-raise unless it's a network error
        _skip_if_offline(exc)

    # every possession has 5-on-5
    assert rows["off_players"].apply(len).eq(5).all()
    assert rows["def_players"].apply(len).eq(5).all()
    # points reconcile to the game's final score total
    assert abs(rows["points"].sum() - box["points"].sum()) < 1e-6
    # realistic pace: ~150-170 total possessions
    assert 140 <= rows["possessions"].sum() <= 180
    # degradation counts are exposed on the frame for the coverage guard; this
    # game is known-clean (every possession resolved to a 5-on-5).
    assert rows.attrs["dropped_possessions"] == 0
    assert rows.attrs["total_possessions"] == len(rows)


def test_wnba_game_ids_returns_completed_games():
    from src.darko.wnba_stats_source import wnba_game_ids

    try:
        ids = wnba_game_ids(2024)
    except Exception as exc:  # noqa: BLE001
        _skip_if_offline(exc)

    assert len(ids) > 100  # a full WNBA regular+playoff season
    assert all(g.startswith("10") for g in ids)
