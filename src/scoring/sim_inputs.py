"""Monte Carlo input assembly from the DB.

`compute_standings` is the genuinely shared piece: the daily run and the live
overlay both derive standings here, so they cannot disagree about wins/losses.

`build_sim_inputs` is the LIVE OVERLAY's assembly and has one caller. The daily
run still builds its own `remaining_games` from the ESPN dicts it already has in
flight (`scripts/daily_update.py`), which is why the two differ on NULL
`season_type` (see the comment at that filter below). Unifying the remaining
schedule too would mean having the daily run re-read it from the DB after its
own upsert — a real change to that path, not a rename, and deliberately not
done here.
"""

import logging
from dataclasses import dataclass

from src.constants import CURRENT_SEASON
from src.db.queries import get_all_teams, get_completed_games, get_upcoming_games
from src.scoring.elo import INITIAL_RATING
from src.scoring.tiebreakers import increment_h2h

logger = logging.getLogger(__name__)


@dataclass
class SimInputs:
    """Everything run_monte_carlo_simulation needs, assembled from the DB."""

    standings: dict[str, dict]
    remaining_games: list[tuple[str, str]]
    remaining_index_by_espn_id: dict[str, int]
    # The loaded team rows, so a caller rendering them doesn't re-query a table
    # this function already read. Postseason deliberately has NO field here: the
    # bracket runs through daily_update's own bracket_state, live mode disables
    # itself on a postseason slate, and an always-None field would read as a
    # working extension point rather than the dead scaffolding it was.
    teams: list
    # False when any team is missing Team.elo_rating — i.e. no daily run has
    # written it yet. The live overlay must stand down rather than simulate a
    # league where every team is INITIAL_RATING. The daily run ignores this:
    # it passes its own freshly replayed ratings to compute_standings.
    elo_populated: bool = True


def compute_standings(
    session, elo_ratings: dict[str, float], teams: list | None = None
) -> dict[str, dict]:
    """Regular-season W/L + h2h per team, with `elo_ratings` attached.

    `teams` lets a caller that has already loaded the table pass it in rather
    than re-querying; omitted, it loads them itself (the daily run's call).
    """
    all_teams = teams if teams is not None else get_all_teams(session)
    # Every team is already in memory — a per-game get_team_by_id here was an
    # N+1 (2.2 queries per completed game, ~650 round-trips over a full season).
    team_by_id = {t.id: t for t in all_teams}
    standings = {
        t.name: {
            "wins": 0,
            "losses": 0,
            "bpi": t.bpi_rating,
            "elo": elo_ratings.get(t.name, INITIAL_RATING),
            "h2h": {},
        }
        for t in all_teams
    }
    completed = get_completed_games(session, season_year=CURRENT_SEASON)
    null_skipped = 0
    for game in completed:
        # Postseason wins/losses don't count toward regular-season seeding.
        if game.season_type == 3:
            continue
        # NULL season_type during the playoff window can mean a postseason
        # game whose backfill failed. Counting it would corrupt seeding;
        # the next daily run should re-attempt the backfill and recompute.
        # Pre-playoffs NULL is also possible (very-early ingest rows from
        # before season_type tracking) — same conservative skip applies.
        if game.season_type is None:
            null_skipped += 1
            continue
        team_a = team_by_id.get(game.team_a_id)
        team_b = team_by_id.get(game.team_b_id)
        if not team_a or not team_b:
            continue
        a_won = game.winner_id == team_a.id
        if a_won:
            standings[team_a.name]["wins"] += 1
            standings[team_b.name]["losses"] += 1
        else:
            standings[team_b.name]["wins"] += 1
            standings[team_a.name]["losses"] += 1
        increment_h2h(standings[team_a.name]["h2h"], team_b.name, won=a_won)
        increment_h2h(standings[team_b.name]["h2h"], team_a.name, won=not a_won)
    if null_skipped:
        logger.warning(
            f"compute_standings: skipped {null_skipped} completed game(s) with "
            f"NULL season_type — backfill should reclassify next run"
        )
    logger.info(f"Computed standings for {len(standings)} teams")
    return standings


def build_sim_inputs(session, since: str) -> SimInputs:
    """Assemble standings and the remaining regular-season schedule from the DB.

    `since` is the LOWER BOUND of the remaining-game window, not "today". The
    live overlay passes yesterday-ET: a 10pm-ET tip is still in progress after
    ET midnight, and its Game.date is yesterday, so a today-floored window would
    drop the very game being watched — `_detect_live_shapes` would report it
    live while it had no index to attach an override to, and the endpoint would
    fall back to the stored snapshot mid-game. Same widening as
    `/api/games/upcoming` and `/api/games/live-status`.

    `remaining_index_by_espn_id` lets a caller address one scheduled game by its
    ESPN id — the live overlay uses it to attach per-game probability overrides.

    Elo comes from Team.elo_rating (written by the daily run). elo_history is
    NOT usable here: it stores the rating a team *enters* each game with.
    """
    teams = get_all_teams(session)
    elo_populated = bool(teams) and all(t.elo_rating is not None for t in teams)
    elo_ratings = {
        t.name: (t.elo_rating if t.elo_rating is not None else INITIAL_RATING)
        for t in teams
    }
    standings = compute_standings(session, elo_ratings, teams=teams)

    team_by_id = {t.id: t for t in teams}
    remaining_games: list[tuple[str, str]] = []
    remaining_index_by_espn_id: dict[str, int] = {}
    for game in get_upcoming_games(session, since):
        # Only regular-season games drive seeding — postseason games are played
        # by the bracket sim, and counting them here would double-count playoff
        # wins into regular-season standings. NULL season_type (a DB row whose
        # backfill hasn't landed) is also skipped: the two code paths read
        # different sources with different NULL semantics. daily_update reads
        # ESPN dicts that always set season_type, so its .get(..., 2) is
        # defensive. Here we read DB rows where NULL is real degradation
        # ("not yet classified"), and counting it as regular season risks
        # leaking a postseason result into seeding. Fail closed: skip NULL,
        # let the next daily run reclassify and recompute (see compute_standings
        # logging in this module).
        if game.season_type != 2:
            continue
        team_a = team_by_id.get(game.team_a_id)
        team_b = team_by_id.get(game.team_b_id)
        if not team_a or not team_b:
            continue
        if game.espn_id:
            remaining_index_by_espn_id[game.espn_id] = len(remaining_games)
        remaining_games.append((team_a.name, team_b.name))

    return SimInputs(
        standings=standings,
        remaining_games=remaining_games,
        remaining_index_by_espn_id=remaining_index_by_espn_id,
        teams=teams,
        elo_populated=elo_populated,
    )
