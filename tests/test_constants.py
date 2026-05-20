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


def test_season_end_covers_playoff_window():
    """The schedule fetch horizon must extend past the Finals or postseason
    games never enter the DB, freezing the bracket sim mid-playoffs.

    WNBA 2026 postseason runs Sept 27 → mid/late October; a Bo7 Finals can
    reach Oct 20-25. Lock the horizon at >= Oct 25 of the regular-season year.
    """
    from datetime import date

    from src.data.espn_api import _SEASON_END

    season_year = _SEASON_END.year
    assert _SEASON_END >= date(season_year, 10, 25), (
        f"_SEASON_END={_SEASON_END} too early — must extend through the Finals "
        f"(WNBA Bo7 can reach ~Oct 25)"
    )
