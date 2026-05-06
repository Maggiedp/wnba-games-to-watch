"""Tests for constants module — primarily the conference-assignment helpers."""

import pytest

from src.constants import TEAM_CONFERENCES, assert_all_teams_have_conferences


def test_team_conferences_covers_all_teams():
    assert len(TEAM_CONFERENCES) >= 13
    assert set(TEAM_CONFERENCES.values()) == {"East", "West"}


def test_team_conferences_split_east_west():
    east = [n for n, c in TEAM_CONFERENCES.items() if c == "East"]
    west = [n for n, c in TEAM_CONFERENCES.items() if c == "West"]
    assert len(east) >= 6
    assert len(west) >= 7


def test_assert_all_teams_have_conferences_passes_when_complete():
    standings = {name: {} for name in TEAM_CONFERENCES}
    assert_all_teams_have_conferences(standings)  # no raise


def test_assert_all_teams_have_conferences_raises_on_missing():
    standings = {"FakeTeam": {}}
    with pytest.raises(KeyError, match="FakeTeam"):
        assert_all_teams_have_conferences(standings)
