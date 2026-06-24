#!/usr/bin/env python3
"""Daily update job for WNBA Games to Watch."""

import json
import logging
import os
import random
import sys
from datetime import date, datetime, timedelta

from src.constants import UN_FINALIZE_STATUSES, GameStatus
from src.data.espn_api import (
    ESPNAPIError,
    ESPNNotFoundError,
    _SEASON_END,
    fetch_bpi_ratings,
    fetch_games_for_range,
    fetch_live_win_probability,
    fetch_schedule_and_results,
    fetch_team_details,
    today_et,
)
from src.data.wnba_schedule import (
    enhance_games_with_broadcasters,
    fetch_wnba_schedule_broadcasters,
)
from src.db.queries import (
    delete_importance_ceilings_before,
    get_all_teams,
    get_completed_games,
    get_completed_games_missing_excitement,
    get_completed_games_missing_shape,
    get_completed_postseason_games,
    get_games_for_excitement_refresh,
    get_importance_max_swing,
    get_team_abbrev_map,
    get_team_by_id,
    get_team_by_name,
    get_teams_by_ids,
    replace_elo_history,
    save_importance_max_swing,
    upsert_daily_ranking,
    upsert_game,
    upsert_game_shape,
    upsert_playoff_probability,
    upsert_team,
)
from scripts.backfill_legacy_espn_ids import backfill_legacy_espn_ids
from scripts.backfill_preseason_season_type import backfill_legacy_preseason
from src.db.schema import get_session, init_db
from src.scoring.elo import (
    DEFAULT_HOME_ADVANTAGE,
    INITIAL_RATING,
    EloReplay,
    build_elo_timeline,
    expected_win_prob,
    replay_games,
)
from src.scoring.excitement import compute_excitement
from src.scoring.game_shape import compute_game_shape
from src.scoring.importance import (
    normalize_importance_score,
    normalize_postseason_importance,
)
from src.scoring.monte_carlo import (
    RoundProbabilities,
    compute_directional_movers_from_matrix,
    compute_importance_from_matrix,
    compute_postseason_movers_from_matrix,
    run_monte_carlo_simulation,
    to_team_standings,
)
from src.scoring.quality import compute_quality_score
from src.scoring.tiebreakers import PLAYOFF_TEAMS, increment_h2h, resolve_seeding

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/daily_update.log"),
    ],
)
logger = logging.getLogger(__name__)


def _make_team_id_resolver(session):
    """Return a cached team-name → team-id lookup for a single session."""
    cache: dict[str, int | None] = {}

    def resolve(name: str) -> int | None:
        if name not in cache:
            team = get_team_by_name(session, name)
            cache[name] = team.id if team else None
        return cache[name]

    return resolve


# How far back to pull games for Elo warm-up. Two prior seasons gives enough
# updates that ratings have separated from the 1500 seed by opening day.
_ELO_HISTORY_START = date(2024, 5, 1)


def fetch_and_store_bpi_ratings(session) -> dict[str, float]:
    logger.info("Fetching BPI ratings from ESPN...")
    ratings = fetch_bpi_ratings()
    team_details = fetch_team_details()
    if not ratings:
        logger.error("Failed to fetch BPI ratings")
        return {}
    for name, bpi in ratings.items():
        details = team_details.get(name, {})
        upsert_team(
            session,
            name,
            bpi,
            abbreviation=details.get("abbreviation", ""),
            logo_url=details.get("logo_url", ""),
        )

    # Expansion teams won't appear in historical BPI — seed them at 0.0 so
    # their games still get stored and ranked by quality. Shifted harmonic mean
    # treats 0 as league-average (BPI is zero-centered), so this is a reasonable default.
    for name in set(team_details) - set(ratings):
        details = team_details[name]
        upsert_team(
            session,
            name,
            0.0,
            abbreviation=details.get("abbreviation", ""),
            logo_url=details.get("logo_url", ""),
        )
        logger.info(f"Seeded expansion team with BPI=0: {name}")

    from src.scoring.quality import _BPI_MIN, _BPI_MAX

    bpi_vals = list(ratings.values())
    observed_min, observed_max = min(bpi_vals), max(bpi_vals)
    if observed_min < _BPI_MIN or observed_max > _BPI_MAX:
        logger.warning(
            f"BPI out of normalization range: observed [{observed_min:.2f}, {observed_max:.2f}] "
            f"vs scale [{_BPI_MIN}, {_BPI_MAX}] — quality scores will be clamped. "
            f"Consider updating _BPI_MIN/_BPI_MAX in quality.py for next season."
        )

    logger.info(f"Stored {len(team_details)} teams")
    return ratings


def fetch_and_store_games(session) -> list[dict]:
    logger.info("Fetching schedule and results from ESPN...")
    games = fetch_schedule_and_results()
    if not games:
        logger.warning("No games fetched from ESPN")
        return []

    logger.info("Fetching broadcaster info from WNBA.com...")
    today = today_et()
    broadcasters = fetch_wnba_schedule_broadcasters(today)
    games = enhance_games_with_broadcasters(games, broadcasters)

    get_cached_team_id = _make_team_id_resolver(session)
    stored = 0
    for game in games:
        team_a, team_b = game.get("team_a", ""), game.get("team_b", "")
        if not team_a or not team_b:
            continue
        team_a_id = get_cached_team_id(team_a)
        team_b_id = get_cached_team_id(team_b)
        if not team_a_id or not team_b_id:
            logger.warning(f"Unknown team(s): {team_a!r}, {team_b!r} — skipping")
            continue
        winner_team = game.get("winner_team")
        status = game.get("status")
        # Only an explicit un-finalize status downgrades a stored final.
        # STATUS_UNKNOWN and other unrecognized statuses are no-ops to
        # avoid wiping valid completed games on a transient ESPN glitch.
        if winner_team:
            is_complete: bool | None = True
        elif status in UN_FINALIZE_STATUSES:
            is_complete = False
        else:
            is_complete = None
        upsert_game(
            session,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            date=game.get("date", ""),
            time=game.get("time", ""),
            time_utc=game.get("time_utc"),
            broadcaster=game.get("broadcaster", ""),
            winner_id=get_cached_team_id(winner_team) if winner_team else None,
            final_score_a=game.get("final_score_a"),
            final_score_b=game.get("final_score_b"),
            espn_id=game.get("event_id"),
            is_complete=is_complete,
            season_type=game.get("season_type"),
        )
        stored += 1

    logger.info(f"Upserted {stored} games")
    return games


