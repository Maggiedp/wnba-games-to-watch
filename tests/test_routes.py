"""Tests for src/api/routes.py — focused on response formatting."""

import json
from datetime import date as date_cls
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.routes import format_games_response, render_game_detail
from src.data.espn_api import today_et
from src.db.queries import (
    get_team_by_id,
    upsert_daily_ranking,
    upsert_game,
    upsert_game_shape,
    upsert_playoff_probability,
    upsert_team,
)
from src.db.schema import Base, Game


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def team_ids(session):
    a = upsert_team(
        session, name="Team A", abbreviation="TMA", logo_url="", bpi_rating=0.0
    )
    b = upsert_team(
        session, name="Team B", abbreviation="TMB", logo_url="", bpi_rating=0.0
    )
    return a.id, b.id


def test_format_games_response_includes_time_from_game_row(session, team_ids):
    """Response time comes from the Game row (DailyRanking has no time column)."""
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )

    [resp] = format_games_response([ranking], session)

    assert resp.time == "7:00 PM ET"


def test_format_games_response_empty_time_when_no_game_row(session, team_ids):
    """Falls back to empty string if no Game row matches the ranking."""
    a_id, b_id = team_ids

    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="",
    )

    [resp] = format_games_response([ranking], session)

    assert resp.time == ""


def test_format_games_response_playoff_join_survives_utc_rollover(session, team_ids):
    """Writer and reader must resolve to the same ET date across the UTC rollover."""
    from unittest.mock import patch
    import src.data.espn_api as et_mod
    from tests.conftest import frozen_datetime_class, utc

    a_id, b_id = team_ids
    with patch.object(
        et_mod, "datetime", frozen_datetime_class(utc(2026, 5, 19, 3, 30))
    ):
        today = today_et()
        assert today == "2026-05-18"
        upsert_game(
            session, team_a_id=a_id, team_b_id=b_id, date=today, time="", broadcaster=""
        )
        upsert_daily_ranking(
            session,
            date=today,
            team_a_id=a_id,
            team_b_id=b_id,
            quality_score=50.0,
            importance_score=40.0,
            overall_score=46.0,
            broadcaster="",
        )
        upsert_playoff_probability(session, date=today, team_id=a_id, probability=0.81)
        upsert_playoff_probability(session, date=today, team_id=b_id, probability=0.19)

        from src.db.queries import get_daily_rankings

        rankings = get_daily_rankings(session, today)
        [resp] = format_games_response(rankings, session)

    assert resp.team_a_playoff_prob == pytest.approx(0.81)
    assert resp.team_b_playoff_prob == pytest.approx(0.19)


def test_format_games_response_includes_playoff_probs(session, team_ids):
    """format_games_response joins today's playoff odds onto each game response."""
    a_id, b_id = team_ids
    today = today_et()

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=today,
        time="7:00 PM ET",
        broadcaster="ESPN",
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=40.0,
        overall_score=46.0,
        broadcaster="ESPN",
    )
    upsert_playoff_probability(session, date=today, team_id=a_id, probability=0.72)
    upsert_playoff_probability(session, date=today, team_id=b_id, probability=0.48)

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.team_a_playoff_prob == pytest.approx(0.72)
    assert resp.team_b_playoff_prob == pytest.approx(0.48)


def test_format_games_response_playoff_probs_none_when_missing(session, team_ids):
    """playoff probs are None when no odds have been stored for the date."""
    a_id, b_id = team_ids
    today = today_et()

    upsert_game(
        session, team_a_id=a_id, team_b_id=b_id, date=today, time="", broadcaster=""
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=None,
        overall_score=30.0,
        broadcaster="",
    )

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.team_a_playoff_prob is None
    assert resp.team_b_playoff_prob is None


def test_format_games_response_passes_win_prob(session, team_ids):
    """win_prob_a from DailyRanking flows through to GameResponse."""
    a_id, b_id = team_ids
    today = today_et()

    upsert_game(
        session, team_a_id=a_id, team_b_id=b_id, date=today, time="", broadcaster=""
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=40.0,
        overall_score=46.0,
        broadcaster="",
        win_prob_a=0.62,
    )

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.win_prob_a == pytest.approx(0.62)


def test_format_games_response_win_prob_none_when_missing(session, team_ids):
    """win_prob_a is None in response when not stored."""
    a_id, b_id = team_ids
    today = today_et()

    upsert_game(
        session, team_a_id=a_id, team_b_id=b_id, date=today, time="", broadcaster=""
    )
    upsert_daily_ranking(
        session,
        date=today,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=None,
        overall_score=30.0,
        broadcaster="",
    )

    from src.db.queries import get_daily_rankings

    rankings = get_daily_rankings(session, today)
    [resp] = format_games_response(rankings, session)

    assert resp.win_prob_a is None


def test_format_games_response_includes_espn_id(session, team_ids):
    """espn_id from the Game row is passed through to GameResponse."""
    from src.db.queries import get_game_fields  # noqa: F401 — verify it's importable

    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )
    result = format_games_response([ranking], session)
    assert result[0].espn_id == "401856901"


def test_format_games_response_populates_game_status_from_dict(session, team_ids):
    """game_status_by_espn_id dict is applied when provided."""
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )
    result = format_games_response(
        [ranking],
        session,
        game_status_by_espn_id={"401856901": "STATUS_IN_PROGRESS"},
    )
    assert result[0].game_status == "STATUS_IN_PROGRESS"


def test_format_games_response_game_status_none_when_no_dict(session, team_ids):
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401856901",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )
    result = format_games_response([ranking], session)
    assert result[0].game_status is None


def test_completed_endpoint_returns_excitement_sorted_games(env, client):
    """GET /api/games/completed returns 2026 completed games sorted by excitement desc."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    team_a_id = session.query(env.Team).filter_by(name="Aces").one().id
    team_b_id = session.query(env.Team).filter_by(name="Liberty").one().id

    for date, excitement in [
        ("2026-05-20", 3.0),
        ("2026-05-21", 7.0),
        ("2026-05-22", 5.0),
    ]:
        session.add(
            Game(
                team_a_id=team_a_id,
                team_b_id=team_b_id,
                date=date,
                time="",
                broadcaster="ION",
                winner_id=team_a_id,
                final_score_a=80,
                final_score_b=70,
                espn_id=f"e{date}",
                excitement_index=excitement,
            )
        )
        upsert_daily_ranking(
            session,
            date=date,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            quality_score=50.0,
            importance_score=None,
            overall_score=50.0,
            broadcaster="ION",
        )
    session.commit()
    session.close()

    resp = client.get("/api/games/completed")
    assert resp.status_code == 200
    rows = resp.json()
    dates_in_order = [r["date"] for r in rows]
    assert dates_in_order == ["2026-05-21", "2026-05-22", "2026-05-20"]
    assert rows[0]["excitement_index"] == 7.0
    assert rows[0]["final_score_a"] == 80
    assert rows[0]["final_score_b"] == 70


def test_completed_endpoint_includes_null_excitement_sorted_last(env, client):
    """A completed game with NULL excitement_index (ESPN PBP missing or
    transiently unavailable) must still appear in the archive, sorted
    after games that have a score. Otherwise an ESPN outage silently
    deletes real completed games from the user-visible list."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Liberty").one().id

    # Two scored games + one NULL-excitement.
    for date, exc in [("2026-05-20", 4.0), ("2026-05-21", 6.0), ("2026-05-22", None)]:
        game_kwargs = dict(
            team_a_id=a,
            team_b_id=b,
            date=date,
            time="",
            broadcaster="ION",
            winner_id=a,
            final_score_a=80,
            final_score_b=70,
            espn_id=f"e{date}",
        )
        if exc is not None:
            game_kwargs["excitement_index"] = exc
        session.add(Game(**game_kwargs))
        upsert_daily_ranking(
            session,
            date=date,
            team_a_id=a,
            team_b_id=b,
            quality_score=50.0,
            importance_score=None,
            overall_score=50.0,
            broadcaster="ION",
        )
    session.commit()
    session.close()

    rows = client.get("/api/games/completed").json()
    dates_in_order = [r["date"] for r in rows]
    assert dates_in_order == ["2026-05-21", "2026-05-20", "2026-05-22"]
    null_row = next(r for r in rows if r["date"] == "2026-05-22")
    assert null_row["excitement_index"] is None
    assert null_row["final_score_a"] == 80


