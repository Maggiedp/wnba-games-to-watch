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
    check_postseason_importance,
    played_postseason_pairs,
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


# --- check_postseason_importance -----------------------------------------
#
# Values measured 2026-09-19 by driving the real scoring path over a completed
# 2026 season (see _POSTSEASON_FLOOR's comment). These are the numbers the
# postseason is expected to produce; the band exists to bracket them, so the
# band must not be retuned without re-measuring.


_MIN, _DAL = "Minnesota Lynx", "Dallas Wings"


def _post(v, team_a=_MIN, team_b=_DAL):
    """An /api/games/upcoming row. Carries real team names so the opener
    check keys on a realistic pair rather than a degenerate empty one."""
    return {
        "team_a": team_a,
        "team_b": team_b,
        "team_a_abbr": team_a[:3].upper(),
        "team_b_abbr": team_b[:3].upper(),
        "importance_score": v,
    }


@pytest.mark.parametrize(
    "value,label",
    [
        (40.37, "QF G1 low"),
        (51.36, "QF G1 high"),
        (25.17, "QF G2 at 1-0, low"),
        (98.80, "QF G3 at 1-1"),
        (32.82, "SF G1 low"),
        (98.92, "SF G5 at 2-2"),
        (27.37, "F G1 low"),
        (99.20, "F G7 winner-take-all"),
    ],
)
def test_measured_postseason_values_pass(value, label):
    """Every value the real path was measured to produce must read as healthy.

    The decisive games (QF G3, SF G5, F G7) are the ones that matter here: a
    win-or-go-home game is structurally pinned near POSTSEASON_MAX_SWING, so it
    scores ~99, not the ~45 an opener scores. The original (25, 85) band failed
    all three against correct behavior.

    F G7's 99.20 also covers the fallback sentinel from below: the gap between
    a real winner-take-all game and 100.0 is the Monte Carlo noise floor, so
    the sentinel must not be a threshold parked in that gap.
    """
    assert check_postseason_importance([_post(value)]).status == PASS, label


def test_the_slot_match_fallback_is_still_caught():
    """The documented likely failure mode: a game that couldn't be matched to a
    bracket slot is scored a flat 100.0."""
    result = check_postseason_importance([_post(100.0)])
    assert result.status == FAIL
    assert "fallback" in result.detail


def test_a_value_just_under_the_fallback_is_not_read_as_the_fallback():
    """The input that separates an exact sentinel from a `>= 99.5` threshold.

    99.20 (the measured Game 7) passes under both rules, so it cannot pin this
    behavior on its own. 99.6 can: it is what the same winner-take-all game
    scores once the noise floor shrinks — roughly a 40k-sim run — and the old
    threshold would have called it a slot-matching failure.
    """
    assert check_postseason_importance([_post(99.6)]).status == PASS


def test_a_regular_season_sized_score_still_fails():
    """A postseason game scored down the regular-season path — the failure the
    floor exists to catch."""
    assert check_postseason_importance([_post(12.0)]).status == FAIL


def test_played_postseason_pairs_ignores_scheduled_and_regular_season_rows():
    """Only COMPLETED postseason games make a matchup "already played".

    A scheduled later game in the same series must not mark the series as
    under way — that would silently drop the opener ceiling on the very game
    it is meant to judge.
    """
    history = [
        {"team_a": "A", "team_b": "B", "season_type": 3, "winner_team": "A"},
        {"team_a": "C", "team_b": "D", "season_type": 3, "winner_team": None},
        {"team_a": "E", "team_b": "F", "season_type": 2, "winner_team": "E"},
    ]
    assert played_postseason_pairs(history) == {frozenset({"A", "B"})}


def test_a_series_opener_in_the_nineties_fails():
    """The gap adversarial review found: with only a floor and an exact
    sentinel, an over-inflated opener reads as healthy.

    Game 1 of a series is never win-or-go-home -- true of Bo3, Bo5 and Bo7
    alike -- so an opener has a real ceiling even though a later game does
    not. Measured openers top out at 51.36; 95.0 is the inflated value this
    must catch.
    """
    result = check_postseason_importance([_post(95.0)], played_pairs=set())
    assert result.status == FAIL
    assert "opener" in result.detail


def test_a_decisive_later_game_in_the_nineties_still_passes():
    """The counterpart the opener ceiling must not break: once the two teams
    have already played, the series can be at win-or-go-home and ~99 is
    correct. Guards against reintroducing the 85-ceiling bug behind a new
    name."""
    pair = {frozenset({_MIN, _DAL})}
    assert check_postseason_importance([_post(98.80)], played_pairs=pair).status == PASS


@pytest.mark.parametrize("value", [40.37, 51.36, 32.82, 37.09, 27.37, 30.93])
def test_measured_openers_pass_against_the_opener_ceiling(value):
    """Every opener value actually measured must clear the ceiling."""
    assert check_postseason_importance([_post(value)], played_pairs=set()).status == PASS


def test_unknown_series_history_does_not_manufacture_a_failure():
    """If the postseason history fetch fails, the opener ceiling cannot be
    applied. It must go unapplied rather than failing every decisive game --
    the false-positive direction this branch exists to remove."""
    result = check_postseason_importance([_post(98.80)], played_pairs=None)
    assert result.status == PASS
    assert "opener ceiling not applied" in result.detail


def test_the_probe_sentinel_matches_the_value_production_actually_emits():
    """Pin the cross-module coupling the exact comparison depends on.

    `_POSTSEASON_FALLBACK` is only meaningful because `_importance_for_game`
    returns that exact literal when a postseason game cannot be matched to a
    bracket slot. Nothing else ties the two together, so changing the
    production fallback would silently blind the probe. Assert the real
    function, rather than trusting a number copied between modules.
    """
    from scripts.daily_update import _importance_for_game
    from scripts.verify_game_night import _POSTSEASON_FALLBACK

    unmatchable = {"team_a": "A", "team_b": "B", "season_type": 3, "event_id": "x"}
    fallback = _importance_for_game(unmatchable, [], {}, 1.0, bracket_state=None)
    assert fallback == _POSTSEASON_FALLBACK
    assert check_postseason_importance([_post(fallback)]).status == FAIL


def test_no_postseason_score_skips_rather_than_passes():
    assert check_postseason_importance([]).status == SKIP
    assert (
        check_postseason_importance(
            [{"team_a_abbr": "A", "team_b_abbr": "B", "importance_score": None}]
        ).status
        == SKIP
    )
