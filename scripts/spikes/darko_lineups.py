"""Reconstruct on-court 5-per-team at every pbp event. Seed the opening lineup per
team from the player_box `starter` flag, then apply Substitution events in
sequence. Lineups carry across periods (ESPN logs period-boundary subs as events at
the start of the period)."""

import pandas as pd

SUB = "Substitution"


def starters_by_team(box: pd.DataFrame, game_id: int) -> dict[int, set[int]]:
    """{team_id: {athlete_id,...}} for players flagged starter in this game."""
    g = box[(box["game_id"] == game_id) & (box["starter"])]
    out: dict[int, set[int]] = {}
    for _, r in g.iterrows():
        out.setdefault(int(r["team_id"]), set()).add(int(r["athlete_id"]))
    return out


def walk_lineups(pbp: pd.DataFrame, box: pd.DataFrame, game_id: int):
    """Return (events, snapshots, seed) where events is the game's pbp rows ordered
    by sequence_number, snapshots[i] = {team_id: frozenset(on_court 5)} AFTER
    event i, and seed is the opening {team_id: starters} (returned so callers don't
    recompute it). Seeds from box starters; applies each Substitution."""
    events = pbp[pbp["game_id"] == game_id].sort_values("sequence_number")
    seed = starters_by_team(box, game_id)
    on_court = {t: set(s) for t, s in seed.items()}
    snapshots = []
    for _, r in events.iterrows():
        if r["type_text"] == SUB and pd.notna(r["team_id"]):
            t = int(r["team_id"])
            a_in = int(r["athlete_id_1"]) if pd.notna(r["athlete_id_1"]) else None
            a_out = int(r["athlete_id_2"]) if pd.notna(r["athlete_id_2"]) else None
            if t in on_court:
                if a_out is not None:
                    on_court[t].discard(a_out)
                if a_in is not None:
                    on_court[t].add(a_in)
        snapshots.append({t: frozenset(s) for t, s in on_court.items()})
    return events, snapshots, seed