def backfill_missing_season_types(session) -> None:
    """One-shot guard for rows ingested before the season_type column
    existed. The schedule refresh only fetches yesterday-onward, so
    pre-deploy games (notably preseason) would otherwise keep NULL
    season_type forever and continue feeding standings via the
    NULL-tolerant filter in get_completed_games.

    Idempotent: a fast COUNT short-circuits after the first run leaves
    no NULL rows for the current season.
    """
    from src.db.schema import Game

    # Count is over-inclusive on purpose: includes upcoming games with
    # NULL season_type too. Daily ingest populates season_type for new
    # rows, so this only stays non-zero while legacy rows remain — and
    # while it's non-zero, get_completed_games stays in degrade-gracefully
    # mode (NULL-tolerant). Both flips together once the legacy set drains.
    null_count = (
        session.query(Game)
        .filter(Game.date.like("2026-%"))
        .filter(Game.season_type.is_(None))
        .filter(Game.espn_id.isnot(None))
        .count()
    )
    if null_count == 0:
        return
    logger.info(f"Backfilling season_type for {null_count} legacy rows")
    # End date must cover the full playoff window — _SEASON_END is the
    # canonical horizon (extends through October Finals). Capping at Sept 30
    # would leave October postseason rows unclassified and let their wins
    # leak into regular-season standings.
    parsed = fetch_games_for_range(date(2026, 4, 1), _SEASON_END)
    by_espn_id = {
        g["event_id"]: g.get("season_type")
        for g in parsed
        if g.get("event_id") and g.get("season_type") is not None
    }
    updated = 0
    for game in (
        session.query(Game)
        .filter(Game.date.like("2026-%"))
        .filter(Game.season_type.is_(None))
        .filter(Game.espn_id.isnot(None))
        .all()
    ):
        st = by_espn_id.get(game.espn_id)
        if st is not None:
            game.season_type = st
            updated += 1
    session.commit()
    logger.info(f"Backfilled season_type for {updated} games")


DAILY_EXCITEMENT_RETRY_CAP = 50
BACKFILL_ESPN_TIMEOUT_S = 5
# How long after first compute we keep re-checking ESPN in case the PBP
# was refined post-final, plus the per-run cap for that refresh work.
EXCITEMENT_REFRESH_WINDOW_DAYS = 2
EXCITEMENT_REFRESH_CAP = 25


def populate_excitement_for_recent_completions(
    session,
    limit: int | None = DAILY_EXCITEMENT_RETRY_CAP,
    timeout: int = BACKFILL_ESPN_TIMEOUT_S,
) -> None:
    """For 2026 games that completed but have no excitement_index, fetch
    play-by-play from ESPN and compute and store the final excitement score.

    Transient failures (ESPN unreachable, missing PBP, parse error) leave the
    row's excitement_index as NULL so the next run retries it. A persisted
    0.0 would be indistinguishable from a true blowout score and would never
    be retried since the retry query filters on NULL.

    `limit` caps retries per run (least-recently-attempted first, so
    permanently-failing games rotate to the back instead of starving
    the rest of the queue). Pass `limit=None` from the one-shot
    backfill script to process everything.
    `timeout` is per ESPN call; the default is shorter than the live-WP
    panel default since one slow game shouldn't hold up the daily run.
    """
    games = get_completed_games_missing_excitement(
        session, season_year=2026, limit=limit
    )
    if not games:
        logger.info("No completed games need excitement backfill")
        return
    logger.info(f"Computing excitement_index for {len(games)} completed games")
    now = datetime.now()
    stored = 0
    for game in games:
        # Stamp the attempt timestamp BEFORE the network call so a failure
        # path still records that we tried — this is what keeps the retry
        # queue rotating instead of pinning to a permanently-failing head.
        game.excitement_last_attempt_at = now
        try:
            wp = fetch_live_win_probability(game.espn_id, timeout=timeout)
            status = wp.get("status")
            plays = wp.get("plays") or []
            score = compute_excitement(plays, final=True)
        except (ESPNAPIError, ESPNNotFoundError) as e:
            logger.warning(
                f"Could not fetch PBP for game {game.id} (espn_id={game.espn_id}): {e} "
                "— leaving excitement_index NULL for retry"
            )
            continue
        except Exception as e:
            logger.warning(
                f"Failed to compute excitement for game {game.id} (espn_id={game.espn_id}): {e} "
                "— leaving excitement_index NULL for retry"
            )
            continue
        # Gate on ESPN's own "final" signal. The DB winner_id can be set before
        # ESPN has finalized the PBP feed; persisting a partial payload would
        # never be retried because the retry query filters on NULL.
        if status != GameStatus.FINAL:
            logger.warning(
                f"Game {game.id} (espn_id={game.espn_id}) has ESPN status "
                f"{status!r}, not {GameStatus.FINAL} — leaving NULL for retry"
            )
            continue
        if score is None:
            logger.warning(
                f"ESPN returned insufficient play data for game {game.id} "
                f"(espn_id={game.espn_id}) — leaving excitement_index NULL for retry"
            )
            continue
        game.excitement_index = score
        game.excitement_computed_at = now
        stored += 1
    session.commit()
    logger.info(f"Stored excitement_index for {stored} games")


