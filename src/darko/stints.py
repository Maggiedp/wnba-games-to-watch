"""Build RAPM-ready stint-rows from wehoop pbp + player_box.

Steps 1–2 (this file): reconstruct on-court 5-per-team at every event (lineup
reconstruction) and segment into stint-rows with possessions + points. Step 3
(Task 5, upcoming): missed-sub repair."""

import pandas as pd
from scripts.spikes import darko_lineups


def on_court_snapshots(pbp: pd.DataFrame, box: pd.DataFrame, game_id: int):
    """List of {team_id: frozenset(on_court 5)} after each event, in sequence order.
    Thin wrapper over the validated spike kernel."""
    _events, snapshots, _seed = darko_lineups.walk_lineups(pbp, box, game_id)
    return snapshots


def _ends_possession(ev) -> bool:
    """A possession ends on a made shot, a turnover, a defensive rebound, or the end
    of a period. The real wehoop taxonomy has no single "Made Shot"/"Turnover"
    type_text — made shots are flagged by `scoring_play`, and turnovers/rebounds are
    families of type_text strings — so match against those instead of literals."""
    if bool(ev.get("scoring_play", False)):
        return True
    tt = ev["type_text"]
    if not isinstance(tt, str):
        return False
    if "Turnover" in tt and tt != "No Turnover":
        return True
    return tt in ("Defensive Rebound", "End Period")


def build_stint_rows(pbp, box, game_id, home_team, away_team):
    """One stint-row per (constant-10 segment, team-on-offense). Columns per the
    stint-row contract in types.py. Possessions counted from possession-ending
    events; points from the per-event score delta for the team in possession."""
    _events, snapshots, _seed = darko_lineups.walk_lineups(pbp, box, game_id)
    g = (
        pbp[pbp["game_id"] == game_id]
        .sort_values("sequence_number")
        .reset_index(drop=True)
    )
    date = g["game_date"].iloc[0] if "game_date" in g.columns else None

    # home_score/away_score are keyed to the league's real home/away side, which the
    # pbp records authoritatively in home_team_id — NOT to the arg order of
    # home_team/away_team. Derive the home side from pbp so a team's points land in
    # the right score column regardless of which arg slot the caller passed it in.
    pbp_home_id = (
        int(g["home_team_id"].dropna().iloc[0])
        if "home_team_id" in g.columns
        else home_team
    )

    rows = []
    prev_home, prev_away = 0, 0
    for i, ev in g.iterrows():
        snap = snapshots[i]
        if home_team not in snap or away_team not in snap:
            # wehoop forward-fills home_score/away_score (verified non-null in 2024),
            # so direct int() is safe; .get fallback is for the snapshot-miss branch only.
            prev_home, prev_away = (
                ev.get("home_score", prev_home),
                ev.get("away_score", prev_away),
            )
            continue
        team = ev["team_id"]
        # wehoop forward-fills home_score/away_score (verified non-null across 101 501
        # rows in 2024), so direct int() is safe here.
        d_home = int(ev.get("home_score", prev_home)) - prev_home
        d_away = int(ev.get("away_score", prev_away)) - prev_away
        if team == home_team:
            off, deff = home_team, away_team
        elif team == away_team:
            off, deff = away_team, home_team
        else:
            prev_home, prev_away = prev_home + d_home, prev_away + d_away
            continue
        pts = d_home if team == pbp_home_id else d_away
        poss = 1.0 if _ends_possession(ev) else 0.0
        rows.append(
            {
                "game_id": int(game_id),
                "date": date,
                "off_players": snap[off],
                "def_players": snap[deff],
                "possessions": poss,
                "points": float(max(pts, 0)),
            }
        )
        prev_home, prev_away = prev_home + d_home, prev_away + d_away

    df = pd.DataFrame(rows)
    # Collapse consecutive identical-lineup, same-offense events into stints.
    if df.empty:
        return df
    key = df[["off_players", "def_players"]].map(lambda s: tuple(sorted(s)))
    grp = (key != key.shift()).any(axis=1).cumsum()
    out = (
        df.groupby(grp)
        .agg(
            game_id=("game_id", "first"),
            date=("date", "first"),
            off_players=("off_players", "first"),
            def_players=("def_players", "first"),
            possessions=("possessions", "sum"),
            points=("points", "sum"),
        )
        .reset_index(drop=True)
    )
    return out[out["possessions"] > 0].reset_index(drop=True)


def minutes_discrepancy(recon_seconds: dict, box, game_id):
    """{athlete_id: reconstructed_seconds - box_seconds} for players whose
    reconstructed on-court time overruns the box. Positive = overrun (missed sub).
    Used to down-weight games with poor sub fidelity in RAPM."""
    g = box[(box["game_id"] == game_id) & box["minutes"].notna()]
    truth = dict(zip(g["athlete_id"].astype(int), g["minutes"].astype(float) * 60.0))
    out = {}
    for pid, box_s in truth.items():
        recon_s = recon_seconds.get(int(pid), 0.0)
        if recon_s - box_s > 0:
            out[int(pid)] = recon_s - box_s
    return out


def game_reliability(
    recon_seconds: dict, box, game_id, tol_seconds: float = 75.0
) -> float:
    """Fraction of checked players whose reconstructed minutes reconcile within tol.
    A per-game weight in [0,1] for RAPM (Task 7). Mirrors the spike's minutes-ok
    metric so RAPM trusts clean games more than ragged ones."""
    g = box[(box["game_id"] == game_id) & box["minutes"].notna()]
    truth = dict(zip(g["athlete_id"].astype(int), g["minutes"].astype(float) * 60.0))
    checked = ok = 0
    for pid, box_s in truth.items():
        if box_s <= 0:
            continue
        checked += 1
        if abs(recon_seconds.get(int(pid), 0.0) - box_s) <= tol_seconds:
            ok += 1
    return ok / checked if checked else 0.0
