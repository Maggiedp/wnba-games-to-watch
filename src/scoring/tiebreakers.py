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

from src.constants import TEAM_CONFERENCES

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


def conference_playoff_winpct(
    tied_teams: list[str],
    standings: dict[str, "TeamStanding"],
    provisional_playoffs: set[str],
    same_conference: bool,
) -> dict[str, float]:
    """Win% for each tied team vs provisional-playoff teams in target conference.

    same_conference=True: count games vs playoff teams in the team's own conference
                          (WNBA tiebreaker step 3).
    same_conference=False: count games vs playoff teams in the OTHER conference
                           (WNBA tiebreaker step 4).

    Returns 0.5 for a team with no qualifying opponents (e.g., no playoff teams
    in the target conference) — advance to next step in chain.
    """
    result: dict[str, float] = {}
    for name in tied_teams:
        team = standings[name]
        own_conf = TEAM_CONFERENCES[name]
        target_conf = (
            own_conf if same_conference else ("West" if own_conf == "East" else "East")
        )

        wins = 0
        losses = 0
        for opponent, rec in team.h2h.items():
            if opponent == name:
                continue
            if opponent not in provisional_playoffs:
                continue
            if TEAM_CONFERENCES.get(opponent) != target_conf:
                continue
            wins += rec[0]
            losses += rec[1]

        total = wins + losses
        result[name] = wins / total if total > 0 else 0.5
    return result


from collections import defaultdict  # noqa: E402

from src.constants import assert_all_teams_have_conferences  # noqa: E402

PLAYOFF_TEAMS = 8
_MAX_FIXED_POINT_ITERATIONS = 3


def resolve_seeding(
    standings: dict[str, "TeamStanding"],
) -> list[str]:
    """Return team names in seeded order (1st place first), applying the full
    WNBA tiebreaker chain.

    Algorithm:
        1. Provisional sort: wins desc, then H2H within each tied group.
        2. Provisional top 8 = first 8 teams from provisional sort.
        3. Final sort: full chain (wins → H2H → own-conf → other-conf → elo)
           using the provisional set for conference-record tiebreakers.
        4. If new top 8 != provisional top 8, repeat from step 2 with new set.
           Cap at 3 iterations.
    """
    assert_all_teams_have_conferences(standings)

    provisional = _sort_with_keys(
        list(standings.keys()),
        standings,
        provisional_playoffs=set(),  # not used for provisional sort
        include_conference_keys=False,
    )
    provisional_playoffs = set(provisional[:PLAYOFF_TEAMS])

    seeded = provisional
    for _ in range(_MAX_FIXED_POINT_ITERATIONS):
        seeded = _sort_with_keys(
            list(standings.keys()),
            standings,
            provisional_playoffs=provisional_playoffs,
            include_conference_keys=True,
        )
        new_playoffs = set(seeded[:PLAYOFF_TEAMS])
        if new_playoffs == provisional_playoffs:
            return seeded
        provisional_playoffs = new_playoffs

    logger.warning(
        "Tiebreaker resolution did not converge in %d iterations",
        _MAX_FIXED_POINT_ITERATIONS,
    )
    return seeded


def _sort_with_keys(
    teams: list[str],
    standings: dict[str, "TeamStanding"],
    provisional_playoffs: set[str],
    include_conference_keys: bool,
) -> list[str]:
    """Sort teams by the chain. Group by wins, sort within tied groups by
    successive tiebreakers, concatenate."""
    by_wins: dict[int, list[str]] = defaultdict(list)
    for name in teams:
        by_wins[standings[name].wins].append(name)

    final_order: list[str] = []
    for wins in sorted(by_wins.keys(), reverse=True):
        group = by_wins[wins]
        if len(group) == 1:
            final_order.extend(group)
            continue
        h2h = head_to_head_winpct(group, standings)
        if include_conference_keys:
            own_conf = conference_playoff_winpct(
                group, standings, provisional_playoffs, same_conference=True
            )
            other_conf = conference_playoff_winpct(
                group, standings, provisional_playoffs, same_conference=False
            )
        else:
            own_conf = {n: 0.5 for n in group}
            other_conf = {n: 0.5 for n in group}

        sorted_group = sorted(
            group,
            key=lambda n: (
                -h2h[n],
                -own_conf[n],
                -other_conf[n],
                -standings[n].elo,
            ),
        )
        final_order.extend(sorted_group)

    return final_order
