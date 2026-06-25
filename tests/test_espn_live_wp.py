"""Tests for ESPN live win probability fetch functions."""

from unittest.mock import patch

import pytest

from src.data.espn_api import (
    ESPNAPIError,
    ESPNNotFoundError,
    fetch_live_win_probability,
    fetch_today_game_statuses,
)


def _make_summary(
    status="STATUS_FINAL",
    home="Dallas Wings",
    away="Atlanta Dream",
    plays=None,
    wp=None,
):
    if plays is None:
        plays = [
            {
                "id": "4018569010",
                "period": {"number": 1},
                "clock": {"displayValue": "10:00"},
            },
            {
                "id": "4018569015",
                "period": {"number": 2},
                "clock": {"displayValue": "8:30"},
            },
        ]
    if wp is None:
        wp = [
            {"playId": "4018569010", "homeWinPercentage": 0.5, "tiePercentage": 0.0},
            {"playId": "4018569015", "homeWinPercentage": 0.6, "tiePercentage": 0.0},
        ]
    return {
        "header": {
            "competitions": [
                {
                    "status": {"type": {"name": status}},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": home}},
                        {"homeAway": "away", "team": {"displayName": away}},
                    ],
                }
            ]
        },
        "plays": plays,
        "winprobability": wp,
    }


def test_fetch_live_win_probability_parses_status_and_teams():
    with patch("src.data.espn_api._get", return_value=_make_summary()):
        result = fetch_live_win_probability("401856901")
    assert result["status"] == "STATUS_FINAL"
    assert result["home_team"] == "Dallas Wings"
    assert result["away_team"] == "Atlanta Dream"
    assert result["espn_id"] == "401856901"


def test_fetch_live_win_probability_builds_plays_list():
    with patch("src.data.espn_api._get", return_value=_make_summary()):
        result = fetch_live_win_probability("401856901")
    plays = result["plays"]
    assert len(plays) == 2
    assert plays[0] == {"seq": 0, "period": 1, "clock": "10:00", "home_pct": 0.5}
    assert plays[1] == {"seq": 1, "period": 2, "clock": "8:30", "home_pct": 0.6}


def test_fetch_live_win_probability_empty_wp_returns_empty_plays():
    summary = _make_summary(wp=[])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert result["plays"] == []


def test_fetch_live_win_probability_propagates_espn_error():
    with patch("src.data.espn_api._get", side_effect=ESPNAPIError("timeout")):
        with pytest.raises(ESPNAPIError):
            fetch_live_win_probability("401856901")


def test_fetch_live_win_probability_drops_sample_with_unmatched_play_id():
    # An unmatched playId has no resolvable game clock, so the sample can't be
    # placed on the time axis the curve + excitement/tension/lead-change metrics
    # all assume -> drop it (like an invalid home_pct) rather than keep it with a
    # fabricated clock that would mis-sort and mis-weight it.
    summary = _make_summary(
        plays=[
            {
                "id": "4018569010",
                "period": {"number": 1},
                "clock": {"displayValue": "10:00"},
            }
        ],
        wp=[{"playId": "UNKNOWN", "homeWinPercentage": 0.4, "tiePercentage": 0.0}],
    )
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert result["plays"] == []


