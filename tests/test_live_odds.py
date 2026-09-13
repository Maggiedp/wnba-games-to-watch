"""Determinism primitives for the live playoff-odds overlay."""

from pathlib import Path

import pytest

from src.constants import CURRENT_SEASON


def test_quantize_rounds_to_the_nearest_percentage_point():
    from src.scoring.live_odds import quantize_win_prob

    assert quantize_win_prob(0.612) == 0.61
    assert quantize_win_prob(0.614) == 0.61
    assert quantize_win_prob(0.6151) == 0.62
    assert quantize_win_prob(0.0) == 0.0
    assert quantize_win_prob(1.0) == 1.0


def test_seed_does_not_depend_on_python_hash_randomization():
    """Must not use Python's builtin hash(): it is salted per process
    (PYTHONHASHSEED), so the published number would change on every Cloud Run
    container restart. Two interpreters with DIFFERENT hash seeds must agree."""
    import os
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    code = (
        "from src.scoring.live_odds import live_sim_seed;"
        "print(live_sim_seed({'A': {'wins': 1, 'losses': 2, 'elo': 1500.0}},"
        "[('A', 'B')], {0: 0.61}))"
    )

    def seed_with(hashseed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": hashseed}
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_root,
            env=env,
        ).stdout.strip()

    assert seed_with("0") == seed_with("1")


def test_seed_changes_with_overrides_and_with_standings():
    from src.scoring.live_odds import live_sim_seed

    standings = {"A": {"wins": 1, "losses": 2, "elo": 1500.0}}
    games = [("A", "B")]

    base = live_sim_seed(standings, games, {0: 0.61})
    assert live_sim_seed(standings, games, {0: 0.62}) != base
    assert live_sim_seed(standings, games, {}) != base
    moved = {"A": {"wins": 2, "losses": 2, "elo": 1500.0}}
    assert live_sim_seed(moved, games, {0: 0.61}) != base
    assert live_sim_seed(standings, [("A", "C")], {0: 0.61}) != base


def test_seed_ignores_dict_ordering():
    from src.scoring.live_odds import live_sim_seed

    games = [("A", "B")]
    one = {
        "A": {"wins": 1, "losses": 0, "elo": 1500.0},
        "B": {"wins": 0, "losses": 1, "elo": 1400.0},
    }
    two = {
        "B": {"wins": 0, "losses": 1, "elo": 1400.0},
        "A": {"wins": 1, "losses": 0, "elo": 1500.0},
    }
    assert live_sim_seed(one, games, {0: 0.5}) == live_sim_seed(two, games, {0: 0.5})


def _game(event_id, status, winner=None, season_type=2):
    return {
        "event_id": event_id,
        "team_a": "Home Team",
        "team_b": "Away Team",
        "winner_team": winner,
        "status": status,
        "season_type": season_type,
    }


def test_final_game_absent_from_the_db_becomes_a_certainty_override():
    """The trap this closes: conditioning on the 9:30pm game while still
    treating the 7pm game as unplayed publishes a number strictly WORSE than
    the morning snapshot it replaced."""
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        today_games=[_game("1", "STATUS_FINAL", winner="Home Team")],
        live_win_probs={},
        remaining_index_by_espn_id={"1": 0},
        remaining_games=[("Home Team", "Away Team")],
    )

    assert result.overrides == {0: 1.0}
    assert result.settled_espn_ids == ["1"]
    assert result.live_espn_ids == []


def test_away_winner_becomes_a_zero_override():
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_FINAL", winner="Away Team")],
        {},
        {"1": 0},
        [("Home Team", "Away Team")],
    )
    assert result.overrides == {0: 0.0}


def test_live_game_uses_quantized_home_pct():
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_IN_PROGRESS")],
        {"1": 0.6137},
        {"1": 0},
        [("Home Team", "Away Team")],
    )
    assert result.overrides == {0: 0.61}
    assert result.live_espn_ids == ["1"]


def test_unstarted_game_gets_no_override():
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_SCHEDULED")],
        {},
        {"1": 0},
        [("Home Team", "Away Team")],
    )
    assert result.overrides == {}


def test_game_already_recorded_in_the_db_contributes_nothing():
    """A game the daily run has ingested is no longer in remaining_games, so it
    has no index and must not produce an override."""
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_FINAL", winner="Home Team")],
        {},
        remaining_index_by_espn_id={},
        remaining_games=[],
    )
    assert result.overrides == {}
    assert result.settled_espn_ids == []