def _build_and_store_shape(session, espn_id, date, abbrev_map, timeout) -> bool:
    """Fetch a completed game's WP series, compute its shape, upsert the
    game_shapes row. Returns True if stored. Shared by the daily populate and
    the backfill. Leaves the row absent (returns False) on a non-final feed,
    insufficient plays, or unparseable scores — so it's retried next run."""
    wp = fetch_live_win_probability(espn_id, timeout=timeout)
    if wp.get("status") != GameStatus.FINAL:
        return False
    metrics = compute_game_shape(wp.get("plays") or [])
    if metrics is None:
        return False
    try:
        home_score = int(wp["home_score"])
        away_score = int(wp["away_score"])
    except (KeyError, ValueError, TypeError):
        return False
    home_team = wp["home_team"]
    away_team = wp["away_team"]
    upsert_game_shape(
        session,
        espn_id=espn_id,
        season=int(date[:4]),
        date=date,
        home_team=home_team,
        away_team=away_team,
        home_abbr=abbrev_map.get(home_team, home_team),
        away_abbr=abbrev_map.get(away_team, away_team),
        home_score=home_score,
        away_score=away_score,
        winner="home" if home_score > away_score else "away",
        excitement=metrics.excitement,
        tension=metrics.tension,
        comeback=metrics.comeback,
        lead_changes=metrics.lead_changes,
        winner_low_wp=metrics.winner_low_wp,
        curve=metrics.curve,
    )
    return True


def populate_game_shapes_for_recent_completions(
    session,
    limit: int | None = DAILY_EXCITEMENT_RETRY_CAP,
    timeout: int = BACKFILL_ESPN_TIMEOUT_S,
) -> None:
    """For completed 2026 games with no game_shapes row, fetch PBP and store the
    shape. Mirrors populate_excitement_for_recent_completions; transient failures
    leave the row absent for next-run retry."""
    games = get_completed_games_missing_shape(session, season_year=2026, limit=limit)
    if not games:
        logger.info("No completed games need game-shape backfill")
        return
    logger.info(f"Computing game shapes for {len(games)} completed games")
    abbrev_map = get_team_abbrev_map(session)
    now = datetime.now()
    stored = 0
    for game in games:
        # Stamp the attempt before the network call so a failure still records
        # that we tried — keeps the capped retry queue rotating (mirrors the
        # excitement populate).
        game.game_shape_last_attempt_at = now
        try:
            if _build_and_store_shape(
                session, game.espn_id, game.date, abbrev_map, timeout
            ):
                stored += 1
        except (ESPNAPIError, ESPNNotFoundError) as e:
            logger.warning(
                f"Could not fetch PBP for shape (espn_id={game.espn_id}): {e} — skipping"
            )
        except Exception as e:
            logger.warning(
                f"Failed to compute shape (espn_id={game.espn_id}): {e} — skipping"
            )
    session.commit()
    logger.info(f"Stored game_shapes for {stored} games")


def refresh_recent_excitement_scores(
    session,
    window_days: int = EXCITEMENT_REFRESH_WINDOW_DAYS,
    limit: int | None = EXCITEMENT_REFRESH_CAP,
    timeout: int = BACKFILL_ESPN_TIMEOUT_S,
) -> None:
    """Re-fetch already-scored games within a bounded freshness window so
    late ESPN PBP refinements can correct an early STATUS_FINAL compute.

    Without this, the first FINAL response becomes the eternal sort key
    even if ESPN later adds/fixes plays. After `window_days` past compute
    time the value is treated as locked.
    """

    cutoff = datetime.now() - timedelta(days=window_days)
    games = get_games_for_excitement_refresh(
        session, cutoff=cutoff, season_year=2026, limit=limit
    )
    if not games:
        logger.info("No recent games eligible for excitement refresh")
        return
    logger.info(f"Refreshing excitement for {len(games)} recent games")
    now = datetime.now()
    rechecked = 0
    updated = 0
    for game in games:
        # Stamp before the network call — same column as backfill, same rotation logic.
        game.excitement_last_attempt_at = now
        try:
            wp = fetch_live_win_probability(game.espn_id, timeout=timeout)
            status = wp.get("status")
            score = compute_excitement(wp.get("plays") or [], final=True)
        except (ESPNAPIError, ESPNNotFoundError) as e:
            logger.warning(
                f"Refresh fetch failed for game {game.id} (espn_id={game.espn_id}): {e}"
            )
            continue
        except Exception as e:
            logger.warning(
                f"Refresh failed for game {game.id} (espn_id={game.espn_id}): {e}"
            )
            continue
        if status != GameStatus.FINAL:
            # The refresh path only updates excitement — it must not
            # un-finalize a stored game from the live-WP summary endpoint.
            # The schedule path (fetch_and_store_games) is the source of
            # truth for completion state; a transient non-final summary
            # response here could otherwise erase real archive entries.
            continue
        if score is None:
            continue
        if score != game.excitement_index:
            logger.info(
                f"Refreshing excitement for game {game.id} (espn_id={game.espn_id}): "
                f"{game.excitement_index} -> {score}"
            )
            game.excitement_index = score
            updated += 1
        # Leave `excitement_computed_at` immutable — it anchors the freshness
        # window. Updating it on refresh would turn the bounded window into
        # a sliding one, keeping rows eligible forever.
        rechecked += 1
    session.commit()
    logger.info(f"Re-checked {rechecked} games; updated {updated}")


