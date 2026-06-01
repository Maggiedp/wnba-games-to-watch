"""Tests for src/api/blurbs.py plain-language score bands."""

from src.api import blurbs


def test_quality_blurb_two_contenders():
    s = blurbs.quality_blurb(80, bpi_a=6.2, bpi_b=4.8, team_a="Storm", team_b="Aces")
    assert "Storm" in s and "Aces" in s
    assert "6.2" in s and "4.8" in s
    assert "best" in s.lower()


def test_quality_blurb_lopsided_when_bpi_gap_large():
    s = blurbs.quality_blurb(55, bpi_a=6.0, bpi_b=-2.0, team_a="Storm", team_b="Sky")
    assert "lopsided" in s.lower()
    assert "Storm" in s


def test_quality_blurb_two_low():
    s = blurbs.quality_blurb(20, bpi_a=-3.0, bpi_b=-4.0, team_a="Sky", team_b="Wings")
    assert "rebuilding" in s.lower()


def test_importance_blurb_bands():
    assert "high stakes" in blurbs.importance_blurb(80).lower()
    assert "seeding" in blurbs.importance_blurb(50).lower()
    assert "low" in blurbs.importance_blurb(10).lower()


def test_importance_blurb_none_is_not_simulated():
    s = blurbs.importance_blurb(None)
    assert "not simulated" in s.lower()


def test_win_prob_blurb_coin_flip():
    # win_prob_a is a 0–1 fraction (Elo probability), not a 0–100 number.
    s = blurbs.win_prob_blurb(0.51, team_a="Storm", team_b="Aces")
    assert "coin flip" in s.lower()


def test_win_prob_blurb_favored_names_higher_team_and_pct():
    s = blurbs.win_prob_blurb(0.62, team_a="Storm", team_b="Aces")
    assert "Storm" in s and "62%" in s

    s2 = blurbs.win_prob_blurb(0.38, team_a="Storm", team_b="Aces")
    assert "Aces" in s2 and "62%" in s2  # favored team is the underdog's opponent


def test_win_prob_blurb_none_returns_empty():
    assert blurbs.win_prob_blurb(None, team_a="Storm", team_b="Aces") == ""