def test_completed_endpoint_uses_game_broadcaster_over_stale_ranking(env, client):
    """Late broadcaster corrections must reach the archive. `Game.broadcaster`
    is refreshed by every daily run; `DailyRanking.broadcaster` froze at
    pre-game scoring time. The archive must serve the Game value so the
    list and the filter agree with reality after a network change."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Liberty").one().id

    # Game (source of truth) shows the corrected broadcaster; DailyRanking
    # has the stale pre-game value.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-20",
            time="",
            broadcaster="NBA TV",
            winner_id=a,
            final_score_a=80,
            final_score_b=70,
            espn_id="corr",
            excitement_index=5.0,
        )
    )
    upsert_daily_ranking(
        session,
        date="2026-05-20",
        team_a_id=a,
        team_b_id=b,
        quality_score=50.0,
        importance_score=None,
        overall_score=50.0,
        broadcaster="ION",
    )
    session.commit()
    session.close()

    resp = client.get("/api/games/completed")
    rows = resp.json()
    assert len(rows) == 1 and rows[0]["broadcaster"] == "NBA TV"
    # Filter by the corrected broadcaster: game appears.
    resp_correct = client.get("/api/games/filter?mode=completed&broadcaster=NBA%20TV")
    assert [r["date"] for r in resp_correct.json()] == ["2026-05-20"]
    # Filter by the stale value: game does NOT appear.
    resp_stale = client.get("/api/games/filter?mode=completed&broadcaster=ION")
    assert resp_stale.json() == []
    # Stored DailyRanking row is unchanged (we expunged, not persisted).
    fresh_session = env.get_session()
    try:
        from src.db.schema import DailyRanking

        stored = fresh_session.query(DailyRanking).one()
        assert stored.broadcaster == "ION"
    finally:
        fresh_session.close()


def test_completed_endpoint_includes_orphan_games_without_ranking(env, client):
    """A completed game with excitement_index but no DailyRanking row must
    still appear in /api/games/completed (with None scored fields), so the
    archive doesn't silently hide games on a missed daily-update day."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Liberty").one().id

    # Orphan: completed + has excitement, no DailyRanking row.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-20",
            time="",
            broadcaster="ESPN",
            winner_id=a,
            final_score_a=85,
            final_score_b=82,
            espn_id="orphan",
            excitement_index=6.4,
        )
    )
    session.commit()
    session.close()

    resp = client.get("/api/games/completed")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2026-05-20"
    assert row["excitement_index"] == 6.4
    assert row["final_score_a"] == 85 and row["final_score_b"] == 82
    # Pre-game ranking fields are None because no DailyRanking exists.
    assert row["quality_score"] is None
    assert row["overall_score"] is None


def test_filter_endpoint_mode_completed(env, client):
    """/api/games/filter?mode=completed&broadcaster=ION restricts to completed ION games."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Liberty").one().id

    # Completed ION game — past date, would be excluded by date >= today filter.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-10",
            time="",
            broadcaster="ION",
            winner_id=a,
            final_score_a=80,
            final_score_b=70,
            espn_id="x1",
            excitement_index=5.0,
        )
    )
    upsert_daily_ranking(
        session,
        date="2026-05-10",
        team_a_id=a,
        team_b_id=b,
        quality_score=50.0,
        importance_score=None,
        overall_score=50.0,
        broadcaster="ION",
    )
    # Completed but wrong broadcaster — also past.
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date="2026-05-11",
            time="",
            broadcaster="ESPN",
            winner_id=a,
            final_score_a=80,
            final_score_b=70,
            espn_id="x2",
            excitement_index=8.0,
        )
    )
    upsert_daily_ranking(
        session,
        date="2026-05-11",
        team_a_id=a,
        team_b_id=b,
        quality_score=50.0,
        importance_score=None,
        overall_score=50.0,
        broadcaster="ESPN",
    )
    session.commit()
    session.close()

    resp = client.get("/api/games/filter?mode=completed&broadcaster=ION")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["date"] for r in rows] == ["2026-05-10"]


def test_format_games_response_includes_time_utc(session, team_ids):
    """time_utc from the Game row must surface in the response."""
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        time_utc="2026-06-01T23:00:00+00:00",
        broadcaster="ESPN",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )

    [resp] = format_games_response([ranking], session)

    assert resp.time_utc == "2026-06-01T23:00:00+00:00"


def test_format_games_response_clears_both_time_fields_on_tbd(session, team_ids):
    """Full path: ESPN-known game → ESPN withdraws to TBD → API serves neither.

    Regression for the failure mode Codex flagged on PR #41 — clearing
    only time_utc let the frontend keep rendering the stale ET fallback
    via formatLocalTime(game.time_utc, game.time). The combined TBD
    signal (time="" + explicit time_utc=None) must clear both columns
    end-to-end so the response shows no tip-off time at all.
    """
    a_id, b_id = team_ids

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="7:00 PM ET",
        time_utc="2026-06-01T23:00:00+00:00",
        broadcaster="ESPN",
        espn_id="401900001",
    )
    ranking = upsert_daily_ranking(
        session,
        date="2026-06-01",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=42.0,
        broadcaster="ESPN",
    )

    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-01",
        time="",
        time_utc=None,
        broadcaster="ESPN",
        espn_id="401900001",
    )

    [resp] = format_games_response([ranking], session)

    assert resp.time == ""
    assert resp.time_utc is None


def test_playoff_odds_endpoint_shape_and_sort(env, client):
    """GET /api/playoff-odds returns 4 round probs sorted by make_playoffs_prob desc."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a_id = session.query(env.Team).filter_by(name="Aces").one().id
    b_id = session.query(env.Team).filter_by(name="Liberty").one().id

    today = today_et()
    upsert_playoff_probability(
        session,
        date=today,
        team_id=a_id,
        probability=0.85,
        reach_semis_prob=0.60,
        reach_finals_prob=0.40,
        win_championship_prob=0.25,
    )
    upsert_playoff_probability(
        session,
        date=today,
        team_id=b_id,
        probability=0.90,
        reach_semis_prob=0.65,
        reach_finals_prob=0.30,
        win_championship_prob=0.10,
    )
    session.close()

    resp = client.get("/api/playoff-odds")
    assert resp.status_code == 200
    rows = resp.json()
    # Liberty leads on make_playoffs (0.90 > 0.85) despite a lower title shot.
    assert [r["team"] for r in rows] == ["Liberty", "Aces"]
    assert set(rows[0].keys()) == {
        "team",
        "abbreviation",
        "logo_url",
        "make_playoffs_prob",
        "reach_semis_prob",
        "reach_finals_prob",
        "win_championship_prob",
        "wins",
        "losses",
        "seed_distribution",
        "live",
        "live_state",
    }
    assert rows[0]["make_playoffs_prob"] == pytest.approx(0.90)
    assert rows[0]["win_championship_prob"] == pytest.approx(0.10)


def test_upcoming_endpoint_includes_yesterday_et_for_west_coast_viewers(
    env, client, monkeypatch
):
    """The endpoint must return rows keyed to yesterday-ET as well.

    A 10 PM ET game whose ET date has rolled over (UTC midnight crossed)
    is still in tonight's local window for any viewer west of Eastern.
    The frontend's localDateISO filter prunes per-viewer; the server
    needs to provide the candidate set or the boundary case shows
    blank for non-ET users.
    """
    monkeypatch.setattr("src.api.app.today_et", lambda: "2026-05-22")
    monkeypatch.setattr("src.data.espn_api.today_et", lambda: "2026-05-22")
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Liberty").one().id

    # Three games: yesterday-ET (boundary), today-ET, tomorrow-ET.
    for date in ("2026-05-21", "2026-05-22", "2026-05-23"):
        session.add(
            Game(
                team_a_id=a,
                team_b_id=b,
                date=date,
                time="10:00 PM ET",
                broadcaster="ION",
                espn_id=f"g{date}",
            )
        )
        upsert_daily_ranking(
            session,
            date=date,
            team_a_id=a,
            team_b_id=b,
            quality_score=50.0,
            importance_score=0.3,
            overall_score=42.0,
            broadcaster="ION",
        )
    session.commit()
    session.close()

    resp = client.get("/api/games/upcoming")
    assert resp.status_code == 200
    dates = sorted(r["date"] for r in resp.json())
    assert dates == ["2026-05-21", "2026-05-22", "2026-05-23"]