def compute_elo_ratings() -> EloReplay:
    """Replay all historical games through the Elo engine to produce current ratings.

    Re-fetches history fresh each run — Elo state isn't persisted, so there's
    nothing that can drift out of sync with the rest of the pipeline. Teams
    without any prior games (expansion teams, or any team pre-opening-day)
    will appear at INITIAL_RATING when looked up later.
    """
    yesterday = date.fromisoformat(today_et()) - date.resolution
    logger.info(f"Fetching Elo history: {_ELO_HISTORY_START} through {yesterday}...")
    all_games = fetch_games_for_range(_ELO_HISTORY_START, yesterday)
    completed = [
        g for g in all_games if g.get("winner_team") and g.get("season_type", 2) != 1
    ]
    logger.info(f"Replaying {len(completed)} completed games through Elo")
    replay = replay_games(completed)
    return replay


def compute_standings(session, elo_ratings: dict[str, float]) -> dict[str, dict]:
    all_teams = get_all_teams(session)
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
    completed = get_completed_games(session, season_year=2026)
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
        team_a = get_team_by_id(session, game.team_a_id)
        team_b = get_team_by_id(session, game.team_b_id)
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


def _calibrate_season_max_swing(standings: dict, all_games: list[dict]) -> float:
    """Return the expected peak importance swing for this season.

    Runs a single 10k Monte Carlo from equal (0-0) standings over all
    non-preseason games, then returns the max swing across every game.
    Called once per season on the first daily-update run and cached in DB.
    """
    zero_standings = {
        team: {**info, "wins": 0, "losses": 0, "h2h": {}}
        for team, info in standings.items()
    }
    remaining = [
        (g["team_a"], g["team_b"]) for g in all_games if g.get("season_type", 2) == 2
    ]
    _, outcome_matrix, playoff_sets, _, _ = run_monte_carlo_simulation(
        zero_standings, remaining, num_simulations=10000, return_matrix=True
    )
    team_names = list(zero_standings.keys())
    swings = compute_importance_from_matrix(
        outcome_matrix, playoff_sets, remaining, team_names
    )
    return max(swings) if swings else 0.75


# Ceilings cached before this date used the pre-noise-floor-correction swing
# formula; the probe in main() drops them so the next lookup recalibrates.
_CEILING_CORRECTION_CUTOFF = datetime(2026, 6, 12)


def _build_current_bracket_state(session, standings: dict):
    """Build the observed BracketState from current seeding and completed
    postseason games.

    Sources playoff history from the DB so games older than the rolling
    ESPN fetch window (yesterday-forward) are not forgotten. A Bo3 takes
    ~4 days; using the in-memory fetch payload alone would drop Game 1
    by the time Game 3 is played, falsely re-opening already-decided
    series.

    Returns None if seeding can't resolve 8 teams (regular season hasn't
    produced a full playoff field yet). Otherwise: when no postseason
    games have completed, returns an empty bracket built from current
    seeding — QF matchups are knowable from regular-season standings, so
    Game 1 of each QF can be matched to a slot and scored. When games
    have completed, decided series are locked and in-progress series
    resume from the current score.
    """
    # resolve_seeding expects TeamStanding objects; daily_update keeps plain dicts.
    seeded = resolve_seeding(to_team_standings(standings))
    if len(seeded) < PLAYOFF_TEAMS:
        return None

    from src.scoring.playoffs import (  # noqa: PLC0415
        empty_bracket_state,
        reconstruct_bracket_state,
    )

    season_year = int(today_et()[:4])
    completed_post_rows = get_completed_postseason_games(session, season_year)
    if not completed_post_rows:
        # Pre-playoffs (or before the first opening-round game finalizes):
        # the QF matchups are known from current seeding. Returning a
        # populated empty bracket lets _importance_for_game match Game 1
        # of each QF to a slot instead of falling back to flat 100.
        return empty_bracket_state(seeded[:PLAYOFF_TEAMS])

    # Resolve team_id → team name once for the rows we have.
    team_ids = set()
    for row in completed_post_rows:
        team_ids.add(row.team_a_id)
        team_ids.add(row.team_b_id)
        if row.winner_id is not None:
            team_ids.add(row.winner_id)
    teams_by_id = get_teams_by_ids(session, team_ids)
    completed_post: list[dict] = []
    for row in completed_post_rows:
        ta = teams_by_id.get(row.team_a_id)
        tb = teams_by_id.get(row.team_b_id)
        winner = teams_by_id.get(row.winner_id) if row.winner_id else None
        if not ta or not tb or not winner:
            continue
        completed_post.append(
            {
                "team_a": ta.name,
                "team_b": tb.name,
                "winner_team": winner.name,
                "date": row.date,
                "event_id": row.espn_id or "",
            }
        )
    if not completed_post:
        return empty_bracket_state(seeded[:PLAYOFF_TEAMS])

    return reconstruct_bracket_state(seeded[:PLAYOFF_TEAMS], completed_post)


def _find_bracket_slot(
    bracket_state, team_a: str, team_b: str
) -> tuple[str, int] | None:
    """Locate the in-progress bracket slot whose participants match {team_a, team_b}.

    Returns (slot_id, next_game_number_in_series), where game number is
    1-indexed and includes already-played games. Returns None if no slot
    matches — e.g. the teams aren't seeded yet (downstream rounds), the
    series is already decided, or the slot's `higher`/`lower` are still
    None (upstream round unresolved).
    """
    if bracket_state is None:
        return None
    pair = {team_a, team_b}
    for slot_id, s in bracket_state.items():
        if (
            s.higher is not None
            and s.lower is not None
            and s.winner is None
            and {s.higher, s.lower} == pair
        ):
            return slot_id, s.higher_wins + s.lower_wins + 1
    return None