def test_postseason_game_on_the_slate_is_flagged():
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_IN_PROGRESS", season_type=3)],
        {"1": 0.5},
        {"1": 0},
        [("Home Team", "Away Team")],
    )
    assert result.has_postseason is True


def test_live_game_with_a_failed_wp_fetch_falls_back_to_elo():
    """_detect_live_shapes drops games whose WP fetch failed. That game keeps
    its Elo probability rather than being dropped from the sim."""
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_IN_PROGRESS")],
        live_win_probs={},
        remaining_index_by_espn_id={"1": 0},
        remaining_games=[("Home Team", "Away Team")],
    )
    assert result.overrides == {}
    assert result.live_espn_ids == []


def test_final_with_no_winner_is_skipped():
    """A tie or an unparsed final must not silently become a home win."""
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_FINAL", winner=None)],
        {},
        {"1": 0},
        [("Home Team", "Away Team")],
    )
    assert result.overrides == {}


def test_final_winner_name_matches_neither_participant():
    """Fail closed when the ESPN winner name matches neither participant.
    Canonicalization drift (a rename, a stale alias row) could cause the name
    to mismatch. Recording such a game would silently invert its contribution."""
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_FINAL", winner="Unknown Team")],
        {},
        {"1": 0},
        [("Home Team", "Away Team")],
    )
    assert result.overrides == {}
    assert result.settled_espn_ids == []


def test_postseason_game_with_no_index_is_flagged():
    """Postseason games not in remaining_index_by_espn_id must still set
    has_postseason=True, so the caller can disable live mode entirely."""
    from src.scoring.live_odds import build_live_overrides

    result = build_live_overrides(
        [_game("1", "STATUS_IN_PROGRESS", season_type=3)],
        {"1": 0.5},
        remaining_index_by_espn_id={},
        remaining_games=[],
    )
    assert result.has_postseason is True
    assert result.overrides == {}


# --- settled_record_deltas (keep the Rec column honest during the overlay) ---


def test_settled_record_deltas_credits_the_home_winner():
    from src.scoring.live_odds import LiveOverrides, settled_record_deltas

    live = LiveOverrides(overrides={0: 1.0}, settled_espn_ids=["1"])
    deltas = settled_record_deltas(live, [("Home Team", "Away Team")], {"1": 0})

    assert deltas == {"Home Team": [1, 0], "Away Team": [0, 1]}


def test_settled_record_deltas_credits_the_away_winner():
    from src.scoring.live_odds import LiveOverrides, settled_record_deltas

    live = LiveOverrides(overrides={0: 0.0}, settled_espn_ids=["1"])
    deltas = settled_record_deltas(live, [("Home Team", "Away Team")], {"1": 0})

    assert deltas == {"Home Team": [0, 1], "Away Team": [1, 0]}


def test_settled_record_deltas_ignores_in_progress_games():
    """A live game's override is a probability, not a result — it must not move
    anyone's record, or the table would credit a win that hasn't happened."""
    from src.scoring.live_odds import LiveOverrides, settled_record_deltas

    live = LiveOverrides(overrides={0: 0.97}, live_espn_ids=["1"])
    deltas = settled_record_deltas(live, [("Home Team", "Away Team")], {"1": 0})

    assert deltas == {}


def test_settled_record_deltas_accumulates_across_a_slate():
    from src.scoring.live_odds import LiveOverrides, settled_record_deltas

    live = LiveOverrides(overrides={0: 1.0, 1: 0.0}, settled_espn_ids=["1", "2"])
    games = [("Aces", "Liberty"), ("Liberty", "Sky")]
    deltas = settled_record_deltas(live, games, {"1": 0, "2": 1})

    # Game 0 override 1.0 -> home (Aces) won, so Liberty lost on the road.
    # Game 1 override 0.0 -> away (Sky) won, so Liberty lost again at home.
    assert deltas == {"Aces": [1, 0], "Liberty": [0, 2], "Sky": [1, 0]}


# --- settled_elo_updates (a decided game is a fact; replay it like the daily run) ---


def _elo_game(event_id, winner, sa, sb, season_type=2):
    return {
        "event_id": event_id,
        "team_a": "Home Team",
        "team_b": "Away Team",
        "winner_team": winner,
        "final_score_a": sa,
        "final_score_b": sb,
        "status": "STATUS_FINAL",
        "season_type": season_type,
    }


