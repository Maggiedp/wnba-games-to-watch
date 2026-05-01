"""WNBA playoff tiebreaker functions.

Pure functions, no I/O. Operate on dicts of TeamStanding (defined in
src.scoring.monte_carlo) and return new structures — never mutate input.

Chain order (matches official WNBA rules):
    1. wins (handled by caller as outer sort key)
    2. head_to_head_winpct
    3. conference_playoff_winpct(same_conference=True)
    4. conference_playoff_winpct(same_conference=False)
    5. fallback: initial elo (handled by caller)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.scoring.monte_carlo import TeamStanding

logger = logging.getLogger(__name__)


def head_to_head_winpct(
    tied_teams: list[str],
    standings: dict[str, "TeamStanding"],
) -> dict[str, float]:
    """Win% for each tied team, counting only games among the tied group.

    Returns 0.5 for a team with no games played against the tied group
    (treats as a tie → next step in the chain breaks it).
    """
    tied_set = set(tied_teams)
    result: dict[str, float] = {}
    for name in tied_teams:
        team = standings[name]
        wins = 0
        losses = 0
        for opponent, rec in team.h2h.items():
            if opponent in tied_set and opponent != name:
                wins += rec[0]
                losses += rec[1]
        total = wins + losses
        result[name] = wins / total if total > 0 else 0.5
    return result
