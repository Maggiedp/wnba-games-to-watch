from src.notify.thriller import classify_excitement, compose_alert


def test_classify_boundaries():
    assert classify_excitement(3.9) is None
    assert classify_excitement(4.0) == "Close game"
    assert classify_excitement(7.4) == "Close game"
    assert classify_excitement(7.5) == "Thriller"
    assert classify_excitement(None) is None


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
