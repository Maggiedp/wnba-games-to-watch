from src.notify.thriller import (
    classify_excitement,
    compose_alert,
    live_excitement_label,
)


def test_classify_boundaries():
    assert classify_excitement(3.9) is None
    assert classify_excitement(4.0) == "Close game"
    assert classify_excitement(7.4) == "Close game"
    assert classify_excitement(7.5) == "Thriller"
    assert classify_excitement(None) is None


def test_live_label_suppresses_when_currently_lopsided():
    """A game that banked Thriller-level excitement but is now decided
    (WP outside [0.15, 0.85]) must NOT alert — the label reflects the
    CURRENT state, not the cumulative history."""
    assert classify_excitement(8.0) == "Thriller"  # cumulative label would fire
    assert live_excitement_label(8.0, 0.95) is None  # but it's a blowout now
    assert live_excitement_label(8.0, 0.05) is None  # (either direction)


def test_live_label_keeps_when_currently_close():
    """Still genuinely close right now → keep the label."""
    assert live_excitement_label(8.0, 0.5) == "Thriller"
    assert live_excitement_label(5.0, 0.5) == "Close game"


def test_live_label_below_close_is_none_regardless_of_wp():
    """Below the Close threshold → None whatever the current WP (unchanged)."""
    assert live_excitement_label(3.0, 0.5) is None
    assert live_excitement_label(3.0, 0.95) is None


def test_live_label_keeps_when_wp_unknown():
    """A missing / non-finite current WP must NOT suppress — we never silently
    kill a real alert on a data hiccup; the gate fires only on a confirmed
    finite lopsided WP (matching the JS mirror, which lets NaN through too)."""
    assert live_excitement_label(8.0, None) == "Thriller"
    assert live_excitement_label(8.0, float("nan")) == "Thriller"


def test_live_label_band_boundaries_inclusive():
    """The band edges [0.15, 0.85] are inclusive (not lopsided)."""
    assert live_excitement_label(5.0, 0.85) == "Close game"
    assert live_excitement_label(5.0, 0.15) == "Close game"
    assert live_excitement_label(5.0, 0.86) is None
    assert live_excitement_label(5.0, 0.14) is None


def _game(**over):
    base = {
        "espn_id": "401700009",
        "home_team": "Los Angeles Sparks",
        "away_team": "Las Vegas Aces",
        "home_score": "74",
        "away_score": "78",
        "excitement": 8.1,
    }
    base.update(over)
    return base


def test_compose_thriller_has_prefix_score_excitement_and_link():
    msg = compose_alert(_game(), "Thriller")
    assert msg.startswith("🔥 Thriller")
    assert "Las Vegas Aces @ Los Angeles Sparks" in msg
    assert "78–74" in msg  # away–home
    assert "excitement 8.1" in msg
    assert "https://wumbers.com/game/401700009" in msg


def test_compose_close_prefix():
    assert compose_alert(_game(excitement=5.0), "Close game").startswith(
        "👀 Close game"
    )


def test_compose_omits_blank_score():
    msg = compose_alert(_game(home_score="", away_score=""), "Thriller")
    assert "–" not in msg  # no en-dash score segment