def test_settled_elo_update_matches_the_daily_replay_exactly():
    """The whole point is agreeing with the 6 AM number. Parameters must mirror
    replay_games: k=DEFAULT_K, home_advantage=0.0 (its default, which
    daily_update's bare replay_games(completed) call uses), MOV on."""
    from src.scoring.elo import update_ratings
    from src.scoring.live_odds import build_live_overrides, settled_elo_updates

    games = [("Home Team", "Away Team")]
    live = build_live_overrides(
        [_elo_game("1", "Home Team", 90, 78)], {}, {"1": 0}, games
    )
    updated = settled_elo_updates(live, games, {"Home Team": 1500.0, "Away Team": 1600.0})

    expected_home, expected_away = update_ratings(
        1500.0, 1600.0, team_a_won=True, home_advantage=0.0, mov=12
    )
    assert updated["Home Team"] == expected_home
    assert updated["Away Team"] == expected_away
    assert updated["Home Team"] > 1500.0  # the upset winner gains


def test_settled_elo_update_is_zero_sum():
    from src.scoring.live_odds import build_live_overrides, settled_elo_updates

    games = [("Home Team", "Away Team")]
    live = build_live_overrides(
        [_elo_game("1", "Away Team", 80, 95)], {}, {"1": 0}, games
    )
    before = {"Home Team": 1500.0, "Away Team": 1500.0}
    after = settled_elo_updates(live, games, before)

    moved = after["Home Team"] - before["Home Team"]
    assert moved < 0  # the home side lost
    assert after["Away Team"] - before["Away Team"] == pytest.approx(-moved)


def test_an_in_progress_game_never_moves_elo():
    """A live game has no result. Only decided games are facts."""
    from src.scoring.live_odds import build_live_overrides, settled_elo_updates

    games = [("Home Team", "Away Team")]
    live = build_live_overrides(
        [{**_elo_game("1", None, None, None), "status": "STATUS_IN_PROGRESS"}],
        {"1": 0.97},
        {"1": 0},
        games,
    )
    before = {"Home Team": 1500.0, "Away Team": 1600.0}
    assert settled_elo_updates(live, games, before) == before


def test_a_final_with_no_usable_scores_still_updates_at_multiplier_one():
    """MOV unknown must not skip the update — replay_games does the same."""
    from src.scoring.live_odds import build_live_overrides, settled_elo_updates

    games = [("Home Team", "Away Team")]
    live = build_live_overrides(
        [_elo_game("1", "Home Team", None, None)], {}, {"1": 0}, games
    )
    updated = settled_elo_updates(live, games, {"Home Team": 1500.0, "Away Team": 1600.0})

    assert updated["Home Team"] > 1500.0


def test_two_settled_games_compound_in_slate_order():
    from src.scoring.live_odds import build_live_overrides, settled_elo_updates

    games = [("Home Team", "Away Team"), ("Home Team", "Third Team")]
    slate = [
        _elo_game("1", "Home Team", 90, 80),
        {**_elo_game("2", "Home Team", 88, 70), "team_b": "Third Team"},
    ]
    live = build_live_overrides(slate, {}, {"1": 0, "2": 1}, games)
    updated = settled_elo_updates(
        live, games, {"Home Team": 1500.0, "Away Team": 1600.0, "Third Team": 1500.0}
    )

    # Two wins compound: strictly more than either alone.
    one_only = build_live_overrides([slate[0]], {}, {"1": 0}, games)
    after_one = settled_elo_updates(
        one_only, games, {"Home Team": 1500.0, "Away Team": 1600.0, "Third Team": 1500.0}
    )
    assert updated["Home Team"] > after_one["Home Team"] > 1500.0


def test_settled_elo_reproduces_the_daily_replay_bit_for_bit():
    """The parity that matters: the overlay and tomorrow's 6 AM snapshot must
    agree on a decided game, not merely be close. Pins the parameters against
    the real replay_games, so a future home_advantage/k drift fails here rather
    than showing up as a silent live-vs-daily disagreement in production."""
    from src.scoring.elo import replay_games
    from src.scoring.live_odds import build_live_overrides, settled_elo_updates

    games = [("Home Team", "Away Team")]
    slate = [_elo_game("1", "Home Team", 90, 78)]
    start = {"Home Team": 1500.0, "Away Team": 1600.0}

    live = build_live_overrides(slate, {}, {"1": 0}, games)
    live_elo = settled_elo_updates(live, games, start)

    daily = replay_games(
        [
            {
                "team_a": "Home Team",
                "team_b": "Away Team",
                "winner_team": "Home Team",
                "final_score_a": 90,
                "final_score_b": 78,
                "date": f"{CURRENT_SEASON}-09-20",
                "event_id": "1",
            }
        ],
        initial_ratings=dict(start),
    ).final_ratings

    assert live_elo == daily
