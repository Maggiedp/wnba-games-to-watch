import pytest

from src.scoring.game_shape import (
    ShapeMetrics,
    compute_comeback,
    compute_game_shape,
    compute_lead_changes,
    compute_tension,
    downsample_curve,
    winner_low_wp,
)


def _play(period, clock, home_pct):
    return {"period": period, "clock": clock, "home_pct": home_pct}


def test_comeback_zero_when_winner_led_throughout():
    plays = [_play(1, "10:00", 0.60), _play(4, "0:00", 0.95)]
    assert compute_comeback(plays, home_won=True) == pytest.approx(0.0)


def test_comeback_measures_home_winner_hole():
    plays = [_play(1, "10:00", 0.50), _play(2, "5:00", 0.20), _play(4, "0:00", 0.80)]
    assert compute_comeback(plays, home_won=True) == pytest.approx(0.30)
    assert winner_low_wp(plays, home_won=True) == pytest.approx(0.20)


def test_comeback_uses_away_winner_series():
    # away won; away's low = 1 - max(home_pct) = 1 - 0.70
    plays = [_play(1, "10:00", 0.50), _play(2, "5:00", 0.70), _play(4, "0:00", 0.20)]
    assert compute_comeback(plays, home_won=False) == pytest.approx(0.20)


def test_comeback_uses_actual_winner_not_terminal_wp():
    # ESPN marks the game FINAL but the WP feed still ends below 0.5 for home,
    # while home actually won (a late buzzer-beater the feed hadn't credited).
    plays = [_play(1, "10:00", 0.50), _play(2, "5:00", 0.20), _play(4, "0:00", 0.45)]
    # Actual winner = home → low is home's nadir (0.20), comeback 0.30.
    assert winner_low_wp(plays, home_won=True) == pytest.approx(0.20)
    assert compute_comeback(plays, home_won=True) == pytest.approx(0.30)
    # Inferring "away" from the terminal sample (0.45 < 0.5) would instead give
    # low = 1 - max(home_pct) = 0.50 → comeback 0.0; the actual-winner path avoids that.
    assert compute_comeback(plays, home_won=False) == pytest.approx(0.0)


def test_lead_changes_counts_midline_crossings():
    plays = [
        _play(1, "10:00", 0.40),
        _play(2, "8:00", 0.60),
        _play(3, "5:00", 0.45),
        _play(4, "0:00", 0.55),
    ]
    assert compute_lead_changes(plays) == 3


def test_lead_changes_zero_when_one_sided():
    plays = [_play(1, "10:00", 0.60), _play(2, "5:00", 0.70), _play(4, "0:00", 0.80)]
    assert compute_lead_changes(plays) == 0


def test_tension_one_for_wire_to_wire_coinflip():
    plays = [
        _play(1, "10:00", 0.50),
        _play(2, "10:00", 0.50),
        _play(3, "10:00", 0.50),
        _play(4, "0:00", 0.50),
    ]
    assert compute_tension(plays) == pytest.approx(1.0)


def test_tension_low_for_blowout():
    plays = [
        _play(1, "10:00", 0.95),
        _play(2, "10:00", 0.95),
        _play(3, "10:00", 0.95),
        _play(4, "0:00", 0.95),
    ]
    assert compute_tension(plays) == pytest.approx(4 * 0.95 * 0.05)


def test_metrics_none_on_short_feed():
    assert compute_tension([]) is None
    assert compute_tension([_play(1, "10:00", 0.5)]) is None
    assert compute_comeback([], home_won=True) is None
    assert compute_lead_changes([]) is None
    assert compute_game_shape([], home_won=True) is None


def test_downsample_preserves_endpoints_and_caps_count():
    plays = [_play(1, "10:00", i / 300) for i in range(300)]
    curve = downsample_curve(plays, points=100)
    assert len(curve) <= 100
    assert curve[0][1] == pytest.approx(0.0)
    assert curve[-1][1] == pytest.approx(299 / 300)


def test_compute_game_shape_aggregates():
    plays = [_play(1, "10:00", 0.50), _play(2, "5:00", 0.20), _play(4, "0:00", 0.80)]
    shape = compute_game_shape(plays, home_won=True)
    assert isinstance(shape, ShapeMetrics)
    assert shape.comeback == pytest.approx(0.30)
    assert shape.excitement > 0
    assert shape.lead_changes == 1
    assert len(shape.curve) == 3
