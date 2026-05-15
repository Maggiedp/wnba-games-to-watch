"""Tests for the Python port of the JS excitement-index formula."""

import pytest

from src.scoring.excitement import (
    EXCITEMENT_CLOSE,
    EXCITEMENT_FUTURE_WEIGHT,
    EXCITEMENT_THRILLER,
    compute_excitement,
    elapsed_seconds,
)


def test_constants_match_js():
    """Python constants must match the JS values in src/api/routes.py.

    If this test fails, update one to match the other — drift causes the
    on-page and stored excitement values to diverge.
    """
    import re
    from pathlib import Path

    src = Path("src/api/routes.py").read_text()
    js_close = float(re.search(r"EXCITEMENT_CLOSE\s*=\s*([\d.]+)", src).group(1))
    js_thriller = float(re.search(r"EXCITEMENT_THRILLER\s*=\s*([\d.]+)", src).group(1))
    js_future = float(
        re.search(r"EXCITEMENT_FUTURE_WEIGHT\s*=\s*([\d.]+)", src).group(1)
    )
    assert EXCITEMENT_CLOSE == js_close
    assert EXCITEMENT_THRILLER == js_thriller
    assert EXCITEMENT_FUTURE_WEIGHT == js_future


def test_elapsed_seconds_colon_format():
    """Standard 'M:SS' clock format."""
    # Period 1, 5:30 remaining → 600 - 330 = 270 elapsed in period, 0 prior.
    assert elapsed_seconds({"period": 1, "clock": "5:30"}) == 270
    # Period 2, 0:00 remaining → full 1200 (2 quarters).
    assert elapsed_seconds({"period": 2, "clock": "0:00"}) == 1200


def test_elapsed_seconds_decimal_format():
    """Under-a-minute decimal seconds format from ESPN."""
    # Period 4, 48.7 seconds remaining → 600 - 48.7 = 551.3 in period, plus 1800 prior = 2351.3
    assert elapsed_seconds({"period": 4, "clock": "48.7"}) == pytest.approx(2351.3)


def test_elapsed_seconds_overtime():
    """OT periods are 5 minutes (300s), and prior periods sum to 2400."""
    # First OT, 5:00 remaining = 0 elapsed in OT → 2400 prior.
    assert elapsed_seconds({"period": 5, "clock": "5:00"}) == 2400
    # First OT, end → 2400 + 300 = 2700.
    assert elapsed_seconds({"period": 5, "clock": "0:00"}) == 2700


def test_compute_excitement_blowout_is_low():
    """A blowout (no WP movement, ends at 100% home) scores low."""
    plays = [
        {"period": 1, "clock": "10:00", "home_pct": 0.5},
        {"period": 2, "clock": "0:00", "home_pct": 0.95},
        {"period": 4, "clock": "0:00", "home_pct": 1.0},
    ]
    score = compute_excitement(plays)
    # Play 1: period 2 clock 0:00 → elapsed = 600+600 = 1200s
    # Play 2: period 4 clock 0:00 → elapsed = 1800+600 = 2400s
    # Past: |0.95-0.5|·(1200/2400) + |1.0-0.95|·(2400/2400) = 0.225 + 0.05 = 0.275
    # Future: 2·1.0·0·1.0 = 0
    assert score == pytest.approx(0.275)


def test_compute_excitement_thriller_late_swing():
    """Big late WP swing in a final play registers strongly."""
    plays = [
        {"period": 1, "clock": "10:00", "home_pct": 0.5},
        {"period": 4, "clock": "0:30", "home_pct": 0.7},
        {"period": 4, "clock": "0:00", "home_pct": 0.05},  # last-second steal/score
    ]
    score = compute_excitement(plays)
    # past: |0.7-0.5|·(2370/2400) + |0.05-0.7|·(2400/2400)
    #     = 0.2·0.9875 + 0.65 = 0.1975 + 0.65 = 0.8475
    # future: 2·0.05·0.95·1.0 = 0.095; ·2.5 = 0.2375
    # total ≈ 1.085 — small total because the chain is tiny; the unit test only
    # asserts directional correctness vs. the blowout case.
    assert score > 1.0


def test_compute_excitement_empty_returns_zero():
    """Fewer than 2 plays → 0.0 (don't crash, store a deterministic value)."""
    assert compute_excitement([]) == 0.0
    assert compute_excitement([{"period": 1, "clock": "10:00", "home_pct": 0.5}]) == 0.0


def test_compute_excitement_finished_game_future_collapses():
    """When final p is 0 or 1, future term is 0 regardless of weight."""
    plays_blowout = [
        {"period": 1, "clock": "10:00", "home_pct": 0.5},
        {"period": 4, "clock": "0:00", "home_pct": 1.0},
    ]
    plays_close_final = [
        {"period": 1, "clock": "10:00", "home_pct": 0.5},
        {"period": 4, "clock": "0:00", "home_pct": 0.5},
    ]
    # The close-final case has future = 2·0.5·0.5·1.0 = 0.5; total includes γ·0.5 = 1.25.
    # The blowout case has future = 0; total is just past = 0.25.
    assert compute_excitement(plays_close_final) > compute_excitement(plays_blowout)
