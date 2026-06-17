"""Three falsifiable checks on a reconstructed game. Verdict thresholds live in the
findings doc; these produce the raw numbers."""

import pandas as pd
from scripts.spikes import darko_lineups as lineups


def validate_game(pbp, box, game_id):
    events, snaps = lineups.walk_lineups(pbp, box, game_id)
    rows = list(events.itertuples(index=False))
    cols = {c: i for i, c in enumerate(events.columns)}

    # 1. Invariant: exactly 5 per team at every event.
    total = len(snaps)
    bad = sum(1 for s in snaps if any(len(v) != 5 for v in s.values()))
    invariant_ok = (total - bad) / total if total else 0.0

    # 2. Starters: P1 seed loaded as exactly 5 per team for two teams.
    seed = lineups.starters_by_team(box, game_id)
    box_starters = set().union(*seed.values()) if seed else set()
    starters_ok = all(len(v) == 5 for v in seed.values()) and len(seed) == 2

    # 3. Minutes reconcile: integrate on-court seconds per player vs box minutes.
    secs = _on_court_seconds(rows, cols, snaps)
    minutes_ok, detail = _reconcile_minutes(secs, box, game_id, tol_seconds=75)

    return {
        "game_id": int(game_id),
        "events": total,
        "invariant_ok_frac": invariant_ok,
        "invariant_bad_events": bad,
        "starters_ok": starters_ok,
        "n_box_starters": len(box_starters),
        "minutes_ok_frac": minutes_ok,
        "minute_misses": detail[:10],
    }


def _on_court_seconds(rows, cols, snaps):
    """Accumulate seconds each player is on court between consecutive events using
    start/end game-seconds-remaining (monotonic non-increasing within the game)."""
    secs: dict[int, float] = {}
    rem_i = cols.get("start_game_seconds_remaining")
    end_i = cols.get("end_game_seconds_remaining")
    if rem_i is None or end_i is None:
        return secs
    for i, r in enumerate(rows):
        start_rem = r[rem_i]
        end_rem = r[end_i]
        if pd.isna(start_rem) or pd.isna(end_rem):
            continue
        elapsed = float(start_rem) - float(end_rem)  # seconds this event spanned
        if elapsed <= 0:
            continue
        for team_set in snaps[i].values():
            for pid in team_set:
                secs[pid] = secs.get(pid, 0.0) + elapsed
    return secs


def _box_minutes(box, game_id):
    g = box[(box["game_id"] == game_id) & box["minutes"].notna()]
    return {int(r["athlete_id"]): float(r["minutes"]) * 60.0 for _, r in g.iterrows()}


def _reconcile_minutes(secs, box, game_id, tol_seconds):
    truth = _box_minutes(box, game_id)
    ok = checked = 0
    misses = []
    for pid, box_s in truth.items():
        if box_s <= 0:
            continue
        checked += 1
        recon_s = secs.get(pid, 0.0)
        if abs(recon_s - box_s) <= tol_seconds:
            ok += 1
        else:
            misses.append(
                {"player": pid, "box_s": round(box_s), "recon_s": round(recon_s)}
            )
    return (ok / checked if checked else 0.0), misses
