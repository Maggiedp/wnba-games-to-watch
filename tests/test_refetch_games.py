"""Tests for scripts/refetch_games.py — the out-of-window repair path."""

from datetime import date

import pytest


@pytest.fixture
def script(monkeypatch):
    import scripts.refetch_games as rg

    monkeypatch.setattr(rg, "init_db", lambda: None)

    class FakeSession:
        def close(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(rg, "get_session", lambda: FakeSession())
    return rg


def test_main_passes_the_parsed_window_to_the_ingest(script, monkeypatch):
    seen = {}

    def fake_ingest(session, window=None):
        seen["window"] = window
        return []

    monkeypatch.setattr(script, "fetch_and_store_games", fake_ingest)

    rc = script.main(["--start", "2026-09-17", "--end", "2026-09-17"])

    assert rc == 0
    assert seen["window"] == (date(2026, 9, 17), date(2026, 9, 17))


def test_main_rejects_an_end_before_the_start(script, monkeypatch):
    """A reversed range silently fetches nothing, which reads as success."""

    def must_not_run(session, window=None):
        raise AssertionError("ingest must not run on an invalid range")

    monkeypatch.setattr(script, "fetch_and_store_games", must_not_run)

    assert script.main(["--start", "2026-09-18", "--end", "2026-09-17"]) == 2


def test_main_returns_failure_when_the_ingest_raises(script, monkeypatch):
    def boom(session, window=None):
        raise RuntimeError("ESPN down")

    monkeypatch.setattr(script, "fetch_and_store_games", boom)

    assert script.main(["--start", "2026-09-17", "--end", "2026-09-17"]) == 1