def test_fetch_live_win_probability_sorts_plays_by_game_time():
    """ESPN lists a few winprobability entries out of game-time order; the
    fever-line curve plots x by elapsed time (and excitement/lead-change
    metrics assume time order), so fetch must return plays time-sorted. Ties
    (same clock) keep their original relative order (stable)."""
    from src.scoring.excitement import elapsed_seconds

    plays = [
        {"id": "a", "period": {"number": 1}, "clock": {"displayValue": "10:00"}},
        {"id": "b", "period": {"number": 1}, "clock": {"displayValue": "2:00"}},
        {"id": "c", "period": {"number": 1}, "clock": {"displayValue": "5:00"}},
        {"id": "d", "period": {"number": 1}, "clock": {"displayValue": "2:00"}},
    ]
    # winprobability lists b (2:00, late) before c (5:00, earlier) -> out of
    # order; d ties b at 2:00 and must stay after it (stable).
    wp = [
        {"playId": "a", "homeWinPercentage": 0.50},
        {"playId": "b", "homeWinPercentage": 0.70},
        {"playId": "c", "homeWinPercentage": 0.60},
        {"playId": "d", "homeWinPercentage": 0.72},
    ]
    summary = _make_summary(plays=plays, wp=wp)
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    got = result["plays"]
    # game-time order: 10:00 (0s), 5:00 (300s), 2:00/b (480s), 2:00/d (480s)
    assert [p["clock"] for p in got] == ["10:00", "5:00", "2:00", "2:00"]
    assert [p["home_pct"] for p in got] == [0.50, 0.60, 0.70, 0.72]
    el = [elapsed_seconds(p) for p in got]
    assert el == sorted(el)  # non-decreasing
    assert [p["seq"] for p in got] == [0, 1, 2, 3]  # resequenced over final order


def test_fetch_live_win_probability_drops_unmatched_and_sorts_the_rest():
    """A single unmatched playId must NOT disable time-ordering for the valid
    samples (the all-or-nothing gate would have): drop the unmatched one and
    still sort the rest by game time."""
    plays = [
        {"id": "a", "period": {"number": 1}, "clock": {"displayValue": "10:00"}},
        {"id": "b", "period": {"number": 4}, "clock": {"displayValue": "1:00"}},
    ]
    # feed lists b (late) first, then an unmatched sample, then a (early)
    wp = [
        {"playId": "b", "homeWinPercentage": 0.80},
        {"playId": "UNMATCHED", "homeWinPercentage": 0.40},
        {"playId": "a", "homeWinPercentage": 0.50},
    ]
    summary = _make_summary(plays=plays, wp=wp)
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    # unmatched (0.40) dropped; the rest sorted by game time: a (10:00, 0s) then
    # b (Q4 1:00, 2340s).
    assert [p["home_pct"] for p in result["plays"]] == [0.50, 0.80]
    assert [p["seq"] for p in result["plays"]] == [0, 1]


def test_fetch_live_win_probability_drops_matched_play_missing_period_or_clock():
    # A matched playId whose play entry lacks a period/clock has no trustworthy
    # game-time, so it must be dropped (not kept with a fabricated 1/"" default
    # the sort would then trust) — same treatment as an unmatched playId.
    plays = [
        {"id": "a", "period": {"number": 1}, "clock": {"displayValue": "10:00"}},
        {"id": "b"},  # matched id but no period/clock fields
    ]
    wp = [
        {"playId": "a", "homeWinPercentage": 0.55},
        {"playId": "b", "homeWinPercentage": 0.60},
    ]
    summary = _make_summary(plays=plays, wp=wp)
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert [p["home_pct"] for p in result["plays"]] == [0.55]  # b dropped


def _summary_with_wp(pcts):
    """Build a summary whose winprobability entries carry the given
    raw `homeWinPercentage` values (any type), one play per value."""
    ids = [f"p{i}" for i in range(len(pcts))]
    plays = [
        {"id": pid, "period": {"number": 1}, "clock": {"displayValue": "10:00"}}
        for pid in ids
    ]
    wp = [{"playId": pid, "homeWinPercentage": v} for pid, v in zip(ids, pcts)]
    return _make_summary(plays=plays, wp=wp)


def _home_pcts(result):
    return [p["home_pct"] for p in result["plays"]]


def test_null_home_pct_is_dropped():
    summary = _summary_with_wp([0.3, None, 0.8])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert _home_pcts(result) == [0.3, 0.8]


def test_string_home_pct_is_dropped():
    summary = _summary_with_wp([0.3, "garbage", 0.8])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert _home_pcts(result) == [0.3, 0.8]


