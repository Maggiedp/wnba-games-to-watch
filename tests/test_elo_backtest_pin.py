"""Pin the published Elo backtest constants to a frozen-fixture recomputation.

The Brier / pick-accuracy figures in transparency.html and METHODOLOGY.md are
hand-regenerated from scripts/validate_elo.py. This test replays the frozen
historical game set (tests/fixtures/elo_backtest_games.json.gz, written by
scripts/freeze_elo_backtest_fixture.py) with the deployed Elo defaults and
recomputes both metrics, so the suite fails if hyperparameters change without
re-running the validation and updating the published numbers.

Metrics are recomputed inline (not via scripts._calibration) on purpose —
an independent recomputation can't inherit a bug from the validation helpers.
"""

import functools
import gzip
import inspect
import json
import os
import re

import pytest

from scripts.freeze_elo_backtest_fixture import _FIXTURE_PATH
from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    DEFAULT_K,
    DEFAULT_SEASON_REGRESSION,
    expected_win_prob,
    replay_games,
)

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_TRANSPARENCY_PATH = os.path.join(
    _REPO_ROOT, "src", "api", "templates", "transparency.html"
)
_METHODOLOGY_PATH = os.path.join(_REPO_ROOT, "METHODOLOGY.md")

# The deployed MOV setting is replay_games' default; read it once so the
# metric replay and the hyperparameter pin can't diverge.
_DEPLOYED_USE_MOV = inspect.signature(replay_games).parameters["use_mov"].default

# Drift beyond these means the published constants are stale. Recomputation
# off the frozen fixture is deterministic, so the Brier tolerance only needs
# to absorb publish-rounding to 3 decimals (±0.0005) plus a little data-drift
# slack; ±0.002 still trips on the nearest historic alternative (K=28 moves
# Brier by ~0.0026). Accuracy is coarser-grained (1 game ≈ 0.0005), so ±0.005
# ≈ a 9-10 game swing.
_BRIER_TOL = 0.002
_ACC_TOL = 0.005

_STALE_HINT = (
    "Published backtest constants look stale. Re-run scripts/validate_elo.py, "
    "update transparency.html + METHODOLOGY.md, and regenerate the fixture via "
    "scripts/freeze_elo_backtest_fixture.py if the eval window changed."
)


@functools.lru_cache(maxsize=None)
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _published_transparency() -> tuple[float, float, int]:
    match = re.search(
        r"brier:\s*([0-9.]+),\s*pickAcc:\s*([0-9.]+),\s*n:\s*(\d+)",
        _read(_TRANSPARENCY_PATH),
    )
    assert match, "transparency.html no longer carries the brier/pickAcc/n constants"
    return float(match.group(1)), float(match.group(2)), int(match.group(3))


def _published_methodology() -> tuple[float, float, int]:
    match = re.search(
        r"\(2[0-9]{3}–2[0-9]{3}, ([0-9,]+) games\):\*\* Brier \*\*([0-9.]+)\*\*, "
        r"pick accuracy \*\*([0-9.]+)%\*\*",
        _read(_METHODOLOGY_PATH),
    )
    assert match, "METHODOLOGY.md no longer carries the backtest Brier/accuracy line"
    n = int(match.group(1).replace(",", ""))
    return float(match.group(2)), float(match.group(3)) / 100.0, n


def test_published_hyperparameters_match_deployed_defaults():
    """METHODOLOGY.md states K/H/regression/MOV explicitly; pin them to the code.

    The metric pins alone can't catch drift within the flat region of the loss
    surface (e.g. K=14 reproduces Brier 0.214 on the fixture), so any default
    change must also force this published line to be updated.
    """
    match = re.search(
        r"`K=([0-9.]+)`, home-court `H=\+([0-9.]+)` Elo, season-boundary "
        r"regression `([0-9.]+)`, margin-of-victory\s+multiplier (on|off)",
        _read(_METHODOLOGY_PATH),
    )
    assert match, "METHODOLOGY.md no longer carries the hyperparameters line"
    stale = (
        "METHODOLOGY.md's published hyperparameters no longer match the deployed "
        "defaults in src/scoring/elo.py. Re-run scripts/validate_elo.py and "
        "update the published line + backtest constants."
    )
    assert float(match.group(1)) == DEFAULT_K, stale
    assert float(match.group(2)) == DEFAULT_HOME_ADVANTAGE, stale
    assert float(match.group(3)) == DEFAULT_SEASON_REGRESSION, stale
    assert (match.group(4) == "on") == _DEPLOYED_USE_MOV, stale


@pytest.fixture(scope="module")
def recomputed() -> tuple[float, float, int]:
    """(brier, pick_accuracy, n_eval) replayed from the frozen fixture."""
    with gzip.open(_FIXTURE_PATH, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    eval_start = payload["eval_start"]
    replay = replay_games(
        payload["games"],
        k=DEFAULT_K,
        home_advantage=DEFAULT_HOME_ADVANTAGE,
        season_regression=DEFAULT_SEASON_REGRESSION,
        use_mov=_DEPLOYED_USE_MOV,
        presorted=True,
    )
    sq_err = 0.0
    picks = 0
    n = 0
    for h in replay.history:
        if h["date"] < eval_start:
            continue
        p_a = expected_win_prob(h["pre_a"], h["pre_b"], DEFAULT_HOME_ADVANTAGE)
        outcome = 1.0 if h["winner"] == h["team_a"] else 0.0
        sq_err += (p_a - outcome) ** 2
        picks += int((p_a > 0.5) == (outcome == 1.0))
        n += 1
    assert n > 0, "Fixture produced no evaluated games"
    return sq_err / n, picks / n, n


def test_published_documents_agree():
    t_brier, t_acc, t_n = _published_transparency()
    m_brier, m_acc, m_n = _published_methodology()
    # approx on accuracy: METHODOLOGY publishes a percentage, so /100 leaves
    # float dust (67.1 / 100 != 0.671 exactly).
    assert t_brier == m_brier and t_n == m_n and t_acc == pytest.approx(m_acc), (
        "transparency.html and METHODOLOGY.md publish different backtest numbers"
    )


def test_eval_game_count_matches_published(recomputed):
    _, _, n = recomputed
    _, _, published_n = _published_transparency()
    assert n == published_n, _STALE_HINT


def test_brier_matches_published(recomputed):
    brier, _, _ = recomputed
    published_brier, _, _ = _published_transparency()
    assert brier == pytest.approx(published_brier, abs=_BRIER_TOL), _STALE_HINT


def test_pick_accuracy_matches_published(recomputed):
    _, acc, _ = recomputed
    _, published_acc, _ = _published_transparency()
    assert acc == pytest.approx(published_acc, abs=_ACC_TOL), _STALE_HINT