def _assign_postseason_slot_lookup(
    upcoming_games: list[dict], bracket_state
) -> dict[str, tuple[str, int]]:
    """Map each upcoming postseason game's event_id to (slot_id, game_num).

    `_find_bracket_slot` alone returns the *next* game in a slot, so when
    ESPN lists multiple unplayed games of the same series (e.g. Game 1 and
    Game 2 of a QF before Game 1 is played), they'd all map to the same
    game_num. This helper sorts scheduled games within each slot by
    (date, time_utc, time, event_id) and assigns sequential game numbers
    starting at `higher_wins + lower_wins + 1`, so each scheduled game
    gets its own ordinal within the series and therefore its own swing
    when partitioned through `compute_postseason_swing_from_matrix`.

    Games whose slot can't be identified (TBD downstream, decided series,
    no bracket_state) are omitted; callers should fall back to flat 100
    for those.
    """
    if bracket_state is None:
        return {}
    by_slot: dict[str, list[dict]] = {}
    for g in upcoming_games:
        if g.get("season_type") != 3:
            continue
        matched = _find_bracket_slot(
            bracket_state, g.get("team_a", ""), g.get("team_b", "")
        )
        if matched is None:
            continue
        slot_id, _ = matched
        by_slot.setdefault(slot_id, []).append(g)

    lookup: dict[str, tuple[str, int]] = {}
    for slot_id, games_in_slot in by_slot.items():
        s = bracket_state[slot_id]
        base = s.higher_wins + s.lower_wins + 1
        games_in_slot.sort(
            key=lambda g: (
                g.get("date", ""),
                g.get("time_utc") or "",
                g.get("time", ""),
                g.get("event_id", ""),
            )
        )
        for offset, g in enumerate(games_in_slot):
            event_id = g.get("event_id", "")
            if event_id:
                lookup[event_id] = (slot_id, base + offset)
    return lookup


def _impute_missing_importance(importances: list[float | None]) -> float:
    """Value to stand in for an unsimulated game's importance in the overall blend.

    A None importance means "stakes unknown" (e.g. an unclassified NULL-season_type
    row), not "zero stakes" — collapsing it to 0 would systematically bury the game.
    Impute the mean of the games that DID get a computed importance so it blends in
    at typical stakes. Falls back to 0.0 only when nothing today was simulated (a
    degenerate case where ranking is quality-order regardless of the constant).
    """
    computed = [i for i in importances if i is not None]
    return sum(computed) / len(computed) if computed else 0.0


def _importance_for_game(
    game: dict,
    raw_swings: list[float],
    remaining_event_index: dict[str, int],
    importance_ceiling: float,
    bracket_state=None,
    bracket_outcomes: list[dict[tuple[str, int], bool]] | None = None,
    champions: list[str | None] | None = None,
    team_names: list[str] | None = None,
    postseason_slot_lookup: dict[str, tuple[str, int]] | None = None,
) -> float | None:
    """Compute the importance score for one upcoming game.

    Preseason: 0. Postseason: championship-swing derived from the existing
    MC run (sims partitioned by who won the focal bracket game), normalized
    against POSTSEASON_MAX_SWING=2.0. Falls back to 100.0 when the game
    can't be matched to an in-progress bracket slot (downstream rounds
    where upstream hasn't resolved, TBD ESPN rows, or no bracket_state).
    Regular season: normalized bubble-swing from the MC run, or None if the
    event_id isn't in the sim universe.

    When `postseason_slot_lookup` is provided, its `(slot_id, game_num)` is
    authoritative for the game's series ordinal — needed so multiple
    upcoming games in the same series get distinct game numbers. Without
    it, falls back to `_find_bracket_slot`'s next-game-in-series result
    (correct only when at most one unplayed game per series is being
    scored).
    """
    season_type = game.get("season_type", 2)
    if season_type == 1:
        return 0.0
    if season_type == 3:
        if (
            bracket_state is None
            or bracket_outcomes is None
            or champions is None
            or team_names is None
        ):
            return 100.0
        event_id = game.get("event_id", "")
        if postseason_slot_lookup is not None:
            located = postseason_slot_lookup.get(event_id)
            if located is None:
                return 100.0
            slot_id, game_num = located
        else:
            matched = _find_bracket_slot(bracket_state, game["team_a"], game["team_b"])
            if matched is None:
                return 100.0
            slot_id, game_num = matched
        from src.scoring.monte_carlo import compute_postseason_swing_from_matrix  # noqa: PLC0415

        swing = compute_postseason_swing_from_matrix(
            slot_id, game_num, bracket_outcomes, champions, team_names
        )
        return normalize_postseason_importance(swing)
    game_index = remaining_event_index.get(game.get("event_id", ""))
    if game_index is None:
        return None
    return normalize_importance_score(
        raw_swings[game_index], max_swing=importance_ceiling
    )


