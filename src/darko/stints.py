"""Build RAPM-ready stint-rows from wehoop pbp + player_box.

Step 1 (this): reconstruct on-court 5-per-team at every event by reusing the spike
kernel. Step 2 (Task 4): segment into stint-rows with possessions + points. Step 3
(Task 5): missed-sub repair."""

import pandas as pd
from scripts.spikes import darko_lineups


def on_court_snapshots(pbp: pd.DataFrame, box: pd.DataFrame, game_id: int):
    """List of {team_id: frozenset(on_court 5)} after each event, in sequence order.
    Thin wrapper over the validated spike kernel."""
    _events, snapshots, _seed = darko_lineups.walk_lineups(pbp, box, game_id)
    return snapshots
