"""Tests for the importance/overall backfill (backfill_importance).

Uses the shared `env` fixture. `rebuild_date` takes its ESPN-fetched
game lists as explicit params (rather than fetching internally), so a
unit test can hand it a small fixed schedule with no network/monkeypatch
needed for the core recompute logic; `main()`'s own fetch + wiring is
covered separately by monkeypatching `fetch_games_for_range`.
"""

import json

import pytest

from scripts.backfill_importance import rebuild_date
from src.db.queries import upsert_daily_ranking, upsert_game, upsert_team


def _two_teams(session):
    a = upsert_team(
        session, name="Las Vegas Aces", abbreviation="LV", logo_url="", bpi_rating=0.0
    )
    b = upsert_team(
        session,
        name="New York Liberty",
        abbreviation="NY",
        logo_url="",
        bpi_rating=0.0,
    )
    return a.id, b.id


def test_rebuild_date_rewrites_importance_and_overall_consistently(env):
    """A regular-season row's importance_score moves off the stale value,
    and overall_score stays derived from the CURRENT quality + importance —
    the two must never be rewritten out of step."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-08-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        season_type=2,
        espn_id="G1",
    )
    stale_importance = 987.0  # obviously off the current 0-100 scale
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=stale_importance,
        overall_score=60.0 * 0.6 + stale_importance * 0.4,
        broadcaster="ESPN",
    )
    session.commit()
    session.close()

    session = env.get_session()
    regular_season_games = [
        {
            "team_a": "Las Vegas Aces",
            "team_b": "New York Liberty",
            "date": "2026-08-01",
            "event_id": "G1",
            "season_type": 2,
        }
    ]
    changed = rebuild_date(session, "2026-08-01", [], regular_season_games)
    assert changed == 1

    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-08-01", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score != stale_importance
    assert row.importance_score is not None
    assert 0.0 <= row.importance_score <= 100.0
    assert row.overall_score == row.quality_score * 0.6 + row.importance_score * 0.4
    # This 2-team scenario has no bubble competition at all (both teams
    # always land in the same fate bucket regardless of who wins), so the
    # swing is exactly 0.0 — verified independently by calling
    # run_monte_carlo_simulation/compute_importance_from_matrix with this
    # exact standings+seed outside the test. The zero-gate in
    # _importance_detail_for_game must therefore suppress the panel: a
    # scored-but-zero-stakes game must not carry raw-noise movers.
    assert row.importance_score == 0.0
    assert row.importance_detail is None
    session.close()


def test_rebuild_date_leaves_preseason_and_postseason_untouched(env):
    """Only the regular-season bubble-swing metric changed scale — a
    preseason or postseason row's importance/overall must be left exactly
    as stored, since compute_postseason_swing_from_matrix never consumed
    fate_levels and preseason is pinned at 0 either way."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-04-15",
        time="",
        broadcaster="",
        season_type=1,
        espn_id="PRE1",
    )
    upsert_daily_ranking(
        session,
        date="2026-04-15",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=55.0,
        importance_score=0.0,
        overall_score=55.0 * 0.6,
        broadcaster="",
    )
    session.commit()
    session.close()

    session = env.get_session()
    changed = rebuild_date(session, "2026-04-15", [], [])
    assert changed == 0

    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-04-15", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score == 0.0
    assert row.overall_score == 55.0 * 0.6
    session.close()


def test_rebuild_date_raises_when_regular_season_row_has_no_matching_game(env):
    """A DailyRanking row with no matching Game row can't be proven
    preseason/postseason (season_type only lives on Game), so it must be
    treated as a possibly-regular-season row and hard-fail rather than
    silently pass as untouched — this deliberately reproduces what used to
    be a silent skip (confirmed by manually reverting the fix and re-running
    this test, see task-6-report.md: it failed with no exception raised)."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    # Deliberately no upsert_game call: the (date, team_a_id, team_b_id)
    # Game lookup inside rebuild_date will find nothing.
    stale_importance = 987.0
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=stale_importance,
        overall_score=60.0 * 0.6 + stale_importance * 0.4,
        broadcaster="",
    )
    session.commit()
    session.close()

    session = env.get_session()
    with pytest.raises(RuntimeError, match="could not be re-matched"):
        rebuild_date(session, "2026-08-01", [], [])
    session.close()

    # Nothing should have been committed — the stale value survives.
    session = env.get_session()
    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-08-01", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score == stale_importance
    session.close()


def test_rebuild_date_raises_on_null_season_type_row(env):
    """NULL season_type means 'not yet classified by the season-type
    backfill' (a known degraded state — see compute_standings, which
    separately skips + warns on it), NOT proof the game is preseason or
    postseason. A found Game row with season_type=None must route into
    the same fail-closed path as any other unmatchable row, not be
    silently treated as a legitimate preseason/postseason exemption."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-08-01",
        time="",
        broadcaster="",
        season_type=None,
        espn_id="LEGACY1",
    )
    stale_importance = 987.0
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=stale_importance,
        overall_score=60.0 * 0.6 + stale_importance * 0.4,
        broadcaster="",
    )
    session.commit()
    session.close()

    session = env.get_session()
    with pytest.raises(RuntimeError, match="could not be re-matched"):
        rebuild_date(session, "2026-08-01", [], [])
    session.close()

    # Nothing should have been committed — the stale value survives.
    session = env.get_session()
    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-08-01", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score == stale_importance
    session.close()


