"""Tests for the importance/overall backfill (backfill_importance).

Uses the shared `env` fixture. `rebuild_date` takes its ESPN-fetched
game lists as explicit params (rather than fetching internally), so a
unit test can hand it a small fixed schedule with no network/monkeypatch
needed for the core recompute logic; `main()`'s own fetch + wiring is
covered separately by monkeypatching `fetch_games_for_range`.
"""

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


def _eight_team_bubble_scenario(session, include_focal_in_schedule=True):
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

    Returns (date_str, team_ids, regular_season_games, elo_games).
    """
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
        importance_score=987.0,
        overall_score=60.0 * 0.6 + 987.0 * 0.4,
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


def test_main_recompute_fails_closed_when_a_date_errors(env, monkeypatch):
    """One date raising must not silently exit 0 — the operator needs to
    know to re-run (mirrors the other two backfills' fail-closed gate)."""
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

    monkeypatch.setattr(
        bf, "fetch_games_for_range", lambda start, end, failed_windows=None: []
    )

    def fake_rebuild(session, date_str, elo_games, regular_season_games):
        raise RuntimeError("boom")

    monkeypatch.setattr(bf, "rebuild_date", fake_rebuild)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_importance", "--recompute"])

    assert bf.main() == 1

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