def test_live_status_endpoint_merges_yesterday_and_today_et(client, monkeypatch):
    """Live-status must include yesterday-ET in-progress games.

    Mirrors the upcoming-window widening. Without this, a late-ET game
    still live after the UTC midnight rollover would appear in the
    upcoming response but with no status — the frontend's isLiveStatus
    gate would skip live-WP polling and render stale pregame odds.
    """
    monkeypatch.setattr("src.api.app.today_et", lambda: "2026-05-22")
    monkeypatch.setattr("src.data.espn_api.today_et", lambda: "2026-05-22")

    calls: list[str] = []

    def fake_fetch(game_date: str):
        calls.append(game_date)
        if game_date == "2026-05-22":
            return {"401_today": "STATUS_SCHEDULED"}
        if game_date == "2026-05-21":
            return {"401_yesterday_live": "STATUS_IN_PROGRESS"}
        return {}

    monkeypatch.setattr("src.api.app.fetch_today_game_statuses", fake_fetch)

    resp = client.get("/api/games/live-status")
    assert resp.status_code == 200
    body = resp.json()

    # Both ET days fetched, both espn_ids surfaced.
    assert set(calls) == {"2026-05-21", "2026-05-22"}
    assert body["401_today"] == "STATUS_SCHEDULED"
    assert body["401_yesterday_live"] == "STATUS_IN_PROGRESS"


def test_live_status_endpoint_degrades_gracefully_when_yesterday_fetch_fails(
    client, monkeypatch
):
    """A transient ESPN failure on yesterday's call must NOT strand today's
    live games. Today is the primary call; yesterday is best-effort.
    """
    monkeypatch.setattr("src.api.app.today_et", lambda: "2026-05-22")
    monkeypatch.setattr("src.data.espn_api.today_et", lambda: "2026-05-22")

    from src.data.espn_api import ESPNAPIError

    def fake_fetch(game_date: str):
        if game_date == "2026-05-22":
            return {"401_today_live": "STATUS_IN_PROGRESS"}
        raise ESPNAPIError("simulated yesterday outage")

    monkeypatch.setattr("src.api.app.fetch_today_game_statuses", fake_fetch)

    resp = client.get("/api/games/live-status")
    assert resp.status_code == 200
    assert resp.json() == {"401_today_live": "STATUS_IN_PROGRESS"}


def test_live_status_endpoint_502s_when_today_fetch_fails(client, monkeypatch):
    """Today's call is the canary — failure surfaces as 502 so the
    frontend backoff loop kicks in. Preserves the prior contract.
    """
    monkeypatch.setattr("src.api.app.today_et", lambda: "2026-05-22")
    monkeypatch.setattr("src.data.espn_api.today_et", lambda: "2026-05-22")

    from src.data.espn_api import ESPNAPIError

    def fake_fetch(game_date: str):
        if game_date == "2026-05-22":
            raise ESPNAPIError("simulated today outage")
        return {}

    monkeypatch.setattr("src.api.app.fetch_today_game_statuses", fake_fetch)

    resp = client.get("/api/games/live-status")
    assert resp.status_code == 502


def test_render_game_detail_unknown_espn_id_returns_none(session, team_ids):
    assert render_game_detail(session, "does-not-exist") is None


def test_render_game_detail_known_game_renders_core_fields(session, team_ids):
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    upsert_daily_ranking(
        session,
        date=date,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=78.0,
        importance_score=64.0,
        overall_score=72.0,
        broadcaster="ION",
        win_prob_a=0.58,
    )
    session.commit()

    html = render_game_detail(session, "401736210")

    assert html is not None
    assert "Team A" in html and "Team B" in html
    assert "72" in html  # overall score
    # The shared top nav replaces the old bespoke "back to rankings" link.
    # A detail page is part of the "Games" section, so that item is active
    # (and exactly one item is active, matching every other page).
    assert 'class="site-nav"' in html
    assert (
        '<a href="/" class="site-nav-link is-active" aria-current="page">Games</a>'
        in html
    )
    assert html.count('aria-current="page"') == 1


def test_render_game_detail_shows_blurbs_and_h2h_empty_state(session, team_ids):
    a_id, b_id = team_ids
    date = today_et()
    # Re-upsert by name to set BPI ratings that drive the quality blurb
    # (team_ids fixture created them with bpi 0.0). upsert_team updates in place.
    upsert_team(session, name="Team A", abbreviation="TMA", logo_url="", bpi_rating=6.2)
    upsert_team(session, name="Team B", abbreviation="TMB", logo_url="", bpi_rating=4.8)
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    upsert_daily_ranking(
        session,
        date=date,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=78.0,
        importance_score=64.0,
        overall_score=72.0,
        broadcaster="ION",
        win_prob_a=0.58,
    )
    session.commit()

    html = render_game_detail(session, "401736210")

    assert "How this is scored" in html
    assert "BPI" in html  # quality uses BPI
    assert "Elo" in html  # win prob uses Elo
    assert "First meeting of the season" in html  # no completed H2H yet


def test_render_game_detail_not_simulated_when_no_ranking(session, team_ids):
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="",
        espn_id="401736211",
    )
    session.commit()

    html = render_game_detail(session, "401736211")

    assert html is not None
    assert "Not simulated" in html  # graceful, no crash
    # The pregame qualifier only labels a real projected number; the
    # not-simulated branch has nothing to qualify.
    assert "Pregame projection" not in html


def test_render_game_detail_labels_win_prob_as_pregame(session, team_ids):
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736212",
    )
    upsert_daily_ranking(
        session,
        date=date,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=78.0,
        importance_score=64.0,
        overall_score=72.0,
        broadcaster="ION",
        win_prob_a=0.58,
    )
    session.commit()

    html = render_game_detail(session, "401736212")

    # The frozen pregame Elo number is labeled so it doesn't read as a live
    # number during a game (the live readout is a separate surface).
    assert "Pregame projection" in html


def test_site_url_is_canonical_domain():
    from src.api.routes import _SITE_URL

    assert _SITE_URL == "https://wumbers.com"


def test_render_game_detail_has_og_meta_tags(session, team_ids):
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    upsert_daily_ranking(
        session,
        date=date,
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=78.0,
        importance_score=64.0,
        overall_score=72.0,
        broadcaster="ION",
        win_prob_a=0.58,
    )
    session.commit()

    html = render_game_detail(session, "401736210")

    assert '<meta name="description"' in html
    assert 'property="og:title" content="Team A vs Team B' in html
    assert 'property="og:description"' in html
    assert 'property="og:type" content="article"' in html
    assert 'property="og:url" content="https://wumbers.com/game/401736210"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert 'name="twitter:title"' in html
    assert 'name="twitter:description"' in html
    assert "60% matchup quality" in html


def test_render_game_detail_og_description_uses_not_simulated_fallback(
    session, team_ids
):
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="",
        espn_id="401736211",
    )
    session.commit()

    html = render_game_detail(session, "401736211")

    assert 'property="og:description" content="Not simulated' in html


def test_render_game_detail_passes_abbreviations_via_data_attributes(session, team_ids):
    a_id, b_id = team_ids  # fixture creates Team A/Team B with abbr TMA/TMB
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736213",
    )
    session.commit()

    html = render_game_detail(session, "401736213")

    # Abbreviations reach the chart JS via data-attributes on #wp-chart, read
    # through chartEl.dataset — never injected into a <script> literal.
    assert 'data-home-abbr="TMA"' in html
    assert 'data-away-abbr="TMB"' in html
    assert "chartEl.dataset.homeAbbr" in html
    assert "chartEl.dataset.awayAbbr" in html
    # The old json.dumps-into-script injection (const HOME_ABBR = "TMA";) is gone:
    # the value now comes from the data-attribute, never a quoted JS literal.
    assert 'const HOME_ABBR = "' not in html


def test_render_game_detail_autoescapes_team_names(session, team_ids):
    a_id, b_id = team_ids
    # Rename team_a's record (the one referenced by a_id) in place so the
    # rendered team_a name is the malicious string. upsert_team keys by name,
    # so it would create a NEW team rather than rename a_id's record.
    team_a = get_team_by_id(session, a_id)
    team_a.name = "<script>x</script>"
    team_a.abbreviation = 'X"Y'
    session.commit()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=today_et(),
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736214",
    )
    session.commit()

    html = render_game_detail(session, "401736214")

    assert "<script>x</script>" not in html  # raw markup never emitted
    assert "&lt;script&gt;x&lt;/script&gt;" in html  # autoescaped in h1/summary/og
    assert 'data-home-abbr="X"Y"' not in html  # quote escaped, not raw