def _importance_detail_for_game(
    game: dict,
    outcome_matrix: list[list[bool | None]],
    playoff_sets: list[set[str]],
    remaining_event_index: dict[str, int],
    bracket_state=None,
    bracket_outcomes: list[dict[tuple[str, int], bool]] | None = None,
    champions: list[str | None] | None = None,
    team_names: list[str] | None = None,
    postseason_slot_lookup: dict[str, tuple[str, int]] | None = None,
    importance: float | None = None,
) -> str | None:
    """Build the directional-movers JSON payload for one upcoming game.

    Regular season: top playoff-odds movers from the MC outcome matrix.
    Postseason: top championship-odds movers from bracket outcomes, with the
    slot's higher/lower seed mapped onto the matchup's team_a/team_b.
    Returns None for preseason, non-simulated games, games that can't be
    located in the sim universe, when no team's odds clear the threshold, or
    when the game's corrected importance is exactly 0 (swing clamped to the
    noise floor — movers report raw per-team deltas, so rendering them would
    show spurious stakes on a game scored as no-signal). importance=None
    means "not scored", not "zero stakes", and does not suppress.
    """
    season_type = game.get("season_type", 2)
    team_a, team_b = game["team_a"], game["team_b"]
    if season_type == 1:
        return None
    if importance == 0.0:
        return None

    if season_type == 3:
        if (
            bracket_state is None
            or bracket_outcomes is None
            or champions is None
            or team_names is None
            or postseason_slot_lookup is None
        ):
            return None
        located = postseason_slot_lookup.get(game.get("event_id", ""))
        if located is None:
            return None
        slot_id, game_num = located
        raw = compute_postseason_movers_from_matrix(
            slot_id, game_num, bracket_outcomes, champions, team_names
        )
        if not raw:
            return None
        slot = bracket_state[slot_id]
        a_is_higher = team_a == slot.higher
        movers = [
            {
                "team": m["team"],
                "if_a": m["if_higher"] if a_is_higher else m["if_lower"],
                "if_b": m["if_lower"] if a_is_higher else m["if_higher"],
            }
            for m in raw
        ]
        return json.dumps(
            {
                "metric": "championship",
                "if_a_team": team_a,
                "if_b_team": team_b,
                "movers": movers,
            }
        )

    if team_names is None:
        return None
    game_index = remaining_event_index.get(game.get("event_id", ""))
    if game_index is None:
        return None

    movers = compute_directional_movers_from_matrix(
        outcome_matrix, playoff_sets, game_index, team_names
    )
    if not movers:
        return None
    return json.dumps(
        {
            "metric": "playoffs",
            "if_a_team": team_a,
            "if_b_team": team_b,
            "movers": movers,
        }
    )


def compute_daily_scores(
    session, games: list[dict], standings: dict
) -> tuple[list[dict], RoundProbabilities]:
    """Score upcoming games and return (scored_games, round_probabilities).

    Uses a single 10k-sim Monte Carlo run. Game importance is derived by
    splitting that run's outcome matrix — no additional simulations needed.
    Playoff probabilities (one per team per round) are the aggregate of the same run.
    """
    today = today_et()

    # Empty `games` means ESPN fetch failed or returned nothing — NOT the
    # same as a legitimate "no upcoming games" end-of-season state (which has
    # completed games in `games` but no future ones). Don't overwrite today's
    # row with synthetic end-of-season odds; keep yesterday's record intact.
    if not games:
        logger.warning(
            "Empty games list — likely ESPN fetch failure. "
            "Skipping ranking/odds update; previous record preserved."
        )
        return [], RoundProbabilities()

    upcoming_games = [
        g
        for g in games
        if g.get("date", "") >= today and g.get("status") != GameStatus.FINAL
    ]

    # Seed with date of last completed game so scores are stable until new results arrive.
    last_completed_date = max(
        (g["date"] for g in games if g.get("status") == GameStatus.FINAL),
        default=today,
    )
    random.seed(int(last_completed_date.replace("-", "")))
    logger.info(f"Monte Carlo seed: last completed game on {last_completed_date}")

    # Only regular-season (season_type == 2) games drive seeding. Postseason
    # games (3) are simulated by the bracket sim; including them here would
    # double-count playoff wins into regular-season standings.
    remaining_games = []
    remaining_event_index: dict[str, int] = {}
    for g in games:
        if g.get("status") != GameStatus.FINAL and g.get("season_type", 2) == 2:
            eid = g.get("event_id", "")
            if eid:
                remaining_event_index[eid] = len(remaining_games)
            remaining_games.append((g["team_a"], g["team_b"]))

    # If postseason is underway, thread observed bracket state through the sim:
    # decided series use the real winner, in-progress series resume from the
    # actual score. With no completed postseason games, this is a no-op.
    bracket_state = _build_current_bracket_state(session, standings)

    # Always run MC: round probabilities are valid even when there are no
    # upcoming regular-season games (e.g. end of season, playoff lulls, or
    # the post-Finals window). With remaining_games empty the sim still
    # resolves seeding from standings and plays the bracket — exactly what
    # the playoff picture needs during those windows.
    logger.info(
        f"Running 10k Monte Carlo over {len(remaining_games)} remaining games..."
    )
    round_probs, outcome_matrix, playoff_sets, bracket_outcomes, champions = (
        run_monte_carlo_simulation(
            standings,
            remaining_games,
            num_simulations=10000,
            return_matrix=True,
            bracket_state=bracket_state,
        )
    )

    if not upcoming_games:
        logger.info("No upcoming games to score — returning round probabilities only")
        return [], round_probs

    team_names = list(standings.keys())
    raw_swings = compute_importance_from_matrix(
        outcome_matrix, playoff_sets, remaining_games, team_names
    )
    # Pre-compute (slot, game_num) per upcoming postseason game so multiple
    # scheduled games in the same series get distinct ordinals (Game 1, 2, 3...
    # rather than all mapped to "next game").
    postseason_slot_lookup = _assign_postseason_slot_lookup(
        upcoming_games, bracket_state
    )
    logger.info(f"Computed importance swings for {len(raw_swings)} remaining games")

    # Quality: normalize by current season's live BPI spread (slow-moving, reflects
    # real team-strength changes).
    bpi_values = [s["bpi"] for s in standings.values()]
    bpi_min, bpi_max = min(bpi_values), max(bpi_values)

    # Importance: normalize by the season-start ceiling (computed once from equal
    # standings over the full schedule, then cached in DB). This keeps scores
    # comparable all season — later games naturally score higher as swings grow.
    season_year = int(today[:4])
    importance_ceiling = get_importance_max_swing(session, season_year)
    if importance_ceiling is None:
        logger.info(
            "First run of season — calibrating importance ceiling from equal standings..."
        )
        importance_ceiling = _calibrate_season_max_swing(standings, games)
        save_importance_max_swing(session, season_year, importance_ceiling)
        logger.info(f"Season importance ceiling: {importance_ceiling:.3f}")

    logger.info(
        f"Normalization: BPI=[{bpi_min:.2f}, {bpi_max:.2f}], "
        f"importance_ceiling={importance_ceiling:.3f}"
    )

    # First pass: score quality + importance per game. `importance` is None when
    # the game wasn't simulated (e.g. an unclassified NULL-season_type row), which
    # is "stakes unknown", NOT "zero stakes" — see the overall-score imputation below.
    partial = []
    for game in upcoming_games:
        team_a, team_b = game["team_a"], game["team_b"]
        bpi_a = standings.get(team_a, {}).get("bpi", 0.0)
        bpi_b = standings.get(team_b, {}).get("bpi", 0.0)
        quality = compute_quality_score(bpi_a, bpi_b, bpi_min=bpi_min, bpi_max=bpi_max)
        elo_a = standings.get(team_a, {}).get("elo", INITIAL_RATING)
        elo_b = standings.get(team_b, {}).get("elo", INITIAL_RATING)
        win_prob_a = expected_win_prob(elo_a, elo_b, DEFAULT_HOME_ADVANTAGE)

        importance = _importance_for_game(
            game,
            raw_swings,
            remaining_event_index,
            importance_ceiling,
            bracket_state=bracket_state,
            bracket_outcomes=bracket_outcomes,
            champions=champions,
            team_names=team_names,
            postseason_slot_lookup=postseason_slot_lookup,
        )
        importance_detail = _importance_detail_for_game(
            game,
            outcome_matrix,
            playoff_sets,
            remaining_event_index,
            bracket_state=bracket_state,
            bracket_outcomes=bracket_outcomes,
            champions=champions,
            team_names=team_names,
            postseason_slot_lookup=postseason_slot_lookup,
            importance=importance,
        )
        partial.append(
            {
                "game": game,
                "quality": quality,
                "importance": importance,
                "win_prob_a": win_prob_a,
                "importance_detail": importance_detail,
            }
        )

    # Impute a missing importance with the mean importance of the other games
    # ranked today, so an unsimulated game blends in at "typical stakes" rather
    # than being deflated to 0 (which would systematically bury it). The stored
    # importance_score stays None (renders as em-dash); only `overall` uses the
    # imputed value. Fallback 0.0 only when no game today has a computed
    # importance — a degenerate case where ranking is quality-order regardless.
    imputed_importance = _impute_missing_importance([p["importance"] for p in partial])

    scored = []
    for p in partial:
        game = p["game"]
        team_a, team_b = game["team_a"], game["team_b"]
        game_date = game.get("date", today)
        importance = p["importance"]
        importance_for_overall = (
            importance if importance is not None else imputed_importance
        )
        overall = p["quality"] * 0.6 + importance_for_overall * 0.4
        imp_log = (
            f"{importance:.1f}"
            if importance is not None
            else f"~{imputed_importance:.1f}(imp)"
        )
        logger.info(
            f"{game_date} {team_a} vs {team_b}: "
            f"quality={p['quality']:.1f} importance={imp_log} overall={overall:.1f}"
        )
        scored.append(
            {
                "team_a": team_a,
                "team_b": team_b,
                "date": game_date,
                "time": game.get("time", ""),
                "quality": p["quality"],
                "importance": importance,
                "overall": overall,
                "broadcaster": game.get("broadcaster", ""),
                "win_prob_a": p["win_prob_a"],
                "importance_detail": p["importance_detail"],
            }
        )

    return scored, round_probs