def _eight_team_bubble_scenario(
    session,
    include_focal_in_schedule=True,
    focal_importance_score=987.0,
    focal_overall_score=None,
):
    """8 teams (enough that the PLAYOFF_TEAMS=8 cutoff actually bites) with
    a close Seattle/Phoenix bubble race. A completed "boundary" game between
    Seattle and Phoenix falls exactly ON date_str ("2026-08-10") — nothing
    to do with the focal Aces/Liberty matchup being scored, but if the
    as-of-date filter leaks it into "prior" standings it measurably moves
    the bubble race, and therefore the focal game's swing (importance sums
    over every team's fate-level movement, not just the two teams playing).

    `include_focal_in_schedule=False` omits the Aces/Liberty game itself
    from `regular_season_games` (its Game row/DailyRanking row are still
    seeded), so it can't be matched to the sim universe — used by the
    unmatched-game imputation test, which needs the REST of this scenario's
    genuinely nonzero swings as the imputation pool.

    `focal_importance_score`/`focal_overall_score` set the seeded
    DailyRanking row's STORED values before rebuild_date runs — defaults
    match the original stale-987.0 scenario; pass `focal_importance_score=
    None` (with an explicit stale `focal_overall_score`) to seed a row
    that was ALREADY unmatched under the old imputed mean, for the
    overall-must-still-move-when-importance-stays-None regression test.

    Returns (date_str, team_ids, regular_season_games, elo_games).
    """
    if focal_overall_score is None:
        focal_overall_score = 60.0 * 0.6 + focal_importance_score * 0.4
    teams = [
        "Las Vegas Aces",
        "New York Liberty",  # focal matchup, scored on date_str
        "Seattle Storm",
        "Phoenix Mercury",  # boundary game: Seattle beats Phoenix ON date_str
        "Minnesota Lynx",
        "Connecticut Sun",
        "Chicago Sky",
        "Atlanta Dream",
    ]
    ids = {}
    for t in teams:
        row = upsert_team(
            session, name=t, abbreviation=t[:3].upper(), logo_url="", bpi_rating=0.0
        )
        ids[t] = row.id

    date_str = "2026-08-10"

    regular_season_games = []
    if include_focal_in_schedule:
        regular_season_games.append(
            {
                "team_a": "Las Vegas Aces",
                "team_b": "New York Liberty",
                "date": date_str,
                "event_id": "FOCAL",
                "season_type": 2,
            }
        )
    regular_season_games.append(
        {
            "team_a": "Seattle Storm",
            "team_b": "Phoenix Mercury",
            "date": date_str,
            "event_id": "BOUNDARY",
            "winner_team": "Seattle Storm",
            "season_type": 2,
        }
    )
    # A modest remaining schedule so the bubble race isn't trivially decided
    # either way (matters for the boundary game to have room to move it).
    future_dates = [f"2026-08-{d:02d}" for d in range(11, 25)]
    future_pairs = [
        ("Seattle Storm", "Minnesota Lynx"),
        ("Phoenix Mercury", "Connecticut Sun"),
        ("Seattle Storm", "Chicago Sky"),
        ("Phoenix Mercury", "Atlanta Dream"),
        ("Minnesota Lynx", "Connecticut Sun"),
        ("Chicago Sky", "Atlanta Dream"),
        ("Seattle Storm", "Phoenix Mercury"),
        ("Las Vegas Aces", "Minnesota Lynx"),
        ("New York Liberty", "Chicago Sky"),
    ]
    for i, (a, b) in enumerate(future_pairs):
        regular_season_games.append(
            {
                "team_a": a,
                "team_b": b,
                "date": future_dates[i % len(future_dates)],
                "event_id": f"F{i}",
                "season_type": 2,
            }
        )

    # Prior (strictly before date_str) results giving Seattle/Phoenix close,
    # comparable records so the boundary game meaningfully affects the bubble.
    prior_dates = [f"2026-07-{d:02d}" for d in range(1, 25)]
    prior_results = [
        ("Seattle Storm", "Atlanta Dream", "Seattle Storm"),
        ("Phoenix Mercury", "Chicago Sky", "Phoenix Mercury"),
        ("Minnesota Lynx", "Connecticut Sun", "Minnesota Lynx"),
        ("Chicago Sky", "Atlanta Dream", "Chicago Sky"),
    ]
    elo_games = []
    for i, (a, b, w) in enumerate(prior_results):
        regular_season_games.append(
            {
                "team_a": a,
                "team_b": b,
                "date": prior_dates[i],
                "event_id": f"P{i}",
                "winner_team": w,
                "season_type": 2,
            }
        )
        elo_games.append(
            {"team_a": a, "team_b": b, "date": prior_dates[i], "winner_team": w}
        )

    upsert_game(
        session,
        team_a_id=ids["Las Vegas Aces"],
        team_b_id=ids["New York Liberty"],
        date=date_str,
        time="",
        broadcaster="",
        season_type=2,
        espn_id="FOCAL",
    )
    upsert_daily_ranking(
        session,
        date=date_str,
        team_a_id=ids["Las Vegas Aces"],
        team_b_id=ids["New York Liberty"],
        quality_score=60.0,
        importance_score=focal_importance_score,
        overall_score=focal_overall_score,
        broadcaster="",
    )
    session.commit()

    return date_str, ids, regular_season_games, elo_games