def test_buildwpsvg_escapes_team_abbreviations(session, team_ids):
    """The client-side WP chart drops team abbreviations into innerHTML, so they
    must be escaped — team names/abbreviations are external ESPN/DB data. There is
    no JS runtime in this suite, so guard at the source level: the SVG <text>
    labels must use the escaped variables and the escape helper must be present,
    not the raw params interpolated directly."""
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736212",
    )
    session.commit()

    html = render_game_detail(session, "401736212")

    # buildWpSvg escapes both labels via the shared escapeHtml helper.
    assert "function escapeHtml" in html
    assert "const homeLbl = escapeHtml(homeAbbr)" in html
    assert "const awayLbl = escapeHtml(awayAbbr)" in html
    # SVG labels use the escaped vars; the raw, unescaped params are gone.
    assert "${awayLbl}</text>" in html
    assert "${homeLbl}</text>" in html
    assert "${awayAbbr}</text>" not in html
    assert "${homeAbbr}</text>" not in html


def test_detail_chart_header_renders_live_wp_readout(session, team_ids):
    """A live game's chart header surfaces the current win probability as a
    number (the leading team + its WP%), not just the score. There's no JS
    runtime in this suite, so guard at the source level: headerHtml must build
    the readout markup for live games and stay score-only for finished games.

    Leader is derived from the latest play's home_pct (independent of the
    pregame team_a/team_b ordering): home_pct >= 0.5 -> home leads, else away.
    """
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=today_et(),
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736215",
    )
    session.commit()

    html = render_game_detail(session, "401736215")

    # The readout is gated on a live status; the hero markup + CSS must ship.
    assert "if (!isLiveStatus(data.status))" in html
    assert 'class="wp-live-readout"' in html
    assert 'class="wp-live-pct"' in html
    assert ".wp-live-pct {" in html  # CSS for the Fraunces hero number
    assert "win probability &middot;" in html  # the small label + trailing team

    # The hero must never synthesize a probability: ESPN can emit null/garbage
    # home_pct (the server passes it through), and a fake "50%" reads as a real,
    # confident call. The readout uses the latest FINITE [0,1] value (and drops
    # to the status line when none exists) — it does not fall back to 0.5.
    assert "Number.isFinite(v) && v >= 0 && v <= 1" in html
    assert ": 0.5;" not in html  # no synthesized midpoint fallback


def test_homepage_template_is_packaged_and_renders(client):
    """The homepage HTML lives in src/api/templates/homepage.html and is read
    at import time. If that file is missing from the shipped revision (e.g.
    untracked and left out of a deploy), importing src.api.routes fails and the
    whole API 500s on startup. This guards that the template ships and that all
    token placeholders were substituted (no %% markers leak to the page)."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert body.lstrip().startswith("<!DOCTYPE")
    assert "%%" not in body  # every token placeholder was replaced


def test_completed_excitement_badge_paints_all_eyebrows():
    """renderCompleted must use querySelectorAll, not querySelector — renderGames
    emits both a desktop <tr> and a mobile .games-card, each with its own
    .excitement-eyebrow. querySelector (singular) painted only the desktop one,
    leaving the mobile card badge blank."""
    from src.api.routes import render_homepage

    src = render_homepage()
    # The completed-section paint loop must select ALL matching eyebrows.
    assert (
        'container.querySelectorAll(`[data-espn-id="${g.espn_id}"] .excitement-eyebrow`)'
        in src
    )


def test_featured_hero_card_is_clickable():
    """The top-pick hero card must navigate to /game/{espn_id} like the rows/cards:
    it needs data-espn-id/role=link/tabindex on the <article>, and the
    featured-container must be wired into the navToGame click/keydown listener."""
    from src.api.routes import render_homepage

    src = render_homepage()
    # The hero <article> gets a nav-attribute block guarded on espn_id (same contract as
    # the rows/cards), so a click/Enter on it routes through navToGame to /game/{espn_id}.
    assert '<article class="featured"${game.espn_id ?' in src
    # And featured-container is wired into the navToGame click/keydown listener.
    assert (
        "['games-container', 'completed-games-container', 'featured-container']" in src
    )


def test_detail_page_has_live_pill(session, team_ids):
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=today_et(),
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    session.commit()

    html = render_game_detail(session, "401736210")
    assert html is not None
    assert 'class="live-pill"' in html
    assert 'data-live-id="401736210"' in html
    # the existing WP poll toggles the pill — no new fetch
    assert "classList.toggle('is-live'" in html


def test_detail_page_marks_todays_game_for_pregame_polling(session, team_ids):
    # A game scheduled for today must keep polling pre-tipoff so the LIVE pill
    # (and chart) appear when it starts, without a manual reload.
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=today_et(),
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    session.commit()

    html = render_game_detail(session, "401736210")
    assert 'data-is-today="true"' in html
    # the poll keeps watching for tipoff at a pregame cadence
    assert "PREGAME_INTERVAL" in html
    assert "dataset.isToday" in html


def test_detail_page_does_not_pregame_poll_a_non_today_game(session, team_ids):
    # A far-future (or past) game must NOT poll forever in an open tab.
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2020-01-01",
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736211",
    )
    session.commit()

    html = render_game_detail(session, "401736211")
    assert 'data-is-today="false"' in html


def test_playoff_odds_endpoint_includes_wl_record(env, client):
    """GET /api/playoff-odds carries regular-season wins/losses per team."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a_id = session.query(env.Team).filter_by(name="Aces").one().id
    b_id = session.query(env.Team).filter_by(name="Liberty").one().id

    today = today_et()
    upsert_playoff_probability(
        session,
        date=today,
        team_id=a_id,
        probability=0.85,
        reach_semis_prob=0.6,
        reach_finals_prob=0.4,
        win_championship_prob=0.25,
    )
    upsert_playoff_probability(
        session,
        date=today,
        team_id=b_id,
        probability=0.90,
        reach_semis_prob=0.65,
        reach_finals_prob=0.3,
        win_championship_prob=0.10,
    )
    # Aces 2-1, Liberty 1-2 (regular season only).
    year = today[:4]
    for date, winner in (
        (f"{year}-05-10", a_id),
        (f"{year}-05-12", a_id),
        (f"{year}-05-14", b_id),
    ):
        session.add(
            Game(
                team_a_id=a_id,
                team_b_id=b_id,
                date=date,
                winner_id=winner,
                season_type=2,
            )
        )
    session.commit()
    session.close()

    rows = client.get("/api/playoff-odds").json()
    by_team = {r["team"]: r for r in rows}
    assert (by_team["Aces"]["wins"], by_team["Aces"]["losses"]) == (2, 1)
    assert (by_team["Liberty"]["wins"], by_team["Liberty"]["losses"]) == (1, 2)


def test_playoff_odds_endpoint_record_defaults_to_zero(env, client):
    """A team with playoff probs but no completed games serializes 0-0."""
    session = env.get_session()
    upsert_team(session, name="Fire", abbreviation="POR", logo_url="", bpi_rating=0.0)
    f_id = session.query(env.Team).filter_by(name="Fire").one().id
    upsert_playoff_probability(
        session,
        date=today_et(),
        team_id=f_id,
        probability=0.5,
        reach_semis_prob=0.2,
        reach_finals_prob=0.1,
        win_championship_prob=0.05,
    )
    session.close()

    rows = client.get("/api/playoff-odds").json()
    assert (rows[0]["wins"], rows[0]["losses"]) == (0, 0)


def test_playoff_odds_endpoint_omits_record_for_historical_date(env, client):
    """A non-today ?date= snapshot omits wins/losses (no mixed-time data).

    Records are current-season-to-date; attaching them to a past odds snapshot
    would publish internally inconsistent (mixed-time) data, so the endpoint
    returns null wins/losses for any date that isn't today.
    """
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    a_id = session.query(env.Team).filter_by(name="Aces").one().id
    upsert_playoff_probability(
        session,
        date="2026-05-15",  # a past snapshot, not today_et()
        team_id=a_id,
        probability=0.8,
        reach_semis_prob=0.5,
        reach_finals_prob=0.3,
        win_championship_prob=0.2,
    )
    session.close()

    rows = client.get("/api/playoff-odds?date=2026-05-15").json()
    assert rows[0]["wins"] is None
    assert rows[0]["losses"] is None


