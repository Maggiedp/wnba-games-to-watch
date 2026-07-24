"""Location+type expected-points-per-shot (xPPS) model + shot-making leaderboard.

Pure functions — no I/O. The endpoint and the daily recompute both call
compute_leaderboard(). "Shot-making" = actual points minus expected points,
where expected points come from 2026 leaguewide make-rates bucketed by
(distance bin x shot family x point value). No defender data — a location+type
model by construction (the known hard limit).
"""

from collections import defaultdict

# Ordered: first substring hit wins. Deliberately literal/defensible — describe
# only what the ESPN type text says, mirroring team_style._PHRASES. NO "three"
# rule here on purpose: three-vs-two is decided by point value in bucket_key /
# the diet layer, not by the type-text family (which rarely says "three").
_FAMILY_RULES = [
    ("rim", ("layup", "dunk", "finger roll", "putback", "tip", "cutting", "hook")),
    ("floater", ("floating", "floater", "running", "driving float")),
    ("mid", ("jump shot", "pullup", "step back", "fadeaway", "turnaround")),
]

_DISTANCE_BINS = [(0, 4), (5, 9), (10, 15), (16, 21), (22, 999)]


def _point_value(shot: dict) -> int:
    # Authoritative value stored at parse time (`_shot_point_value` read the play
    # text, so a missed three is a 3 even though points=0). Do NOT re-infer from
    # points/shot_type here — that misclassifies missed threes as 2s (Task 2 review R1).
    return 3 if shot.get("point_value") == 3 else 2


def shot_family(shot_type: str) -> str:
    t = (shot_type or "").lower()
    for family, needles in _FAMILY_RULES:
        if any(n in t for n in needles):
            return family
    return "other"


def _distance_bin(distance_ft, point_value: int) -> str:
    if point_value == 3:
        return "3pt"
    if distance_ft is None:
        return "na"
    for lo, hi in _DISTANCE_BINS:
        if lo <= distance_ft <= hi:
            return f"{lo}-{hi}"
    return "na"


def bucket_key(shot: dict) -> tuple:
    pv = _point_value(shot)
    fam = "three" if pv == 3 else shot_family(shot.get("shot_type", ""))
    return (_distance_bin(shot.get("distance_ft"), pv), fam, pv)


def _rate(pairs) -> float:
    made = sum(1 for m in pairs if m)
    return made / len(pairs) if pairs else 0.0


def build_baseline(shots: list[dict], min_bucket: int = 25) -> dict:
    """bucket_key -> league make-rate. Buckets under min_bucket attempts fall
    back to (family, point_value), then to point_value, so a sparse bucket never
    hands a player an extreme expectation. Returned dict includes the fallback
    levels under sentinel keys so expected_pps can resolve any shot."""
    by_bucket = defaultdict(list)
    by_family = defaultdict(list)
    by_pv = defaultdict(list)
    for s in shots:
        made = bool(s.get("made"))
        k = bucket_key(s)
        by_bucket[k].append(made)
        by_family[(k[1], k[2])].append(made)
        by_pv[k[2]].append(made)
    baseline = {"_family": {}, "_pv": {}}
    for k, pairs in by_bucket.items():
        baseline[k] = _rate(pairs) if len(pairs) >= min_bucket else None
    for fk, pairs in by_family.items():
        baseline["_family"][fk] = _rate(pairs)
    for pv, pairs in by_pv.items():
        baseline["_pv"][pv] = _rate(pairs)
    return baseline


def expected_pps(shot: dict, baseline: dict) -> float:
    k = bucket_key(shot)
    pv = k[2]
    rate = baseline.get(k)
    if rate is None:
        rate = baseline["_family"].get((k[1], pv))
    if rate is None:
        rate = baseline["_pv"].get(pv, 0.0)
    return rate * pv


def compute_leaderboard(shots: list[dict], min_fga: int = 100) -> list[dict]:
    baseline = build_baseline(shots)
    agg = defaultdict(
        lambda: {
            "fga": 0,
            "made": 0,
            "actual": 0.0,
            "expected": 0.0,
            "name": "",
            "team": "",
            "diet": defaultdict(int),
        }
    )
    for s in shots:
        a = agg[s["athlete_id"]]
        a["name"] = s["athlete_name"]
        a["team"] = s["team_id"]
        a["fga"] += 1
        a["made"] += 1 if s.get("made") else 0
        a["actual"] += s.get("points") or 0
        a["expected"] += expected_pps(s, baseline)
        pv = _point_value(s)
        a["diet"]["three" if pv == 3 else shot_family(s.get("shot_type", ""))] += 1
    rows = []
    for aid, a in agg.items():
        if a["fga"] < min_fga:
            continue
        added = a["actual"] - a["expected"]
        rows.append(
            {
                "athlete_id": aid,
                "athlete_name": a["name"],
                "team_id": a["team"],
                "fga": a["fga"],
                "made": a["made"],
                "actual_pts": round(a["actual"], 2),
                "expected_pts": round(a["expected"], 2),
                "points_added": round(added, 2),
                "points_added_per_100": round(added / a["fga"] * 100, 2),
                "actual_pps": round(a["actual"] / a["fga"], 3),
                "expected_pps": round(a["expected"] / a["fga"], 3),
                "diet": {k: round(v / a["fga"], 3) for k, v in a["diet"].items()},
            }
        )
    rows.sort(key=lambda r: r["points_added"], reverse=True)
    return rows
