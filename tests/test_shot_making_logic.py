from src.scoring.shot_making import (
    shot_family,
    bucket_key,
    build_baseline,
    expected_pps,
    compute_leaderboard,
    compute_player_shot_chart,
)


def _shot(
    atype, dist, pts, made, aid="1", name="P", team="10", pv=None, team_abbr="LV"
):
    return {
        "athlete_id": aid,
        "athlete_name": name,
        "team_id": team,
        "team_abbr": team_abbr,
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
    assert all(r["team_abbr"] == "LV" for r in board)


def test_traded_player_team_is_deterministic_plurality():
    # A player traded mid-season: 70 shots for LV, 50 for the old club (SEA).
    # The row must show the PLURALITY team (LV) regardless of shot order, not
    # last-write-wins over an unordered query (Codex R2).
    lv = [
        _shot("Layup Shot", 2, 2, True, aid="p", name="P", team="17", team_abbr="LV")
        for _ in range(70)
    ]
    sea = [
        _shot("Layup Shot", 2, 2, True, aid="p", name="P", team="14", team_abbr="SEA")
        for _ in range(50)
    ]
    for order in (lv + sea, sea + lv, sea[:25] + lv + sea[25:]):
        board = compute_leaderboard(order, min_fga=100)
        assert len(board) == 1
        assert board[0]["team_id"] == "17"  # plurality team, stable across orders
        assert board[0]["team_abbr"] == "LV"
        assert board[0]["fga"] == 120  # both teams' shots still counted


def _shot_chart(aid="p1", made=True, pv=2, dist=3.0, x=25, y=4, stype="Layup Shot"):
    return {
        "athlete_id": aid,
        "athlete_name": "P One",
        "team_id": "t1",
        "team_abbr": "AAA",
        "shot_type": stype,
        "distance_ft": dist,
        "coord_x": x,
        "coord_y": y,
        "points": pv if made else 0,
        "point_value": pv,
        "made": made,
    }


def _league(n=60):
    # enough shots per bucket so build_baseline doesn't fall back to _pv only
    shots = []
    for i in range(n):
        shots.append(
            _shot_chart(
                aid=f"g{i}", made=(i % 2 == 0), pv=2, dist=3.0, stype="Layup Shot"
            )
        )
        shots.append(
            _shot_chart(
                aid=f"g{i}",
                made=(i % 3 == 0),
                pv=3,
                dist=24.0,
                x=2,
                y=2,
                stype="Jump Shot",
            )
        )
    return shots


def test_chart_dots_skip_null_coords_and_clamp_negative_y():
    baseline = build_baseline(_league())
    player = [
        _shot_chart(made=True, x=25, y=4),
        _shot_chart(
            made=False, x=None, y=None
        ),  # null coords -> no dot, still in zones
        _shot_chart(
            made=True, x=3, y=-2, pv=3, dist=23.0, stype="Jump Shot"
        ),  # y clamped to 0
    ]
    out = compute_player_shot_chart(player, baseline)
    assert len(out["shots"]) == 2  # null-coord dot dropped
    assert out["fga"] == 3  # zones/fga count all 3
    ys = sorted(s["y"] for s in out["shots"])
    assert ys[0] == 0  # -2 clamped to 0


def test_chart_zone_added_sums_to_leaderboard_points_added():
    league = _league()
    player = [
        _shot_chart(
            aid="star", made=True, pv=3, dist=24.0, x=2, y=2, stype="Jump Shot"
        ),
        _shot_chart(
            aid="star", made=True, pv=3, dist=24.0, x=48, y=2, stype="Jump Shot"
        ),
        _shot_chart(
            aid="star", made=False, pv=2, dist=3.0, x=25, y=4, stype="Layup Shot"
        ),
    ]
    all_shots = league + player
    baseline = build_baseline(all_shots)
    row = next(
        r
        for r in compute_leaderboard(all_shots, min_fga=1)
        if r["athlete_id"] == "star"
    )
    chart = compute_player_shot_chart(player, baseline)
    zone_sum = round(sum(z["added"] for z in chart["zones"]), 2)
    # Accepted cent-level rounding drift: each zone's `added` is rounded to 2
    # decimals independently, so their sum can differ from the separately-rounded
    # total by +-0.01. The panel shows no reconciling total, so the gap is cosmetic.
    assert abs(zone_sum - row["points_added"]) <= 0.01
    assert chart["points_added"] == row["points_added"]


def test_chart_zone_shape_and_order():
    baseline = build_baseline(_league())
    player = [
        _shot_chart(made=True, pv=3, dist=24.0, x=2, y=2, stype="Jump Shot"),
        _shot_chart(made=False, pv=2, dist=3.0, x=25, y=4, stype="Layup Shot"),
        _shot_chart(made=True, pv=2, dist=3.0, x=25, y=4, stype="Layup Shot"),
    ]
    out = compute_player_shot_chart(player, baseline)
    fams = [z["family"] for z in out["zones"]]
    assert fams == ["rim", "three"]  # reading order, only present families
    rim = next(z for z in out["zones"] if z["family"] == "rim")
    assert rim["fga"] == 2 and rim["fg_pct"] == 0.5