def store_daily_rankings(session, scored_games: list[dict]) -> None:
    get_cached_team_id = _make_team_id_resolver(session)
    stored = 0
    for game in scored_games:
        team_a_id = get_cached_team_id(game["team_a"])
        team_b_id = get_cached_team_id(game["team_b"])
        if not team_a_id or not team_b_id:
            logger.warning(
                f"Skipping ranking for {game['team_a']} vs {game['team_b']}: team not found"
            )
            continue
        upsert_daily_ranking(
            session,
            date=game["date"],
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            quality_score=game["quality"],
            importance_score=game["importance"],
            overall_score=game["overall"],
            broadcaster=game.get("broadcaster", ""),
            win_prob_a=game.get("win_prob_a"),
            importance_detail=game.get("importance_detail"),
        )
        stored += 1

    logger.info(f"Stored {stored} daily rankings")


def store_playoff_probabilities(
    session, round_probs: RoundProbabilities, snapshot_date: str
) -> None:
    """Persist per-team round-by-round playoff probabilities for a given date."""
    get_cached_team_id = _make_team_id_resolver(session)
    stored = 0
    for team_name, mp_prob in round_probs.make_playoffs.items():
        team_id = get_cached_team_id(team_name)
        if not team_id:
            logger.warning(f"Skipping playoff prob for unknown team: {team_name}")
            continue
        upsert_playoff_probability(
            session,
            date=snapshot_date,
            team_id=team_id,
            probability=mp_prob,
            reach_semis_prob=round_probs.reach_semis.get(team_name),
            reach_finals_prob=round_probs.reach_finals.get(team_name),
            win_championship_prob=round_probs.win_championship.get(team_name),
        )
        stored += 1
    logger.info(f"Stored {stored} playoff probabilities for {snapshot_date}")


