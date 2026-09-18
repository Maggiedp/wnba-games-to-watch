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
    candidate_baseline_dates,
    daily_run_consumed,
    probed_games,
    check_column_suppression,
    check_live_flags,
    check_repeatability,
    checks_for_night,
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
    results = checks_for_night(night, odds=[], snapshot=[], playing=set())
    assert results, "an off night should still report something"
    assert all(r.status == SKIP for r in results)


# --- snapshot baseline + the 6 AM boundary (Codex adversarial review) -----


def _dated(date, status="STATUS_FINAL", **kw):
    g = _game(status=status, **kw)
    g["date"] = date
    return g


def test_probed_games_excludes_scheduled():
    """A scheduled game produces no override, so it must not drag the baseline
    forward onto a date whose 6 AM run has not happened yet."""
    games = [_dated("2026-09-20"), _dated("2026-09-21", status="STATUS_SCHEDULED")]
    assert [g["date"] for g in probed_games(games)] == ["2026-09-20"]


def test_baseline_candidates_run_newest_first_from_today():
    """The overlay perturbs whatever the LAST daily run stored, so the newest
    existing snapshot is the baseline."""
    assert candidate_baseline_dates("2026-09-18", back=3) == [
        "2026-09-18",
        "2026-09-17",
        "2026-09-16",
    ]


def test_baseline_search_never_looks_past_today():
    """Bound the search, don't clamp the result — a future-dated row must not
    become the baseline."""
    assert max(candidate_baseline_dates("2026-09-18")) == "2026-09-18"


def test_consumed_when_the_newest_snapshot_postdates_the_games():
    """store_playoff_probabilities keys every snapshot to today_et() at write
    time, so a snapshot dated after the game proves that run consumed it."""
    assert daily_run_consumed("2026-09-21", [_dated("2026-09-20")]) is True


def test_not_consumed_while_the_snapshot_is_the_games_own_morning():
    assert daily_run_consumed("2026-09-20", [_dated("2026-09-20")]) is False


def test_not_consumed_when_tonights_games_are_the_newest_thing():
    """Evening case: the window holds last night's finals AND tonight's live
    games, and today's snapshot predates tonight."""
    probed = [_dated("2026-09-20"), _dated("2026-09-21", status="STATUS_IN_PROGRESS")]
    assert daily_run_consumed("2026-09-21", probed) is False


def test_not_consumed_without_a_baseline():
    assert daily_run_consumed(None, [_dated("2026-09-20")]) is False


def test_consumed_night_skips_rather_than_failing():
    """The spurious-FAIL guard: demanding live flags after the daily run has
    folded the results in would report FAIL against correct behavior."""
    results = checks_for_night("consumed", odds=[], snapshot=[], playing=set())
    assert results
    assert all(r.status == SKIP for r in results)
    assert "standing down" in results[0].detail


# --- ESPN's three in-progress states (observed live, 2026-09-17) ----------


@pytest.mark.parametrize(
    "status", ["STATUS_IN_PROGRESS", "STATUS_HALFTIME", "STATUS_END_PERIOD"]
)
def test_every_in_progress_state_counts_as_live(status):
    """The real 2026-09-17 slate carried all three at once. Matching only
    STATUS_IN_PROGRESS would call an all-halftime slate 'pregame' and skip
    every check."""
    assert classify_night([_game(status=status)]) == "live"


@pytest.mark.parametrize("status", ["STATUS_HALFTIME", "STATUS_END_PERIOD"])
def test_halftime_games_are_probed_for_seed_movement(status):
    """They were silently dropped from `playing`, so their seed movement went
    unchecked on the one night live mode has ever run."""
    games = [_dated("2026-09-17", status=status)]
    assert probed_games(games) == games


# --- FAIL vs SKIP when the overlay stands down (settled 2026-09-17) -------


def test_not_engaging_without_usable_input_is_not_a_defect():
    """ESPN published winprobability: [] for every in-progress game on
    2026-09-17. The overlay refuses to publish odds it cannot condition on,
    which is correct behavior — the check has no input to judge, so SKIP."""
    odds = [_odds("A", "A", 1.0, {}, live=False)]
    r = check_live_flags(odds, expect_state="live", wp_available=False)
    assert r.status == SKIP
    assert "standing down" in r.detail


def test_not_engaging_WITH_usable_input_is_a_real_failure():
    """The distinction that makes the SKIP above safe: if ESPN had win
    probability and the overlay still did not engage, that is our bug."""
    odds = [_odds("A", "A", 1.0, {}, live=False)]
    r = check_live_flags(odds, expect_state="live", wp_available=True)
    assert r.status == FAIL


def test_seed_movement_is_unjudgeable_when_the_overlay_is_not_engaged():
    """A non-live payload IS the snapshot, so zero movement is expected and
    FAILing on it is a second false alarm."""
    snap = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40})]
    served = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40}, live=False)]
    assert check_seed_movement(served, snap, {"Dallas Wings"}).status == SKIP


def test_seed_movement_still_fails_when_live_but_frozen():
    """The genuine defect this check exists for must survive the guard above."""
    snap = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40})]
    served = [_odds("Dallas Wings", "DAL", 1.0, {"5": 0.40}, live=True)]
    assert check_seed_movement(served, snap, {"Dallas Wings"}).status == FAIL


def test_live_night_without_wp_reports_nothing_verified():
    """End to end: the whole night must not read as a pass, nor as a failure."""
    odds = [_odds("A", "A", 1.0, {}, live=False)]
    results = checks_for_night(
        "live", odds, odds, {"Dallas Wings"}, wp_available=False
    )
    assert not any(r.status == FAIL for r in results)
    assert any(r.status == SKIP for r in results)


def test_repeatability_is_vacuous_when_the_overlay_is_not_engaged():
    """Two identical reads of the stored snapshot say nothing about the live
    path; letting that PASS resurrects the vacuous green in a new place."""
    snap = [_odds("A", "A", 1.0, {}, live=False)]
    assert check_repeatability(snap, snap, frozen=False).status == SKIP


def test_repeatability_still_judges_an_engaged_overlay():
    live = [_odds("A", "A", 1.0, {}, live=True, live_state="live")]
    assert check_repeatability(live, live, frozen=False).status == PASS
