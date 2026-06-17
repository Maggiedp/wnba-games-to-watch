"""Entry point. `validate` runs validators over a sample of a season (incl. OT and
playoff games); `depth` walks seasons backward to find where clean stints stop."""

import json
import sys
from scripts.spikes import darko_fetch as fetch
from scripts.spikes.darko_validate import validate_game


def _ot_and_playoff_ids(pbp):
    """Pick game_ids: a few regular, one OT (max period > 4), one playoff."""
    has_type = "season_type" in pbp.columns
    reg = pbp[pbp["season_type"] == 2] if has_type else pbp
    ids = list(reg.sort_values("sequence_number")["game_id"].drop_duplicates())[:6]
    ot = pbp.groupby("game_id")["period"].max()
    ot_ids = list(ot[ot > 4].index[:1])
    play = pbp[pbp["season_type"] == 3] if has_type else pbp.iloc[0:0]
    play_ids = list(play["game_id"].drop_duplicates()[:1])
    seen, out = set(), []
    for g in ids + ot_ids + play_ids:
        if g not in seen:
            seen.add(g)
            out.append(int(g))
    return out


def _summary(reports):
    n = len(reports)
    return {
        "games": n,
        "mean_invariant_ok": sum(r["invariant_ok_frac"] for r in reports) / n,
        "all_starters_ok": all(r["starters_ok"] for r in reports),
        "mean_minutes_ok": sum(r["minutes_ok_frac"] for r in reports) / n,
        "worst_minutes_ok": min(r["minutes_ok_frac"] for r in reports),
    }


def run_validate(season=2025):
    pbp = fetch.load_pbp(season)
    box = fetch.load_player_box(season)
    sample = _ot_and_playoff_ids(pbp)
    reports = [validate_game(pbp, box, g) for g in sample]
    print(
        json.dumps(
            {"season": season, "per_game": reports, "summary": _summary(reports)},
            indent=2,
            default=str,
        )
    )


def run_depth():
    results = []
    for season in range(2025, 2001, -1):
        try:
            pbp = fetch.load_pbp(season)
            box = fetch.load_player_box(season)
        except Exception as e:
            results.append({"season": season, "status": f"no data: {e}"})
            continue
        n_sub = int((pbp["type_text"] == "Substitution").sum())
        # Sample games spread across the season (NOT a single opener) — a season's
        # verdict must not hinge on one possibly-malformed early game.
        gids = fetch.game_ids(season)
        sample = gids[:: max(1, len(gids) // 8)][:8]
        inv, mins = [], []
        for g in sample:
            try:
                rep = validate_game(pbp, box, int(g))
                inv.append(rep["invariant_ok_frac"])
                mins.append(rep["minutes_ok_frac"])
            except Exception:
                pass
        if not inv:
            results.append(
                {
                    "season": season,
                    "sub_events": n_sub,
                    "status": "reconstruct failed for whole sample",
                }
            )
            continue
        results.append(
            {
                "season": season,
                "sub_events": n_sub,
                "games_sampled": len(inv),
                "invariant_mean": round(sum(inv) / len(inv), 3),
                "invariant_worst": round(min(inv), 3),
                "minutes_mean": round(sum(mins) / len(mins), 3),
                "minutes_worst": round(min(mins), 3),
            }
        )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "depth":
        run_depth()
    else:
        run_validate()