def test_rebuild_date_as_of_date_boundary_is_strict(env):
    """The as-of-date boundary must be STRICT on both ends: prior standings
    only count results strictly BEFORE date_str, and the sim universe
    includes games ON date_str.

    Deliberate-break-verified (manually, see task-6-report.md): flipping
    `_standings_as_of`'s `>= date_str: continue` to `> date_str: continue`
    (leaking the on-date boundary game into "prior" standings) moved this
    scenario's computed importance from ~14.17 to ~17.68 — clearly outside
    the tolerance asserted below. Flipping `remaining_rows`'s `>= date_str`
    to `> date_str` (excluding date_str's own games from the sim universe)
    made the focal game unmatched, turning importance_score into None
    instead of a float — caught by the `is not None` assertion.
    """
    session = env.get_session()
    date_str, ids, regular_season_games, elo_games = _eight_team_bubble_scenario(
        session
    )
    session.close()

    session = env.get_session()
    changed = rebuild_date(session, date_str, elo_games, regular_season_games)
    assert changed == 1

    row = (
        session.query(env.DailyRanking)
        .filter_by(
            date=date_str,
            team_a_id=ids["Las Vegas Aces"],
            team_b_id=ids["New York Liberty"],
        )
        .one()
    )
    assert row.importance_score is not None
    # Correct code reproducibly gives ~14.17 (fixed per-date MC seed); a
    # `>=`-to-`>` leak in _standings_as_of measurably moves it to ~17.68.
    assert 10.0 < row.importance_score < 16.0
    session.close()


def test_rebuild_date_populates_importance_detail_for_a_nonzero_row(env):
    """A rewritten row whose new importance is non-zero must get a
    populated, well-formed importance_detail (the "What's at stake" movers
    JSON) — this backfill used to leave it untouched/NULL even when it
    rewrote a real, non-zero importance_score alongside it.

    Reuses the 8-team bubble scenario (proven non-zero — ~14.17 — by
    test_rebuild_date_as_of_date_boundary_is_strict) and parses the stored
    JSON to confirm it's the same "playoffs" movers shape
    daily_update._importance_detail_for_game produces, not just "truthy."
    """
    session = env.get_session()
    date_str, ids, regular_season_games, elo_games = _eight_team_bubble_scenario(
        session
    )
    session.close()

    session = env.get_session()
    changed = rebuild_date(session, date_str, elo_games, regular_season_games)
    assert changed == 1

    row = (
        session.query(env.DailyRanking)
        .filter_by(
            date=date_str,
            team_a_id=ids["Las Vegas Aces"],
            team_b_id=ids["New York Liberty"],
        )
        .one()
    )
    assert row.importance_score is not None
    assert row.importance_score > 0.0
    assert row.importance_detail is not None
    detail = json.loads(row.importance_detail)
    assert detail["metric"] == "playoffs"
    assert detail["if_a_team"] == "Las Vegas Aces"
    assert detail["if_b_team"] == "New York Liberty"
    assert isinstance(detail["movers"], list)
    assert len(detail["movers"]) > 0
    session.close()


