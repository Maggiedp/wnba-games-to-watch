"""Shared types for the DARKO model.

Stint-row contract (the unit RAPM consumes): one team-on-offense segment of a game
with a constant set of 10 players on court. A pandas DataFrame with columns:
  game_id:    int
  date:       datetime.date   (for walk-forward ordering)
  off_players: frozenset[int] (athlete_ids on offense; exactly 5)
  def_players: frozenset[int] (athlete_ids on defense; exactly 5)
  possessions: float          (>0; offensive possessions in this segment)
  points:      float          (points the offense scored in this segment)
RAPM target per row is 100 * points / possessions.
"""

from dataclasses import dataclass


@dataclass
class PlayerImpact:
    """A player's estimated impact in points per 100 possessions.

    Sign convention: higher is better for BOTH off and def_. def_ is points
    *prevented* relative to average (so a good defender has def_ > 0). total is the
    overall pts/100 impact.
    """

    player_id: int
    off: float
    def_: float
    off_sd: float
    def_sd: float

    @property
    def total(self) -> float:
        return self.off + self.def_
