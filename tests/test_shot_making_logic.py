from src.scoring.shot_making import (
    shot_family,
    bucket_key,
    build_baseline,
    expected_pps,
    compute_leaderboard,
)


def _shot(atype, dist, pts, made, aid="1", name="P", team="10", pv=None):
    return {
        "athlete_id": aid,
        "athlete_name": name,
        "team_id": team,
        "shot_type": atype,
        "distance_ft": dist,
        "points": pts,
        "made": made,
        "point_value": pv if pv is not None else (3 if pts == 3 else 2),
    }


def test_shot_family_mapping():
    assert shot_family("Driving Layup Shot") == "rim"
    assert shot_family("Layup Shot Putback") == "rim"
    assert shot_family("Driving Floating Jump Shot") == "floater"
    assert shot_family("Pullup Jump Shot") == "mid"
    assert shot_family("Step Back Jump Shot") == "mid"
    assert (
        shot_family("makes 24-foot three point jumper") == "other"
    )  # type text, not play text
    assert shot_family("") == "other"


def test_three_pointer_families_by_point_value():
    # A jump shot worth 3 buckets as a three regardless of the type phrase.
    s = _shot("Jump Shot", 24, 3, True)
    assert bucket_key(s)[1] == "three"
    # Regression (Task 2 review R1): a MISSED three (points=0) still buckets as a
    # three via the stored point_value, not as a 2pt jumper.
    miss3 = _shot("Jump Shot", 24, 0, False, pv=3)
    assert bucket_key(miss3)[1] == "three"
    assert bucket_key(miss3)[2] == 3


def test_baseline_and_expected_pps():
    # 30 identical 2pt rim shots (>= min_bucket), 18 made -> direct bucket rate 0.6 -> xPPS 1.2.
    shots = [_shot("Layup Shot", 2, 2 if i < 18 else 0, i < 18) for i in range(30)]
    base = build_baseline(shots)
    assert round(expected_pps(shots[0], base), 3) == 1.2


def test_small_bucket_falls_back():
    # A well-populated ("mid", 2) family in the 10-15ft bin (40/100 made), plus ONE
    # lone shot in a sparse 16-21ft bin. The lone shot's own bucket is under
    # min_bucket, so its expectation must borrow the FAMILY rate, NOT its own
    # degenerate 0% (a miss) rate. Family group = 100 + the lone shot = 40/101.
    fam = [_shot("Jump Shot", 12, 2 if i < 40 else 0, i < 40) for i in range(100)]
    lone = _shot("Jump Shot", 18, 0, False)
    base = build_baseline(fam + [lone], min_bucket=25)
    assert round(expected_pps(lone, base), 3) == round(
        40 / 101 * 2, 3
    )  # family fallback
    assert expected_pps(lone, base) > 0.5  # NOT the degenerate 0.0 of its 1-shot bucket


def test_leaderboard_eligibility_and_residual():
    made = [_shot("Layup Shot", 2, 2, True, aid="hot", name="Hot") for _ in range(120)]
    missed = [
        _shot("Layup Shot", 2, 0, False, aid="cold", name="Cold") for _ in range(120)
    ]
    small = [_shot("Layup Shot", 2, 2, True, aid="tiny", name="Tiny") for _ in range(5)]
    board = compute_leaderboard(made + missed + small, min_fga=100)
    ids = [r["athlete_id"] for r in board]
    assert "tiny" not in ids  # below min_fga
    assert board[0]["athlete_id"] == "hot"  # sorted by points_added desc
    assert board[0]["points_added"] > board[-1]["points_added"]
    assert round(sum(r["diet"].get("rim", 0) for r in board[:1]), 3) == 1.0