def test_nan_home_pct_is_dropped():
    summary = _summary_with_wp([0.3, float("nan"), 0.8])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert _home_pcts(result) == [0.3, 0.8]


def test_out_of_range_home_pct_is_dropped():
    summary = _summary_with_wp([0.3, 1.5, 0.8])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert _home_pcts(result) == [0.3, 0.8]


def test_kept_plays_are_resequenced_after_drops():
    summary = _summary_with_wp([0.3, None, 0.8])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert [p["seq"] for p in result["plays"]] == [0, 1]


def test_all_invalid_home_pct_yields_empty_plays():
    summary = _summary_with_wp([None, "x", float("inf")])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert result["plays"] == []


def test_valid_boundary_values_zero_and_one_are_kept():
    summary = _summary_with_wp([0.0, 1.0])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert _home_pcts(result) == [0.0, 1.0]


def test_single_valid_sample_leaves_excitement_none_for_retry():
    """Regression (Codex): a FINAL feed with only one *real* WP sample must
    not be turned into a stored excitement score. Dropping (not carrying
    forward) keeps play count == real-sample count, so compute_excitement's
    len>=2 guard returns None → daily_update leaves excitement_index NULL
    and retries, instead of permanently archiving a fabricated 0.0."""
    from src.scoring.excitement import compute_excitement

    summary = _summary_with_wp([0.3, None])
    with patch("src.data.espn_api._get", return_value=summary):
        result = fetch_live_win_probability("401856901")
    assert len(result["plays"]) == 1
    assert compute_excitement(result["plays"], final=True) is None


def _make_scoreboard(*games):
    """games: list of (event_id, status_name) tuples."""
    return {
        "events": [
            {
                "id": eid,
                "competitions": [{"status": {"type": {"name": status}}}],
            }
            for eid, status in games
        ]
    }


def test_fetch_today_game_statuses_returns_status_by_espn_id():
    sb = _make_scoreboard(
        ("401856901", "STATUS_FINAL"),
        ("401856902", "STATUS_IN_PROGRESS"),
        ("401856903", "STATUS_SCHEDULED"),
    )
    with patch("src.data.espn_api._get", return_value=sb):
        result = fetch_today_game_statuses("2026-05-13")
    assert result == {
        "401856901": "STATUS_FINAL",
        "401856902": "STATUS_IN_PROGRESS",
        "401856903": "STATUS_SCHEDULED",
    }


def test_fetch_today_game_statuses_raises_on_error():
    import pytest

    with patch("src.data.espn_api._get", side_effect=ESPNAPIError("down")):
        with pytest.raises(ESPNAPIError):
            fetch_today_game_statuses("2026-05-13")


def test_fetch_today_game_statuses_passes_correct_date_param():
    with patch("src.data.espn_api._get", return_value={"events": []}) as mock_get:
        fetch_today_game_statuses("2026-05-13")
    mock_get.assert_called_once_with(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
        timeout=5,
        dates="20260513",
    )


def test_get_raises_not_found_error_on_espn_404():
    """_get raises ESPNNotFoundError when ESPN returns HTTP 404."""
    import requests as req_lib
    from unittest.mock import MagicMock
    from src.data.espn_api import _get

    mock_response = MagicMock()
    mock_response.status_code = 404
    http_error = req_lib.HTTPError(response=mock_response)
    mock_response.raise_for_status.side_effect = http_error
    with patch("requests.get", return_value=mock_response):
        with pytest.raises(ESPNNotFoundError):
            _get("http://example.com/api")


def test_today_et_returns_et_date_after_utc_midnight_rollover():
    import src.data.espn_api as m
    from tests.conftest import frozen_datetime_class, utc

    with patch.object(m, "datetime", frozen_datetime_class(utc(2026, 5, 19, 3, 30))):
        assert m.today_et() == "2026-05-18"