def test_rebuild_date_no_rankings_is_a_noop(env):
    """A date with no stored rankings returns 0 without error (main() will
    only ever call this for dates the DB says have rows, but the function
    should degrade gracefully on its own)."""
    session = env.get_session()
    assert rebuild_date(session, "2026-08-02", [], []) == 0
    session.close()


def test_rebuild_date_unmatched_game_imputes_from_remaining_pool_not_zero(env):
    """A regular-season row whose Game.espn_id isn't in the sim universe
    (schedule mismatch) gets importance_score=None — but overall_score must
    still blend in the mean importance of the OTHER remaining games as of
    date_str, not deflate to 0 (compute_daily_scores' imputation semantics).

    Reuses the 8-team bubble scenario with `include_focal_in_schedule=False`:
    the Aces/Liberty Game/DailyRanking rows exist, but their game is
    deliberately absent from `regular_season_games`, so it can't be matched
    — the imputation pool must be drawn from the OTHER real (nonzero, proven
    by test_rebuild_date_as_of_date_boundary_is_strict) remaining swings.
    """
    session = env.get_session()
    date_str, ids, regular_season_games, elo_games = _eight_team_bubble_scenario(
        session, include_focal_in_schedule=False
    )
    session.close()

    session = env.get_session()
    changed = rebuild_date(session, date_str, elo_games, regular_season_games)
    assert changed == 1

    row = (
        session.query(env.DailyRanking)
        .filter_by(
            date=date_str,
            team_a_id=ids["Las Vegas Aces"],
            team_b_id=ids["New York Liberty"],
        )
        .one()
    )
    assert row.importance_score is None
    # Not deflated to 0: overall must reflect a nonzero imputed importance
    # drawn from the OTHER remaining games' own computed swings.
    assert row.overall_score != row.quality_score * 0.6
    assert row.quality_score * 0.6 < row.overall_score <= 100.0
    session.close()


def test_rebuild_date_moves_overall_when_already_unmatched_stays_none(env):
    """A regular-season row that was ALREADY unmatched under the OLD
    imputed mean (importance_score stored as None, overall_score derived
    from a stale imputed value) must still have overall_score rewritten to
    the NEW imputed mean — importance_score correctly stays None, but
    `overall` can't be allowed to freeze on the old scale while every
    sibling row on the date moves, or the archive row's own stored fields
    stop agreeing with each other.

    Reuses the 8-team bubble scenario with `include_focal_in_schedule=False`
    (so the focal game is unmatched both before and after) and seeds the
    focal row's STORED state as `importance_score=None` with a stale
    overall_score computed from an old-scale imputed mean of 987.0 — i.e.
    exactly what an old-scale unmatched row would have looked like.
    """
    session = env.get_session()
    stale_overall = 60.0 * 0.6 + 987.0 * 0.4
    date_str, ids, regular_season_games, elo_games = _eight_team_bubble_scenario(
        session,
        include_focal_in_schedule=False,
        focal_importance_score=None,
        focal_overall_score=stale_overall,
    )
    session.close()

    session = env.get_session()
    changed = rebuild_date(session, date_str, elo_games, regular_season_games)
    assert changed == 1

    row = (
        session.query(env.DailyRanking)
        .filter_by(
            date=date_str,
            team_a_id=ids["Las Vegas Aces"],
            team_b_id=ids["New York Liberty"],
        )
        .one()
    )
    assert row.importance_score is None
    assert row.overall_score != stale_overall
    # Same nonzero-blend bound as the sibling imputation test — this is
    # the same imputed pool, just approached from an already-None start.
    assert row.quality_score * 0.6 < row.overall_score <= 100.0
    session.close()


