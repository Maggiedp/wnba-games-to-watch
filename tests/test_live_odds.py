"""Determinism primitives for the live playoff-odds overlay."""

from pathlib import Path


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
