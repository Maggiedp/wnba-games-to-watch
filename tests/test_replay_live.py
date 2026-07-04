"""Tests for the /replay live-strip predicate + endpoint (Plan 3d)."""

from src.api.routes import is_live_status


def test_is_live_status_true_for_the_three_live_states():
    assert is_live_status("STATUS_IN_PROGRESS")
    assert is_live_status("STATUS_HALFTIME")
    assert is_live_status("STATUS_END_PERIOD")


def test_is_live_status_false_otherwise():
    assert not is_live_status("STATUS_SCHEDULED")
    assert not is_live_status("STATUS_FINAL")
    assert not is_live_status("STATUS_POSTPONED")
    assert not is_live_status(None)
    assert not is_live_status("")
