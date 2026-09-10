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