def test_playoff_odds_default_falls_back_to_latest_when_today_empty(env, client):
    """The default (no ?date=) request falls back to the freshest RECENT day when
    today has no snapshot yet — the pre-6AM daily-run window or a missed run. A
    dedicated page must show current-ish odds, not a blank shell."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    a_id = session.query(env.Team).filter_by(name="Aces").one().id
    # A snapshot from yesterday (within the fallback age bound); none for today.
    yesterday = (date_cls.fromisoformat(today_et()) - timedelta(days=1)).isoformat()
    upsert_playoff_probability(
        session,
        date=yesterday,
        team_id=a_id,
        probability=0.8,
        reach_semis_prob=0.5,
        reach_finals_prob=0.3,
        win_championship_prob=0.2,
    )
    session.close()

    rows = client.get("/api/playoff-odds").json()  # no date → today empty → fallback
    assert [r["team"] for r in rows] == ["Aces"]
    assert rows[0]["make_playoffs_prob"] == 0.8
    # The fallback day isn't today, so records are omitted (mixed-time guard).
    assert rows[0]["wins"] is None
    assert rows[0]["losses"] is None


def test_playoff_odds_default_does_not_fall_back_to_stale_snapshot(env, client):
    """The fallback is age-bounded: a snapshot older than the cutoff (offseason /
    a multi-day outage) must NOT stand in for today — showing last season's final
    odds as current would be wrong, so the page gets its empty state instead."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    a_id = session.query(env.Team).filter_by(name="Aces").one().id
    # 30 days stale — well beyond the ~3-day fallback bound.
    stale = (date_cls.fromisoformat(today_et()) - timedelta(days=30)).isoformat()
    upsert_playoff_probability(
        session,
        date=stale,
        team_id=a_id,
        probability=0.8,
        reach_semis_prob=0.5,
        reach_finals_prob=0.3,
        win_championship_prob=0.2,
    )
    session.close()

    assert client.get("/api/playoff-odds").json() == []


def test_playoff_odds_explicit_empty_date_does_not_fall_back(env, client):
    """An explicit ?date= for a day with no snapshot returns [] — a historical
    lookup stays exact and must never silently jump to another date."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    a_id = session.query(env.Team).filter_by(name="Aces").one().id
    upsert_playoff_probability(
        session,
        date="2026-05-15",
        team_id=a_id,
        probability=0.8,
        reach_semis_prob=0.5,
        reach_finals_prob=0.3,
        win_championship_prob=0.2,
    )
    session.close()

    # 2026-05-16 has no snapshot; the fallback must NOT engage for an explicit date.
    assert client.get("/api/playoff-odds?date=2026-05-16").json() == []


def test_playoff_odds_empty_when_no_snapshot_exists(env, client):
    """No populated snapshot anywhere → [] (the page renders its empty state).
    Confirms the fallback path is a no-op when there's nothing to fall back to."""
    assert client.get("/api/playoff-odds").json() == []


# Valid baseline game_shapes row for the _detail_shape_section unit tests; each
# test overrides only the field under test (cf. _seed_shape in
# test_game_shape_queries.py for the analogous DB-write builder).
def _shape(**overrides):
    from src.db.schema import GameShape

    fields = dict(
        espn_id="401",
        season=2026,
        date="2026-08-15",
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=86,
        winner="home",
        excitement=9.4,
        tension=0.87,
        comeback=0.31,
        lead_changes=9,
        winner_low_wp=0.33,
        curve="[[0.0, 0.5], [1200.0, 0.2], [2400.0, 0.9]]",
    )
    fields.update(overrides)
    return GameShape(**fields)


def test_detail_shape_section_comeback_game():
    from src.api.routes import _detail_shape_section

    html = _detail_shape_section(_shape())
    assert '<div class="detail-shape">' in html
    assert "detail-shape-metrics" in html
    assert "9.4" in html  # excitement (raw)
    assert "8.7" in html  # tension * 10
    assert "6.2" in html  # comeback * 20
    assert "winner trailed to 33%" in html
    assert "9 lead changes" in html
    # The winner-oriented mini + its heading/explainer are gone — the strip is
    # now folded under the interactive WP chart, not a second curve.
    assert "Game shape</h2>" not in html
    assert 'id="shape-mini"' not in html
    assert "data-curve=" not in html
    assert "detail-shape-key" not in html


def test_detail_shape_section_blowout_uses_led_caption():
    from src.api.routes import _detail_shape_section

    # comeback == 0 + a wire-to-wire winner -> "led X%" caption.
    html = _detail_shape_section(
        _shape(
            comeback=0.0,
            lead_changes=0,
            curve="[[0.0, 0.6], [1200.0, 0.8], [2400.0, 0.99]]",
        )
    )
    assert "led 100% of the way" in html  # winner > .5 at all 3 samples
    assert "0 lead changes" in html


def test_detail_shape_section_none_returns_empty():
    from src.api.routes import _detail_shape_section

    assert _detail_shape_section(None) == ""


def test_detail_shape_section_unparseable_curve_returns_empty():
    from src.api.routes import _detail_shape_section

    assert _detail_shape_section(_shape(curve="not json")) == ""


def test_detail_shape_section_invalid_shape_curve_returns_empty():
    from src.api.routes import _detail_shape_section

    # JSON-parseable but not [[num, num], ...] (a dict). Must not crash on pt[1]
    # in the led branch; the whole section is omitted.
    assert _detail_shape_section(_shape(curve='{"a": 1}')) == ""


def test_detail_shape_section_curve_with_quote_string_is_dropped():
    from src.api.routes import _detail_shape_section

    # Well-shaped 2-tuples but a non-numeric first element. The curve is no longer
    # emitted into the HTML (the winner-oriented mini was removed), but validation
    # still rejects a non-finite curve point -> "". comeback > 0 (default) skips
    # the curve-iterating caption branch, so only validation stops it.
    html = _detail_shape_section(
        _shape(curve='[["a\'onmouseover=alert(1)", 0.5], [1.0, 0.9]]')
    )
    assert html == ""
    assert "onmouseover" not in html


def test_detail_shape_section_nonfinite_scalar_returns_empty():
    from src.api.routes import _detail_shape_section

    # winner_low_wp = NaN would crash _pct -> int(NaN) in the comeback branch.
    assert _detail_shape_section(_shape(winner_low_wp=float("nan"))) == ""


def test_detail_shape_section_null_scalar_returns_empty():
    from src.api.routes import _detail_shape_section

    # excitement = None would crash the f"{...:.1f}" format.
    assert _detail_shape_section(_shape(excitement=None)) == ""


def test_detail_shape_section_nan_in_curve_returns_empty():
    from src.api.routes import _detail_shape_section

    # json.loads accepts NaN; it must be rejected so data-curve stays valid JSON.
    assert _detail_shape_section(_shape(curve="[[0.0, NaN], [1.0, 0.9]]")) == ""


def test_render_game_detail_includes_shape_section_when_present(session, team_ids):
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    upsert_game_shape(
        session,
        espn_id="401736210",
        season=2026,
        date=date,
        home_team="Team A",
        away_team="Team B",
        home_abbr="TMA",
        away_abbr="TMB",
        home_score=88,
        away_score=86,
        winner="home",
        excitement=9.4,
        tension=0.87,
        comeback=0.31,
        lead_changes=9,
        winner_low_wp=0.33,
        curve=[[0.0, 0.5], [2400.0, 0.9]],
    )
    session.commit()

    html = render_game_detail(session, "401736210")
    # The metrics strip is folded under the interactive WP chart...
    assert "detail-shape-metrics" in html
    assert "winner trailed to 33%" in html
    # Finished game → the chart's degraded-state placeholder must not claim the
    # game hasn't tipped off (Codex adversarial-review follow-up).
    assert 'data-has-shape="true"' in html
    assert html.index('<div class="detail-shape">') > html.index('id="wp-chart"')
    # ...not as a separate winner-oriented mini, and buildShapeSvg is no longer
    # injected on the detail page.
    assert "Game shape</h2>" not in html
    assert 'id="shape-mini"' not in html
    assert "data-curve=" not in html
    # The JS renderer is no longer injected. Check the function definition, not
    # bare "buildShapeSvg" — _SHARED_HEAD carries that string in a CSS comment.
    assert "function buildShapeSvg" not in html


def test_render_game_detail_omits_shape_section_when_absent(session, team_ids):
    a_id, b_id = team_ids
    date = today_et()
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    session.commit()

    html = render_game_detail(session, "401736210")
    assert html is not None
    # Body marker, not "detail-shape-metrics" — that class name is always in the
    # head CSS; the wrapper div only renders when there's a stored shape.
    assert '<div class="detail-shape">' not in html
    assert 'id="shape-mini"' not in html
    assert 'data-has-shape="false"' in html  # no stored shape → pre-tipoff copy