def test_main_fails_closed_on_empty_fetch_with_ranked_dates_present(env, monkeypatch):
    """An empty ESPN fetch does NOT make a healthy row unmatchable —
    rebuild_date's unmatchable check keys off the DB's own Game table and
    get_all_teams(session), both independent of this fetch. Without an
    explicit guard, an empty fetch would leave regular_season_games empty,
    every regular-season row would fall into the "not in sim universe"
    branch (importance=None), _impute_missing_importance([]) would return
    0.0 rather than raising, and main() would silently rewrite the whole
    archive to importance=None/overall=quality*0.6 and exit 0.

    Deliberately does NOT monkeypatch rebuild_date (unlike the sibling
    main() tests below) — the real rebuild_date must be reachable for this
    test to actually exercise the guard it's meant to short-circuit.
    """
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-08-01",
        time="",
        broadcaster="",
        season_type=2,
        espn_id="G1",
    )
    stale_importance = 987.0
    stale_overall = 60.0 * 0.6 + stale_importance * 0.4
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=stale_importance,
        overall_score=stale_overall,
        broadcaster="",
    )
    session.commit()
    session.close()

    import scripts.backfill_importance as bf

    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda start, end, failed_windows=None: []
    )
    monkeypatch.setattr(bf.sys, "argv", ["backfill_importance", "--recompute"])

    assert bf.main() == 1

    session = env.get_session()
    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-08-01", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score == stale_importance
    assert row.overall_score == stale_overall
    session.close()


def test_main_recompute_fails_closed_when_a_date_errors(env, monkeypatch):
    """One date raising must not silently exit 0 — the operator needs to
    know to re-run (mirrors the other two backfills' fail-closed gate).

    Must stub `fetch_games_for_range` with a NON-empty regular-season
    fetch — an empty one (`[]`) makes `regular_season_games` empty, which
    the empty-fetch guard in `main()` now catches BEFORE the per-date loop
    ever runs, so `fake_rebuild` would never be invoked and this test would
    silently stop covering the per-date-exception fail-closed path it's
    named for (this happened for real; see task-6-report.md). Asserting
    `calls["n"] == 1` makes that regression impossible to reintroduce
    unnoticed — the test can't pass while skipping its own scenario.
    """
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-08-01",
        time="",
        broadcaster="",
        season_type=2,
        espn_id="G1",
    )
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=987.0,
        overall_score=60.0 * 0.6 + 987.0 * 0.4,
        broadcaster="",
    )
    session.commit()
    session.close()

    import scripts.backfill_importance as bf

    fake_history = [
        {
            "team_a": "Las Vegas Aces",
            "team_b": "New York Liberty",
            "date": "2026-08-01",
            "event_id": "G1",
            "season_type": 2,
            "winner_team": "Las Vegas Aces",
        }
    ]
    monkeypatch.setattr(
        bf,
        "fetch_games_for_range",
        lambda start, end, failed_windows=None: fake_history,
    )

    calls = {"n": 0}

    def fake_rebuild(session, date_str, elo_games, regular_season_games):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(bf, "rebuild_date", fake_rebuild)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_importance", "--recompute"])

    assert bf.main() == 1
    # The raising rebuild_date must actually have been reached — this is
    # what makes the test genuinely exercise the per-date-exception path
    # rather than passing on the exit code alone.
    assert calls["n"] == 1

    # Stale value must survive the failed run untouched.
    session = env.get_session()
    row = (
        session.query(env.DailyRanking)
        .filter_by(date="2026-08-01", team_a_id=a_id, team_b_id=b_id)
        .one()
    )
    assert row.importance_score == 987.0
    session.close()


def test_main_dry_run_does_not_call_rebuild_date(env, monkeypatch):
    """Without --recompute, main() only lists dates; it must not write."""
    session = env.get_session()
    a_id, b_id = _two_teams(session)
    upsert_daily_ranking(
        session,
        date="2026-08-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=60.0,
        importance_score=987.0,
        overall_score=60.0 * 0.6 + 987.0 * 0.4,
        broadcaster="",
    )
    session.commit()
    session.close()

    import scripts.backfill_importance as bf

    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda start, end, failed_windows=None: []
    )
    calls = {"n": 0}

    def fake_rebuild(*args, **kwargs):
        calls["n"] += 1
        return 0

    monkeypatch.setattr(bf, "rebuild_date", fake_rebuild)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_importance"])

    assert bf.main() == 0
    assert calls["n"] == 0
