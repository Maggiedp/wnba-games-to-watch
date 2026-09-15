"""Tests for the game-night production probe's pure predicates.

The probe itself talks to production over HTTP; only the decision logic is
tested here. The property that matters most: a check with no data to judge
must SKIP, never PASS — an off-day run that printed all-green would prove
nothing and would be indistinguishable from a working live path.
"""

import pytest

from scripts.verify_game_night import (
    FAIL,
    PASS,
    SKIP,
    check_column_suppression,
    check_live_flags,
    check_seed_movement,
    classify_night,
    playoffs_column_is_dead,
    seed_movement,
)


def _game(status="STATUS_SCHEDULED", season_type=2, **kw):
    return {
        "event_id": kw.get("event_id", "1"),
        "team_a": kw.get("team_a", "Dallas Wings"),
        "team_b": kw.get("team_b", "Los Angeles Sparks"),
        "status": status,
        "season_type": season_type,
    }


def _odds(name, abbr, mp, seeds, live=False, live_state=None):
    return {
        "team": name,
        "abbreviation": abbr,
        "make_playoffs_prob": mp,
        "seed_distribution": seeds,
        "live": live,
        "live_state": live_state,
    }


# --- classify_night -------------------------------------------------------


def test_no_games_is_an_off_night():
    assert classify_night([]) == "off"


def test_game_in_progress_is_live():
    assert classify_night([_game(status="STATUS_IN_PROGRESS")]) == "live"


def test_all_finals_is_settled():
    games = [_game(status="STATUS_FINAL"), _game(status="STATUS_FINAL")]
    assert classify_night(games) == "settled"


def test_scheduled_but_not_tipped_is_pregame():
    assert classify_night([_game(status="STATUS_SCHEDULED")]) == "pregame"


def test_a_final_alongside_a_live_game_is_still_live():
    games = [_game(status="STATUS_FINAL"), _game(status="STATUS_IN_PROGRESS")]
    assert classify_night(games) == "live"


def test_postseason_wins_over_in_progress():
    """Live mode disables itself on a season_type == 3 slate.

    If this ever returned "live" the probe would demand live flags during the
    postseason and report FAIL against correct behavior.
    """
    games = [_game(status="STATUS_IN_PROGRESS", season_type=3)]
    assert classify_night(games) == "postseason"


def test_one_postseason_game_makes_the_whole_slate_postseason():
    games = [_game(season_type=2), _game(status="STATUS_IN_PROGRESS", season_type=3)]
    assert classify_night(games) == "postseason"


# --- playoffs_column_is_dead (mirrors the JS predicate) -------------------


def test_column_dead_when_every_team_is_in_or_out():
    odds = [
        _odds("A", "A", 1.0, {}),
        _odds("B", "B", 0.0, {}),
        _odds("C", "C", 1.0, {}),
    ]
    assert playoffs_column_is_dead(odds) is True


def test_column_alive_mid_race():
    """The branch the JS originally got wrong — must NOT read as dead."""
    odds = [_odds("A", "A", 1.0, {}), _odds("B", "B", 0.62, {})]
    assert playoffs_column_is_dead(odds) is False


def test_column_alive_for_a_team_that_merely_rounds_to_zero():
    odds = [_odds("A", "A", 1.0, {}), _odds("B", "B", 0.004, {})]
    assert playoffs_column_is_dead(odds) is False


def test_empty_odds_is_not_dead():
    assert playoffs_column_is_dead([]) is False


# --- seed_movement --------------------------------------------------------


def test_seed_movement_reports_changed_cells_for_named_teams():
    snap = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40, "6": 0.60})]
    live = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.55, "6": 0.45}, live=True)]
    moved = seed_movement(live, snap, {"Dallas Wings"})
    assert moved["Dallas Wings"] == [("5", 0.40, 0.55), ("6", 0.60, 0.45)]


def test_seed_movement_ignores_teams_not_playing():
    snap = [_odds("Atlanta Dream", "ATL", 1.0, {"3": 0.20})]
    live = [_odds("Atlanta Dream", "ATL", 1.0, {"3": 0.90}, live=True)]
    assert seed_movement(live, snap, {"Dallas Wings"}) == {}


def test_seed_movement_ignores_sub_jitter_wobble():
    """A 10k run carries ~±0.5pp of jitter; that is not news."""
    snap = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.400})]
    live = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.404}, live=True)]
    assert seed_movement(live, snap, {"Dallas Wings"}) == {}


def test_seed_movement_counts_a_seed_that_appears_from_nothing():
    snap = [_odds("Dallas Wings", "DAL", 1.0, {"5": 1.0})]
    live = [_odds("Dallas Wings", "DAL", 1.0, {"4": 0.30, "5": 0.70}, live=True)]
    moved = seed_movement(live, snap, {"Dallas Wings"})
    assert ("4", 0.0, 0.30) in moved["Dallas Wings"]


# --- check_* return SKIP, never a vacuous PASS ---------------------------


def test_live_flags_skip_when_there_are_no_odds():
    result = check_live_flags([], expect_state="live")
    assert result.status == SKIP


def test_live_flags_pass_on_a_live_payload():
    odds = [_odds("A", "A", 1.0, {}, live=True, live_state="live")]
    assert check_live_flags(odds, expect_state="live").status == PASS


def test_live_flags_fail_when_the_overlay_did_not_engage():
    odds = [_odds("A", "A", 1.0, {}, live=False, live_state=None)]
    assert check_live_flags(odds, expect_state="live").status == FAIL


def test_live_flags_fail_on_the_wrong_state():
    odds = [_odds("A", "A", 1.0, {}, live=True, live_state="settled")]
    assert check_live_flags(odds, expect_state="live").status == FAIL


def test_seed_movement_check_fails_when_the_playing_teams_did_not_move():
    snap = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40})]
    live = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40}, live=True)]
    result = check_seed_movement(live, snap, {"Dallas Wings"})
    assert result.status == FAIL


def test_seed_movement_check_skips_without_a_snapshot():
    live = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40}, live=True)]
    assert check_seed_movement(live, [], {"Dallas Wings"}).status == SKIP


def test_seed_movement_check_skips_when_no_teams_are_playing():
    snap = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40})]
    assert check_seed_movement(snap, snap, set()).status == SKIP


def test_column_suppression_skips_on_an_empty_payload():
    assert check_column_suppression([]).status == SKIP


def test_column_suppression_reports_pass_for_a_clinched_field():
    odds = [_odds("A", "A", 1.0, {}, live=True), _odds("B", "B", 0.0, {}, live=True)]
    assert check_column_suppression(odds).status == PASS


@pytest.mark.parametrize("night", ["off", "pregame"])
def test_off_and_pregame_nights_have_nothing_to_prove(night):
    """The probe must not manufacture a green run out of an empty slate."""
    from scripts.verify_game_night import checks_for_night

    results = checks_for_night(night, odds=[], snapshot=[], playing=set())
    assert results, "an off night should still report something"
    assert all(r.status == SKIP for r in results)
