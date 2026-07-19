import pytest

from src.scoring.excitement import compute_excitement
from src.scoring.game_shape import (
    LiveShape,
    ShapeMetrics,
    compute_comeback,
    compute_game_shape,
    compute_lead_changes,
    compute_live_shape,
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


def test_compute_game_shape_aggregates(wp_plays):
    # 0.55 → 0.20 → 0.85 keeps both 0.5-crossings strictly between samples
    # (an exact-0.5 sample zeroes the sign product and hides the crossing).
    plays = wp_plays([0.55, 0.20, 0.85])
    shape = compute_game_shape(plays, home_won=True)
    assert isinstance(shape, ShapeMetrics)
    assert shape.comeback == pytest.approx(0.30)
    assert shape.excitement > 0
    assert shape.lead_changes == 2
    assert len(shape.curve) == len(plays)


def test_game_shape_none_for_clustered_sparse_feed(degenerate_wp_plays):
    # Pre-gate the DAL@LV replica stored comeback=0.5 (the winner "climbing"
    # from a 2-second 0% sliver) and topped the /replay comeback sort. Must be
    # rejected, not archived.
    assert compute_game_shape(degenerate_wp_plays, home_won=True) is None


def test_game_shape_none_when_feed_spans_too_little(wp_plays):
    # Plenty of samples, but all clustered in the final 90 seconds — the feed
    # can't represent the game's shape even though every sample is valid.
    plays = [_play(4, f"1:{30 - s:02d}", 0.5) for s in range(25)]
    assert compute_game_shape(plays, home_won=True) is None


def test_game_shape_none_when_too_few_plays():
    # Full-game span but only 3 samples: too sparse to trust the time-weighted
    # metrics or draw an honest curve.
    plays = [_play(1, "10:00", 0.50), _play(2, "5:00", 0.20), _play(4, "0:00", 0.80)]
    assert compute_game_shape(plays, home_won=True) is None


def test_live_shape_has_no_coverage_gate(wp_plays):
    # A live game is legitimately partial — compute_live_shape keeps the bare
    # <2-plays contract so early-game strips still render.
    plays = [_play(1, "10:00", 0.50), _play(1, "8:00", 0.55), _play(1, "5:00", 0.60)]
    assert compute_live_shape(plays) is not None


def _live_plays():
    return [
        _play(1, "10:00", 0.50),
        _play(2, "5:00", 0.70),
        _play(3, "5:00", 0.35),
        _play(4, "2:00", 0.60),
        _play(4, "0:00", 0.55),
    ]


def test_compute_live_shape_bundles_winner_independent_metrics():
    plays = _live_plays()
    shape = compute_live_shape(plays)
    assert isinstance(shape, LiveShape)
    assert shape.tension == compute_tension(plays)
    assert shape.excitement == compute_excitement(plays, final=False)
    assert shape.lead_changes == compute_lead_changes(plays)
    assert shape.curve == downsample_curve(plays)


def test_compute_live_shape_uses_live_excitement_with_future_term():
    # Both samples sit at a coin flip: the live future term (final=False) makes
    # the live score strictly greater than the final-only score (final=True).
    plays = [_play(1, "10:00", 0.50), _play(4, "0:00", 0.50)]
    shape = compute_live_shape(plays)
    assert shape.excitement == pytest.approx(compute_excitement(plays, final=False))
    assert compute_excitement(plays, final=False) > compute_excitement(
        plays, final=True
    )


def test_compute_live_shape_none_when_insufficient_plays():
    assert compute_live_shape([]) is None
    assert compute_live_shape([_play(1, "10:00", 0.5)]) is None


def test_compute_live_shape_omits_winner_dependent_fields():
    shape = compute_live_shape(_live_plays())
    assert not hasattr(shape, "comeback")
    assert not hasattr(shape, "winner_low_wp")
    assert not hasattr(shape, "winner")
