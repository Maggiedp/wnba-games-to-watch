"""Tests for tiebreaker functions."""

import pytest

from src.scoring.monte_carlo import TeamStanding
from src.scoring.tiebreakers import (
    conference_playoff_winpct,
    head_to_head_winpct,
)


def _ts(name, wins=0, losses=0, elo=1500.0, h2h=None):
    return TeamStanding(name=name, wins=wins, losses=losses, elo=elo, h2h=h2h or {})


def test_h2h_two_team_tie_sweep():
    """A swept B 3-0 → A=1.0, B=0.0."""
    standings = {
        "A": _ts("A", h2h={"B": [3, 0]}),
        "B": _ts("B", h2h={"A": [0, 3]}),
    }
    result = head_to_head_winpct(["A", "B"], standings)
    assert result["A"] == 1.0
    assert result["B"] == 0.0


def test_h2h_two_team_tie_split():
    """A and B split 2-2 → both 0.5."""
    standings = {
        "A": _ts("A", h2h={"B": [2, 2]}),
        "B": _ts("B", h2h={"A": [2, 2]}),
    }
    result = head_to_head_winpct(["A", "B"], standings)
    assert result["A"] == 0.5
    assert result["B"] == 0.5


def test_h2h_three_team_tie_one_clear_winner():
    """A is 2-0 vs B and 2-0 vs C → A=1.0, B and C tied below."""
    standings = {
        "A": _ts("A", h2h={"B": [2, 0], "C": [2, 0]}),
        "B": _ts("B", h2h={"A": [0, 2], "C": [1, 1]}),
        "C": _ts("C", h2h={"A": [0, 2], "B": [1, 1]}),
    }
    result = head_to_head_winpct(["A", "B", "C"], standings)
    assert result["A"] == 1.0  # 4-0
    assert result["B"] == 0.25  # 1-3
    assert result["C"] == 0.25  # 1-3


def test_h2h_ignores_games_outside_tied_group():
    """Only counts games among the tied teams; games vs outsiders excluded."""
    standings = {
        "A": _ts("A", h2h={"B": [2, 0], "Z": [0, 5]}),
        "B": _ts("B", h2h={"A": [0, 2], "Z": [5, 0]}),
    }
    result = head_to_head_winpct(["A", "B"], standings)
    assert result["A"] == 1.0
    assert result["B"] == 0.0


def test_h2h_no_games_played_returns_half():
    """If teams haven't played each other, return 0.5 (treats as tied → next tiebreaker breaks it)."""
    standings = {
        "A": _ts("A", h2h={}),
        "B": _ts("B", h2h={}),
    }
    result = head_to_head_winpct(["A", "B"], standings)
    assert result["A"] == 0.5
    assert result["B"] == 0.5


def test_conf_playoff_same_conference():
    """A is 4-2 vs East playoff teams, B is 2-4 — A wins this tiebreaker."""
    # Use real conference assignments. A=Liberty (East), B=Sun (East).
    # Playoff teams (East): Liberty, Sun, Fever. Playoff teams (West): Aces, Lynx.
    standings = {
        "New York Liberty": _ts(
            "New York Liberty",
            h2h={
                "Connecticut Sun": [2, 1],  # 2-1 vs East playoff team
                "Indiana Fever": [2, 1],  # 2-1 vs East playoff team
                "Las Vegas Aces": [1, 2],  # excluded (other conference)
            },
        ),
        "Connecticut Sun": _ts(
            "Connecticut Sun",
            h2h={
                "New York Liberty": [1, 2],  # 1-2
                "Indiana Fever": [1, 2],  # 1-2
                "Las Vegas Aces": [2, 1],  # excluded
            },
        ),
        "Indiana Fever": _ts("Indiana Fever"),
        "Las Vegas Aces": _ts("Las Vegas Aces"),
        "Minnesota Lynx": _ts("Minnesota Lynx"),
    }
    provisional = {
        "New York Liberty",
        "Connecticut Sun",
        "Indiana Fever",
        "Las Vegas Aces",
        "Minnesota Lynx",
    }
    result = conference_playoff_winpct(
        ["New York Liberty", "Connecticut Sun"],
        standings,
        provisional,
        same_conference=True,
    )
    assert result["New York Liberty"] == pytest.approx(4 / 6)
    assert result["Connecticut Sun"] == pytest.approx(2 / 6)


def test_conf_playoff_other_conference():
    """Same fixture as above, but now we look at games vs WEST playoff teams."""
    standings = {
        "New York Liberty": _ts(
            "New York Liberty",
            h2h={
                "Las Vegas Aces": [3, 0],
                "Minnesota Lynx": [1, 2],
                "Connecticut Sun": [2, 1],
            },
        ),
        "Connecticut Sun": _ts(
            "Connecticut Sun",
            h2h={
                "Las Vegas Aces": [0, 3],
                "Minnesota Lynx": [2, 1],
            },
        ),
        "Las Vegas Aces": _ts("Las Vegas Aces"),
        "Minnesota Lynx": _ts("Minnesota Lynx"),
    }
    provisional = {
        "New York Liberty",
        "Connecticut Sun",
        "Las Vegas Aces",
        "Minnesota Lynx",
    }
    result = conference_playoff_winpct(
        ["New York Liberty", "Connecticut Sun"],
        standings,
        provisional,
        same_conference=False,
    )
    assert result["New York Liberty"] == pytest.approx(4 / 6)
    assert result["Connecticut Sun"] == pytest.approx(2 / 6)


def test_conf_playoff_no_qualifying_opponents_returns_half():
    """If no playoff teams in the target conference, return 0.5 (advance to next step)."""
    standings = {
        "New York Liberty": _ts("New York Liberty"),
        "Connecticut Sun": _ts("Connecticut Sun"),
    }
    # Provisional set has nobody in the West.
    provisional = {"New York Liberty", "Connecticut Sun"}
    result = conference_playoff_winpct(
        ["New York Liberty", "Connecticut Sun"],
        standings,
        provisional,
        same_conference=False,
    )
    assert result["New York Liberty"] == 0.5
    assert result["Connecticut Sun"] == 0.5