def test_shape_svg_css_is_shared_across_replay_and_homepage():
    from src.api.routes import render_homepage, render_replay

    # buildShapeSvg's output CSS lives in _SHARED_HEAD; the two real consumers —
    # the /replay gallery and the homepage completed/live minis — must carry it.
    # (The detail page no longer renders a shape SVG, so it's no longer asserted.)
    assert ".shape-nadir" in render_replay()
    assert ".shape-nadir" in render_homepage()


def test_game_detail_route_serves_shape_section(env, client):
    session = env.get_session()
    a = upsert_team(
        session, name="Las Vegas Aces", abbreviation="LV", logo_url="", bpi_rating=0.0
    )
    b = upsert_team(
        session, name="New York Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0
    )
    session.commit()
    date = today_et()
    upsert_game(
        session,
        team_a_id=a.id,
        team_b_id=b.id,
        date=date,
        time="7:00 PM ET",
        broadcaster="ION",
        espn_id="401736210",
    )
    upsert_game_shape(
        session,
        espn_id="401736210",
        season=2026,
        date=date,
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=86,
        winner="home",
        excitement=9.4,
        tension=0.87,
        comeback=0.31,
        lead_changes=9,
        winner_low_wp=0.33,
        curve=[[0.0, 0.5], [2400.0, 0.9]],
    )
    session.commit()
    session.close()

    resp = client.get("/game/401736210")
    assert resp.status_code == 200
    assert "detail-shape-metrics" in resp.text
    assert "winner trailed to 33%" in resp.text
    assert 'id="shape-mini"' not in resp.text
    # Renderer JS no longer injected (function def, not the CSS-comment string).
    assert "function buildShapeSvg" not in resp.text


def test_thin_curve_caps_points_and_keeps_endpoints():
    from src.api.routes import _COMPLETED_MINI_POINTS, _thin_curve

    curve = [[float(i), i / 100.0] for i in range(100)]
    thinned = _thin_curve(curve)

    assert len(thinned) <= _COMPLETED_MINI_POINTS
    assert thinned[0] == curve[0]  # first sample preserved
    assert thinned[-1] == curve[-1]  # last sample preserved


def test_thin_curve_returns_short_curve_unchanged():
    from src.api.routes import _thin_curve

    curve = [[0.0, 0.5], [2400.0, 0.9]]
    assert _thin_curve(curve) == curve