def store_elo_history(session, replay: EloReplay, season_year: int) -> None:
    """Persist the per-team Elo trajectory for one season for the
    /transparency chart. Whole-season delete-and-rewrite (idempotent)."""
    timeline = build_elo_timeline(replay.history, str(season_year))
    get_cached_team_id = _make_team_id_resolver(session)
    rows: list[tuple[int, str, float]] = []
    missing: list[str] = []
    for team_name, points in timeline.items():
        team_id = get_cached_team_id(team_name)
        if not team_id:
            missing.append(team_name)
            continue
        for p in points:
            rows.append((team_id, p["date"], p["rating"]))
    if missing:
        # The rewrite deletes every row for the season, so dropping a team here
        # would silently erase it from the chart and publish a partial season.
        # Raise instead — main()'s non-fatal probe rolls back and the previously
        # stored complete season stands until the team data is fixed.
        raise ValueError(
            f"Unresolved teams for Elo history {season_year}: {sorted(missing)}"
        )
    # replace_elo_history commits internally; nothing fallible should run after
    # it here, or main()'s except-rollback would be a no-op over a live write.
    replace_elo_history(session, season_year, rows)
    logger.info(f"Stored {len(rows)} Elo history points for {season_year}")


def main() -> int:
    logger.info("=== Starting daily update job ===")
    try:
        init_db()
        session = get_session()
        try:
            fetch_and_store_bpi_ratings(session)
            games = fetch_and_store_games(session)
            # Recover espn_id for legacy regular-season rows (the 2026 opener
            # through 2026-05-12, ingested before the espn_id column landed).
            # Must run BEFORE backfill_missing_season_types so that step can
            # event_id-join the newly-set ids and classify them season_type=2,
            # and BEFORE populate_excitement_for_recent_completions so PBP can
            # be fetched the same run. Non-fatal: an ESPN outage here mustn't
            # block the ranking computation; the rows stay NULL for next run.
            try:
                n = backfill_legacy_espn_ids(session)
                if n:
                    logger.info(f"Recovered espn_id for {n} legacy rows")
            except Exception as e:
                # Rollback so downstream queries don't inherit a failed
                # transaction or autoflush partially-staged mutations.
                session.rollback()
                logger.warning(f"Legacy espn_id backfill failed (non-fatal): {e}")
            # Must run BEFORE compute_standings — that path consumes
            # get_completed_games, which excludes preseason but tolerates
            # NULL season_type. Legacy NULL rows could otherwise feed
            # preseason wins/losses into standings until backfilled.
            # Non-fatal: an ESPN outage here mustn't block the user-visible
            # ranking computation. Worst case is standings see a stale
            # preseason game for one more day until ESPN recovers.
            try:
                backfill_missing_season_types(session)
            except Exception as e:
                # Rollback so downstream queries don't inherit a failed
                # transaction or autoflush partially-staged mutations.
                session.rollback()
                logger.warning(f"season_type backfill failed (non-fatal): {e}")
            # Reclassify pre-espn_id-column legacy rows as preseason. The
            # event_id-joined backfill above can't reach them (their espn_id
            # is NULL), so they stay NULL-season_type forever and leak into
            # the user-facing completed archive. Idempotent: matches zero
            # rows once cleared. The helper self-commits.
            try:
                n = backfill_legacy_preseason(session)
                if n:
                    logger.info(f"Reclassified {n} legacy preseason rows")
            except Exception as e:
                # Rollback so downstream queries don't inherit a failed
                # transaction or autoflush partially-staged mutations.
                session.rollback()
                logger.warning(f"Legacy preseason backfill failed (non-fatal): {e}")
            # Drop importance ceilings cached with the pre-noise-floor-
            # correction swing formula so compute_daily_scores recalibrates
            # on the corrected scale this run. One-shot: recalibrated rows
            # carry a fresh created_at past the cutoff, so this is a
            # permanent no-op afterward. The helper self-commits.
            try:
                dropped = delete_importance_ceilings_before(
                    session, _CEILING_CORRECTION_CUTOFF
                )
                for year, old_swing in dropped:
                    logger.info(
                        f"Dropped pre-correction importance ceiling for {year} "
                        f"(was {old_swing:.3f}) — recalibrating this run"
                    )
            except Exception as e:
                # Rollback so downstream queries don't inherit a failed
                # transaction. Non-fatal: worst case is one more run on the
                # old (slightly inflated) ceiling.
                session.rollback()
                logger.warning(f"Ceiling invalidation failed (non-fatal): {e}")
            replay = compute_elo_ratings()
            elo_ratings = replay.final_ratings
            standings = compute_standings(session, elo_ratings)
            scored, round_probs = compute_daily_scores(session, games, standings)
            store_daily_rankings(session, scored)
            today = today_et()
            store_playoff_probabilities(session, round_probs, today)
            # Persist the Elo trajectory for the transparency page. Non-fatal:
            # a failure here must not block the user-visible ranking write.
            try:
                store_elo_history(session, replay, int(today[:4]))
            except Exception as e:
                session.rollback()
                logger.warning(f"Elo history store failed (non-fatal): {e}")
            # Archive backfill runs LAST and bounded — a slow/failing ESPN
            # PBP API must not delay the user-visible ranking computation.
            try:
                populate_excitement_for_recent_completions(session)
            except Exception as e:
                logger.warning(f"Excitement backfill failed (non-fatal): {e}")
            try:
                populate_game_shapes_for_recent_completions(session)
            except Exception as e:
                logger.warning(f"Game-shape backfill failed (non-fatal): {e}")
            try:
                refresh_recent_excitement_scores(session)
            except Exception as e:
                logger.warning(f"Excitement refresh failed (non-fatal): {e}")
            logger.info("=== Daily update job completed successfully ===")
            return 0
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Daily update job failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
