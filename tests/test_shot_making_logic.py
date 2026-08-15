import pytest

from src.scoring.shot_making import (
    shot_family,
    bucket_key,
    build_baseline,
    expected_pps,
    compute_leaderboard,
    compute_player_shot_chart,
    player_headline,
    bridge_gaps,
    bridge_scale,
    compute_league_averages,
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


def test_chart_dots_skip_null_coords_and_keep_shots_taken_behind_the_rim():
    # ESPN's coord_y origin is the RIM, not the baseline, so a small negative y
    # is a real position between the rim and the baseline (a reverse layup) —
    # 15 of 995 attempts across a 7-game sample. Clamping those to 0 stacked
    # them on the hoop. Only absurd values are floored.
    baseline = build_baseline(_league())
    player = [
        _shot_chart(made=True, x=25, y=4),
        _shot_chart(
            made=False, x=None, y=None
        ),  # null coords -> no dot, still in zones
        _shot_chart(made=True, x=3, y=-2, pv=3, dist=23.0, stype="Jump Shot"),
    ]
    out = compute_player_shot_chart(player, baseline)
    assert len(out["shots"]) == 2  # null-coord dot dropped
    assert out["fga"] == 3  # zones/fga count all 3
    ys = sorted(s["y"] for s in out["shots"])
    assert ys[0] == -2  # behind the rim, preserved rather than folded onto it


def test_chart_dot_y_is_floored_at_the_baseline():
    # A shot can't be taken from out of bounds, so anything past the baseline is
    # bad data (ESPN uses a large negative sentinel elsewhere in the feed).
    baseline = build_baseline(_league())
    out = compute_player_shot_chart(
        [_shot_chart(made=False, x=25, y=-214748365)], baseline
    )
    assert out["shots"][0]["y"] == -5


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


def test_player_headline_qualified_positive():
    assert player_headline(312, 2.34, 4, 38) == "+2.3 points added · #4 of 38 · 312 FGA"


def test_player_headline_qualified_negative():
    assert (
        player_headline(210, -1.75, 33, 38) == "-1.8 points added · #33 of 38 · 210 FGA"
    )


def test_player_headline_qualified_zero_is_plus():
    assert (
        player_headline(150, 0.0, 20, 38) == "+0.0 points added · #20 of 38 · 150 FGA"
    )


def test_player_headline_sub_threshold():
    # rank is None -> below the leaderboard cutoff: chart-only, no rank line.
    assert player_headline(41, None, None, None) == "41 FGA this season"


def _shots(n_rim, n_three):
    """n_rim made-half rim shots + n_three made-third threes."""
    out = []
    for i in range(n_rim):
        out.append(
            {
                "athlete_id": "a",
                "athlete_name": "A",
                "team_id": "1",
                "shot_type": "Layup Shot",
                "distance_ft": 2.0,
                "point_value": 2,
                "points": 2 if i % 2 == 0 else 0,
                "made": i % 2 == 0,
            }
        )
    for i in range(n_three):
        out.append(
            {
                "athlete_id": "b",
                "athlete_name": "B",
                "team_id": "1",
                "shot_type": "Jump Shot",
                "distance_ft": 24.0,
                "point_value": 3,
                "points": 3 if i % 3 == 0 else 0,
                "made": i % 3 == 0,
            }
        )
    return out


def test_compute_league_averages_spans_every_shot():
    shots = _shots(60, 60)
    avg = compute_league_averages(shots)
    assert avg["fga"] == 120
    # avg_pps is the plain realized rate over all shots, stored to 4 decimals
    total_pts = sum(s["points"] for s in shots)
    assert avg["avg_pps"] == pytest.approx(total_pts / 120, abs=1e-4)
    # the baseline is fitted to these same shots, so expected tracks actual
    assert avg["avg_xpps"] == pytest.approx(avg["avg_pps"], abs=0.05)


def test_compute_league_averages_empty_is_none():
    avg = compute_league_averages([])
    assert avg["fga"] == 0
    assert avg["avg_xpps"] is None
    assert avg["avg_pps"] is None


def test_bridge_gaps_are_exactly_additive():
    g = bridge_gaps(0.932, 1.144, 1.027, 1.037)
    assert g["selection"] == pytest.approx(-0.095, abs=1e-9)
    assert g["total"] == pytest.approx(0.107, abs=1e-9)
    # the defining property: the two components chain to the total, exactly
    assert g["selection"] + g["making"] == pytest.approx(g["total"], abs=1e-12)


def test_bridge_gaps_making_absorbs_the_league_making_residual():
    # L_m = avg_pps - avg_xpps = 0.010; a player whose raw points-added-per-shot
    # equals that residual sits exactly on the making centerline.
    g = bridge_gaps(1.000, 1.010, 1.000, 1.010)
    assert g["making"] == pytest.approx(0.0, abs=1e-12)


class _Row:
    def __init__(self, expected_pps, actual_pps):
        self.expected_pps = expected_pps
        self.actual_pps = actual_pps


def test_bridge_scale_bounds_selection_and_total():
    rows = [_Row(0.932, 1.144), _Row(1.177, 1.152), _Row(1.111, 0.762)]
    # selection gaps: -0.095, +0.150, +0.084 ; total gaps: +0.107, +0.115, -0.275
    scale = bridge_scale(rows, 1.027, 1.037)
    assert scale == pytest.approx(0.275, abs=1e-9)


def test_bridge_scale_is_none_when_board_is_empty_or_anchorless():
    assert bridge_scale([], 1.027, 1.037) is None
    assert bridge_scale([_Row(1.0, 1.0)], None, 1.037) is None


def test_chart_dot_added_carries_enough_precision_to_be_summed():
    """The chart aggregates shots into cells and shows a cell total, so per-shot
    `added` is summed downstream — it is no longer a display-only value.

    Rounding it to cents made those sums drift badly, and NOT as independent
    rounding would predict: `added` is `points - expected_pps(shot)`, and shots
    in one cell share a distance band and family, so they share an expected
    value and round in the SAME direction. The error therefore grows linearly
    with cell size rather than as its square root. Measured live before the
    fix: summing a player's shots missed the zone totals by up to 0.84 pts, and
    a single 81-shot cell showed +40.5 where the truth was +40.1.

    Guard the invariant rather than the digit count: the shot values must
    reproduce the zone totals, which accumulate unrounded server-side."""
    # A league whose rim bucket hits 27/64 -> xPPS exactly 0.84375, so a made 2
    # is added=1.15625 and a miss is added=-0.84375. Both carry the SAME-signed
    # rounding bias at every precision, which is the correlated case. Over the
    # 80 player shots below that bias totals +0.30 at 2 decimals and -0.02 at 3
    # -- both outside the tolerance -- and -0.004 at 4. So this discriminates
    # the fix rather than passing trivially. (The repo's usual _league() fixture
    # yields a clean xPPS of 1.0 and cannot show the problem at all.)
    league = [
        _shot_chart(aid=f"g{i}", made=(i < 27), pv=2, dist=3.0, stype="Layup Shot")
        for i in range(64)
    ]
    baseline = build_baseline(league)
    assert expected_pps(league[0], baseline) == 0.84375, "fixture no longer biases"
    player = [_shot_chart(made=(i % 2 == 0), x=25, y=1) for i in range(80)]
    out = compute_player_shot_chart(player, baseline)

    summed = sum(s["added"] for s in out["shots"])
    zoned = sum(z["added"] for z in out["zones"])
    # Both are shown to one decimal, so they must agree far inside that.
    assert abs(summed - zoned) < 0.01, (
        f"per-shot added sums to {summed} but zones total {zoned}; "
        "precision is too low to aggregate"
    )
    assert abs(summed - out["points_added"]) < 0.01