def _seed_completed_game(env, *, date, espn_id, excitement=5.0):
    """Seed one completed 2026 game (Game + DailyRanking) and return team ids."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Liberty", abbreviation="NY", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Liberty").one().id
    session.add(
        Game(
            team_a_id=a,
            team_b_id=b,
            date=date,
            time="",
            broadcaster="ION",
            winner_id=a,
            final_score_a=88,
            final_score_b=80,
            espn_id=espn_id,
            excitement_index=excitement,
        )
    )
    upsert_daily_ranking(
        session,
        date=date,
        team_a_id=a,
        team_b_id=b,
        quality_score=50.0,
        importance_score=None,
        overall_score=50.0,
        broadcaster="ION",
    )
    session.commit()
    session.close()
    return a, b


def test_completed_endpoint_attaches_shape_curve_when_shape_exists(env, client):
    _seed_completed_game(env, date="2026-05-21", espn_id="e1")
    session = env.get_session()
    upsert_game_shape(
        session,
        espn_id="e1",
        season=2026,
        date="2026-05-21",
        home_team="Aces",
        away_team="Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=80,
        winner="home",
        excitement=5.0,
        tension=0.5,
        comeback=0.2,
        lead_changes=3,
        winner_low_wp=0.3,
        curve=[[0.0, 0.5], [1200.0, 0.4], [2400.0, 0.8]],
    )
    session.commit()
    session.close()

    row = next(
        r for r in client.get("/api/games/completed").json() if r["espn_id"] == "e1"
    )
    assert isinstance(row["shape_curve"], list)
    assert len(row["shape_curve"]) >= 2
    assert row["shape_curve"][0] == [0.0, 0.5]
    assert (
        row["shape_winner"] == "home"
    )  # authoritative winner from the game_shapes row


def test_completed_endpoint_shape_winner_uses_shape_row_not_scores(env, client):
    """The mini's orientation must come from game_shapes.winner, not final scores.
    Seed a shape whose winner ('away') disagrees with the game's final scores
    (home leads 88-80) and assert shape_winner reflects the shape row — so a
    drifted/backfilled row or a null score can't flip the curve upside down
    (Codex adversarial-review round 2)."""
    _seed_completed_game(
        env, date="2026-05-24", espn_id="mw"
    )  # scores: home 88, away 80
    session = env.get_session()
    upsert_game_shape(
        session,
        espn_id="mw",
        season=2026,
        date="2026-05-24",
        home_team="Aces",
        away_team="Liberty",
        home_abbr="LV",
        away_abbr="NY",
        home_score=88,
        away_score=80,
        winner="away",  # deliberately disagrees with the game's final scores
        excitement=5.0,
        tension=0.5,
        comeback=0.2,
        lead_changes=3,
        winner_low_wp=0.3,
        curve=[[0.0, 0.5], [2400.0, 0.8]],
    )
    session.commit()
    session.close()

    row = next(
        r for r in client.get("/api/games/completed").json() if r["espn_id"] == "mw"
    )
    assert row["shape_winner"] == "away"  # from the shape row, NOT the 88>80 scores
    assert isinstance(row["shape_curve"], list)


def test_completed_endpoint_shape_curve_none_without_shape(env, client):
    _seed_completed_game(env, date="2026-05-22", espn_id="e2")

    row = next(
        r for r in client.get("/api/games/completed").json() if r["espn_id"] == "e2"
    )
    assert row["shape_curve"] is None


def test_completed_endpoint_malformed_curve_degrades_to_none(env, client):
    _seed_completed_game(env, date="2026-05-23", espn_id="e3")
    session = env.get_session()
    session.add(
        env.GameShape(
            espn_id="e3",
            season=2026,
            date="2026-05-23",
            home_team="Aces",
            away_team="Liberty",
            home_abbr="LV",
            away_abbr="NY",
            home_score=88,
            away_score=80,
            winner="home",
            excitement=5.0,
            tension=0.5,
            comeback=0.2,
            lead_changes=3,
            winner_low_wp=0.3,
            curve="not-json",
        )
    )
    session.commit()
    session.close()

    resp = client.get("/api/games/completed")
    assert resp.status_code == 200  # one bad row must not 500 the list
    row = next(r for r in resp.json() if r["espn_id"] == "e3")
    assert row["shape_curve"] is None


def test_completed_endpoint_parseable_but_malformed_curves_degrade_to_none(env, client):
    """A JSON-parseable but non-finite / wrong-shaped stored curve must not reach
    the payload as garbage (null points, NaN->null coercion, scalars). Each
    degrades to shape_curve=None with a 200 — the same finite+shape standard as
    the detail panel + /api/replay. Regression for the Codex adversarial-review
    finding that the completed path trusted json.loads success alone."""
    bad = [
        ("bn", "2026-06-01", "[[0.0, NaN], [2400.0, 0.8]]"),  # NaN (json.loads OK)
        ("bi", "2026-06-02", "[[0.0, Infinity], [2400.0, 0.8]]"),  # Infinity
        ("bs", "2026-06-03", "[1, 2]"),  # list of scalars, not [t, pct] pairs
        ("bp", "2026-06-04", "[[0.0, null], [2400.0, 0.8]]"),  # null point value
        ("bx", "2026-06-05", '[["x", "y"], [1.0, 2.0]]'),  # non-numeric points
    ]
    for espn_id, date, curve in bad:
        _seed_completed_game(env, date=date, espn_id=espn_id)
        session = env.get_session()
        session.add(
            env.GameShape(
                espn_id=espn_id,
                season=2026,
                date=date,
                home_team="Aces",
                away_team="Liberty",
                home_abbr="LV",
                away_abbr="NY",
                home_score=88,
                away_score=80,
                winner="home",
                excitement=5.0,
                tension=0.5,
                comeback=0.2,
                lead_changes=3,
                winner_low_wp=0.3,
                curve=curve,
            )
        )
        session.commit()
        session.close()

    resp = client.get("/api/games/completed")
    assert resp.status_code == 200  # no bad row may 500 or emit invalid JSON
    by_id = {r["espn_id"]: r for r in resp.json()}
    for espn_id, _, _ in bad:
        assert by_id[espn_id]["shape_curve"] is None, espn_id


def test_homepage_injects_shape_chart_renderer():
    """buildShapeSvg must reach the homepage so completed-section minis render."""
    from src.api.routes import render_homepage

    assert "function buildShapeSvg" in render_homepage()


def test_completed_minis_placeholder_and_paint_wired():
    """renderGameRow/renderGameCard emit a .shape-mini placeholder and
    renderCompleted fills it via buildShapeSvg, oriented by the authoritative
    game_shapes winner (shape_winner), not the final scores."""
    from src.api.routes import render_homepage

    src = render_homepage()
    assert 'class="shape-mini"' in src  # placeholder emitted
    assert (
        "buildShapeSvg(g.shape_curve, g.shape_winner" in src
    )  # painted; winner from shape
    assert (
        "g.shape_winner === 'home' || g.shape_winner === 'away'" in src
    )  # orientation gate
    # Orientation must NOT be re-derived from final scores (cross-table flip risk).
    assert "g.final_score_a > g.final_score_b ? 'home' : 'away'" not in src
    assert (
        'container.querySelectorAll(`[data-espn-id="${g.espn_id}"] .shape-mini`)' in src
    )  # paints desktop + mobile (both)


def test_completed_section_has_shape_key_and_replay_link():
    from src.api.routes import render_homepage

    src = render_homepage()
    assert 'class="completed-shape-key"' in src
    assert "win-probability swing" in src  # the one-line explainer
    assert (
        'href="/replay">See all' in src
    )  # contextual See-all link (not the footer one)


def test_playoff_odds_endpoint_includes_seed_distribution(env, client):
    """GET /api/playoff-odds carries the per-seed distribution per team."""
    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    a_id = session.query(env.Team).filter_by(name="Aces").one().id
    today = today_et()
    upsert_playoff_probability(
        session,
        date=today,
        team_id=a_id,
        probability=0.85,
        reach_semis_prob=0.60,
        reach_finals_prob=0.40,
        win_championship_prob=0.25,
        seed_distribution=json.dumps({"1": 0.5, "2": 0.35}),
    )
    session.close()

    rows = client.get("/api/playoff-odds").json()
    assert rows[0]["seed_distribution"] == {"1": 0.5, "2": 0.35}


def test_homepage_has_no_playoff_picture(client):
    """The Playoff Picture (toggle, table, Rounds|Seeds view) moved to
    /playoff-odds (PR removing homepage duplication) and must not ship here."""
    html = client.get("/").text
    assert 'id="playoff-view-toggle"' not in html
    assert 'id="playoff-section"' not in html
    assert 'id="playoff-table"' not in html
    assert "Show playoff picture" not in html


# --- /api/playoff-odds live mode -------------------------------------------
#
# The fixture below is built so that ONE settled game decides the 1 seed.
# Aces and Liberty are tied at 6 wins with every other team on 0, and they play
# each other tonight. ESPN reports Liberty already won — but Liberty is the road
# team and 250 Elo points worse, so the Elo-derived probability points the other
# way (~85% Aces). That gap is the whole point: it makes the settled-game
# fold-in *observable* in the published seeding rather than merely present.

_SETTLED_HOME = "Las Vegas Aces"  # tonight's host; the Elo favourite
_SETTLED_AWAY = "New York Liberty"  # tonight's visitor; ESPN says it already won
_LIVE_HOME = "Seattle Storm"
_LIVE_AWAY = "Phoenix Mercury"
_SETTLED_ESPN_ID = "401900001"
_LIVE_ESPN_ID = "401900002"
_LIVE_HOME_PCT = 0.72


def _todays_espn_games():
    """_parse_event-shaped dicts for tonight: one in progress, one already final."""
    today = today_et()
    return [
        {
            "event_id": _LIVE_ESPN_ID,
            "team_a": _LIVE_HOME,
            "team_b": _LIVE_AWAY,
            "date": today,
            "status": "STATUS_IN_PROGRESS",
            "winner_team": None,
            "season_type": 2,
        },
        {
            "event_id": _SETTLED_ESPN_ID,
            "team_a": _SETTLED_HOME,
            "team_b": _SETTLED_AWAY,
            "date": today,
            "status": "STATUS_FINAL",
            "winner_team": _SETTLED_AWAY,
            "season_type": 2,
        },
    ]


def _clear_live_odds_cache():
    import src.api.app as app_module

    with app_module._live_odds_cache_lock:
        app_module._live_odds_cache = None


@pytest.fixture
def seeded_live_slate(env, monkeypatch):
    """15 real teams, a two-game slate tonight, and a stored snapshot to fall
    back to. See the block comment above for why the standings are shaped this
    way."""
    import src.api.app as app_module
    from src.constants import CURRENT_SEASON, TEAM_CONFERENCES
    from src.db.queries import set_team_elo_ratings

    session = env.get_session()
    names = list(TEAM_CONFERENCES)
    for i, name in enumerate(names):
        upsert_team(
            session,
            name=name,
            abbreviation=f"T{i:02d}",
            logo_url="",
            bpi_rating=0.0,
        )
    ids = {t.name: t.id for t in session.query(env.Team).all()}

    # Completed history: Aces 6-0, Liberty 6-0, everyone else 0-1 or 0-0.
    # Seeding sorts on WINS, so no other team can reach the 6/7 win range.
    others = [n for n in names if n not in (_SETTLED_HOME, _SETTLED_AWAY)]
    for winner, victims, tag in (
        (_SETTLED_HOME, others[:6], "A"),
        (_SETTLED_AWAY, others[6:12], "B"),
    ):
        for k, opp in enumerate(victims):
            upsert_game(
                session,
                team_a_id=ids[winner],
                team_b_id=ids[opp],
                date=f"{CURRENT_SEASON}-06-{k + 1:02d}",
                time="7:00 PM ET",
                broadcaster="ESPN",
                winner_id=ids[winner],
                final_score_a=90,
                final_score_b=80,
                espn_id=f"hist{tag}{k}",
                season_type=2,
            )

    today = today_et()
    upsert_game(
        session,
        team_a_id=ids[_SETTLED_HOME],
        team_b_id=ids[_SETTLED_AWAY],
        date=today,
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id=_SETTLED_ESPN_ID,
        season_type=2,
    )
    upsert_game(
        session,
        team_a_id=ids[_LIVE_HOME],
        team_b_id=ids[_LIVE_AWAY],
        date=today,
        time="9:00 PM ET",
        broadcaster="ESPN",
        espn_id=_LIVE_ESPN_ID,
        season_type=2,
    )

    # Distinct Elo per team keeps the tail of the bracket from hanging on the
    # tiebreaker fixed point; the 250-point Aces/Liberty gap is the one that
    # matters (see the block comment).
    ratings = {name: 1300.0 + 5.0 * i for i, name in enumerate(others)}
    ratings[_SETTLED_HOME] = 1700.0
    ratings[_SETTLED_AWAY] = 1450.0
    set_team_elo_ratings(session, ratings)

    # A stored snapshot for today, so "falls back to the stored snapshot" is a
    # real fallback and not an empty response that would pass either way.
    for name in names:
        upsert_playoff_probability(
            session,
            date=today,
            team_id=ids[name],
            probability=0.5,
            reach_semis_prob=0.3,
            reach_finals_prob=0.2,
            win_championship_prob=0.1,
            seed_distribution=json.dumps({"1": 0.125}),
        )
    session.close()

    monkeypatch.setattr(
        app_module, "fetch_games_for_range", lambda *a, **k: _todays_espn_games()
    )
    monkeypatch.setattr(
        app_module, "_detect_live_wps", lambda: {_LIVE_ESPN_ID: _LIVE_HOME_PCT}
    )
    _clear_live_odds_cache()
    yield
    _clear_live_odds_cache()


def test_playoff_odds_serves_live_numbers_when_a_game_is_in_progress(
    client, seeded_live_slate
):
    """Live mode replaces the stored snapshot in place."""
    resp = client.get("/api/playoff-odds")
    assert resp.status_code == 200
    body = resp.json()
    assert body, "live mode must still return rows"
    assert all(row["live"] is True for row in body)
    assert any(row["live_state"] == "live" for row in body)
    # Not the stored snapshot's flat 0.5/0.1 placeholder values.
    assert {row["make_playoffs_prob"] for row in body} != {0.5}


def test_explicit_date_never_goes_live(client, seeded_live_slate):
    """A historical lookup must stay exact — including ?date= equal to today."""
    resp = client.get(f"/api/playoff-odds?date={today_et()}")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows
    assert all(row["live"] is False for row in rows)
    assert all(row["live_state"] is None for row in rows)
    assert all(row["make_playoffs_prob"] == 0.5 for row in rows)


def test_postseason_on_the_slate_disables_live_mode(
    client, seeded_live_slate, monkeypatch
):
    """The bracket runs through bracket_state, not remaining_games — out of
    scope for this overlay, so it must fall back rather than publish a number
    built on the wrong model."""
    monkeypatch.setattr(
        "src.api.app.fetch_games_for_range",
        lambda *a, **k: [{**g, "season_type": 3} for g in _todays_espn_games()],
    )
    rows = client.get("/api/playoff-odds").json()
    assert rows
    assert all(row["live"] is False for row in rows)


def test_missing_team_elo_disables_live_mode(client, env, seeded_live_slate):
    """Between deploy and the first daily run Team.elo_rating is NULL and
    build_sim_inputs falls back to INITIAL_RATING — a coin-flip league. The
    stored snapshot is strictly better than publishing that."""
    session = env.get_session()
    session.query(env.Team).filter_by(name=_LIVE_HOME).one().elo_rating = None
    session.commit()
    session.close()

    rows = client.get("/api/playoff-odds").json()
    assert rows
    assert all(row["live"] is False for row in rows)


def test_scoreboard_failure_degrades_to_the_stored_snapshot(
    client, seeded_live_slate, monkeypatch
):
    """An ESPN outage must not take down a page that has a good number to show."""
    from src.data.espn_api import ESPNAPIError

    def _boom(*args, **kwargs):
        raise ESPNAPIError("scoreboard down")

    monkeypatch.setattr("src.api.app.fetch_games_for_range", _boom)
    resp = client.get("/api/playoff-odds")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows
    assert all(row["live"] is False for row in rows)


def test_unexpected_failure_on_the_live_path_degrades_to_the_snapshot(
    client, seeded_live_slate, monkeypatch
):
    """Anything at all going wrong in the overlay falls back with a 200."""

    def _boom(*args, **kwargs):
        raise RuntimeError("simulation exploded")

    monkeypatch.setattr("src.api.app.run_monte_carlo_simulation", _boom)
    resp = client.get("/api/playoff-odds")
    assert resp.status_code == 200
    assert all(row["live"] is False for row in resp.json())


def test_live_result_is_deterministic_across_calls(client, seeded_live_slate):
    """Same game state must yield byte-identical numbers — the champ column's
    real movement is smaller than 10k sampling jitter."""
    first = client.get("/api/playoff-odds").json()
    _clear_live_odds_cache()  # defeat the response cache
    second = client.get("/api/playoff-odds").json()
    assert first == second
    assert all(row["live"] is True for row in first)


def test_live_mode_writes_nothing(client, env, seeded_live_slate):
    """Read-only: the live overlay must not touch the stored snapshot."""
    from src.db.queries import get_playoff_probabilities

    client.get("/api/playoff-odds")

    session = env.get_session()
    recs = get_playoff_probabilities(session, today_et())
    session.close()
    assert recs
    assert all(r.make_playoffs_prob == 0.5 for r in recs.values())
    assert all(r.win_championship_prob == 0.1 for r in recs.values())


def test_settled_game_absent_from_the_db_changes_the_published_seeding(
    client, seeded_live_slate, monkeypatch
):
    """Deliberate-break guard: drop the settled-game fold-in and this must fail.

    Asserts on the seeding OUTCOME, not merely on the responses differing.
    Removing an override also changes the RNG seed, so a bare `a != b` would
    pass on sampling jitter alone with the fold-in deleted.
    """
    with_fold_in = {r["team"]: r for r in client.get("/api/playoff-odds").json()}
    monkeypatch.setattr(
        "src.api.app.fetch_games_for_range",
        lambda *a, **k: [
            g for g in _todays_espn_games() if g["status"] != "STATUS_FINAL"
        ],
    )
    _clear_live_odds_cache()
    without = {r["team"]: r for r in client.get("/api/playoff-odds").json()}

    # Folded in, Liberty's win is certain and it owns the 1 seed outright.
    assert with_fold_in[_SETTLED_AWAY]["seed_distribution"]["1"] > 0.99
    # Ignored, the game reverts to Elo — which favours the Aces at home.
    assert without[_SETTLED_AWAY]["seed_distribution"]["1"] < 0.30
    assert without[_SETTLED_HOME]["seed_distribution"]["1"] > 0.70


def test_detect_live_wps_reads_the_last_curve_sample(monkeypatch):
    """LiveShape.curve is [[elapsed_seconds, home_pct], ...] — a list of PAIRS,
    not of dicts. downsample_curve always keeps the last sample."""
    import src.api.app as app_module

    monkeypatch.setattr(
        app_module,
        "_detect_live_shapes",
        lambda: (
            [
                {"espn_id": "1", "curve": [[0.0, 0.5], [600.0, 0.63]]},
                {"espn_id": "2", "curve": []},
                {"espn_id": "3", "curve": [[0.0, float("nan")]]},
                {"espn_id": "4"},
            ],
            True,
        ),
    )
    assert app_module._detect_live_wps() == {"1": 0.63}


def test_detect_live_wps_never_raises_on_a_scoreboard_outage(monkeypatch):
    """/api/replay-live 502s because it has nothing else to show; the odds page
    has a stored snapshot, so an outage must degrade to no overrides."""
    import src.api.app as app_module
    from fastapi import HTTPException

    def _boom():
        raise HTTPException(status_code=502, detail="ESPN scoreboard unreachable")

    monkeypatch.setattr(app_module, "_detect_live_shapes", _boom)
    assert app_module._detect_live_wps() == {}


def test_live_mode_is_immune_to_the_global_rng(client, seeded_live_slate, monkeypatch):
    """The live path owns a private random.Random and must not read or write
    process-global RNG state.

    This is not hypothetical: scripts/daily_update seeds the global RNG and runs
    its own multi-second Monte Carlo in THIS process, on the threadpool, whenever
    /internal/daily-update is re-triggered — the documented manual fix-it action.
    Its draws would interleave with a live build.

    Seeding the global *around* the requests would prove nothing: the earlier
    implementation called random.seed() on entry and simply overwrote it. So the
    stand-in for that concurrent run steals a varying number of global draws from
    INSIDE the simulation loop, at a phase that differs between the two builds.
    """
    import random as _random

    from src.scoring import monte_carlo

    real_resolve = monte_carlo.resolve_seeding
    phase = [0]

    def _resolve_stealing_global_draws(standings):
        phase[0] += 1
        for _ in range(phase[0] % 3 + 1):
            _random.random()
        return real_resolve(standings)

    monkeypatch.setattr(monte_carlo, "resolve_seeding", _resolve_stealing_global_draws)

    _random.seed(1)
    first = client.get("/api/playoff-odds").json()
    _clear_live_odds_cache()
    _random.seed(20260910)
    second = client.get("/api/playoff-odds").json()

    assert all(row["live"] is True for row in first)
    assert first == second


def test_settled_game_moves_the_displayed_record_with_the_odds(
    client, seeded_live_slate
):
    """Codex adversarial review: the odds folded tonight's final in as a
    certainty while the Rec column still read from the games table, which has
    no intra-day refresh — so the row published post-result odds beside a
    pre-result W-L. The record must move with the simulation that produced it.
    """
    _clear_live_odds_cache()
    live = {r["team"]: r for r in client.get("/api/playoff-odds").json()}

    _clear_live_odds_cache()
    # Same slate minus the settled game: the baseline the DB alone would show.
    monkey_games = [g for g in _todays_espn_games() if g["status"] != "STATUS_FINAL"]
    import src.api.app as app_module

    original = app_module.fetch_games_for_range
    app_module.fetch_games_for_range = lambda *a, **k: monkey_games
    try:
        without = {r["team"]: r for r in client.get("/api/playoff-odds").json()}
    finally:
        app_module.fetch_games_for_range = original
        _clear_live_odds_cache()

    winner, loser = _SETTLED_AWAY, _SETTLED_HOME
    assert live[winner]["wins"] == without[winner]["wins"] + 1, (
        "the settled winner's W must include tonight's result, like the odds do"
    )
    assert live[loser]["losses"] == without[loser]["losses"] + 1, (
        "the settled loser's L must include tonight's result, like the odds do"
    )
    # An in-progress game has no result — it must not move anyone's record.
    assert live[_LIVE_HOME]["wins"] == without[_LIVE_HOME]["wins"]
    assert live[_LIVE_AWAY]["wins"] == without[_LIVE_AWAY]["wins"]
