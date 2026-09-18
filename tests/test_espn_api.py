"""Tests for ESPN scoreboard event parsing."""

from src.data.espn_api import _parse_event


def _base_event(date: str, time_valid: bool | None = True) -> dict:
    """Minimal ESPN event dict shape consumed by _parse_event.

    Pass `time_valid=None` to omit the `timeValid` key entirely
    (models ESPN payloads where the optional flag is absent).
    """
    competition: dict = {
        "competitors": [
            {
                "homeAway": "home",
                "team": {"displayName": "Connecticut Sun"},
                "score": "0",
            },
            {
                "homeAway": "away",
                "team": {"displayName": "New York Liberty"},
                "score": "0",
            },
        ],
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "broadcasts": [],
    }
    if time_valid is not None:
        competition["timeValid"] = time_valid
    return {
        "date": date,
        "season": {"type": 2},
        "competitions": [competition],
        "id": "401717000",
    }


def test_parse_event_populates_time_utc_when_time_valid():
    event = _base_event("2026-05-21T23:00:00Z", time_valid=True)

    result = _parse_event(event)

    assert result is not None
    assert result["time"] == "7:00 PM ET"
    assert result["time_utc"] == "2026-05-21T23:00:00+00:00"


def test_parse_event_time_utc_none_when_time_tbd():
    # ESPN's canonical TBD sentinel: midnight UTC of the scheduled ET
    # date plus timeValid=False. The UTC calendar component IS the
    # intended game date; we must NOT TZ-shift it (would silently move
    # the row to the previous ET day).
    event = _base_event("2026-05-21T00:00:00Z", time_valid=False)

    result = _parse_event(event)

    assert result is not None
    assert result["date"] == "2026-05-21"
    assert result["time"] == ""
    assert result["time_utc"] is None


def test_parse_event_populates_time_utc_when_genuine_midnight_et():
    # 4 AM UTC = 12 AM (midnight) ET — legitimately late tip-off.
    # timeValid=True must override the midnight-UTC sentinel check.
    event = _base_event("2026-05-22T04:00:00Z", time_valid=True)

    result = _parse_event(event)

    assert result is not None
    assert result["time"] == "12:00 AM ET"
    assert result["time_utc"] == "2026-05-22T04:00:00+00:00"


def test_parse_event_time_utc_none_when_time_invalid_with_non_midnight_timestamp():
    # ESPN occasionally leaves a non-midnight placeholder in `date`
    # while flagging timeValid=False (schedule correction). The flag is
    # authoritative — don't persist the placeholder, and use the UTC
    # calendar date directly (no TZ shift on a meaningless time-of-day).
    event = _base_event("2026-05-22T19:30:00Z", time_valid=False)

    result = _parse_event(event)

    assert result is not None
    assert result["date"] == "2026-05-22"
    assert result["time"] == ""
    assert result["time_utc"] is None


def test_parse_event_preserves_time_when_time_valid_missing_with_real_timestamp():
    # ESPN sometimes omits `timeValid` entirely. A non-midnight UTC
    # timestamp in that case is still a real tip time — don't clear it.
    # Regression for schema drift wiping live/completed game times.
    event = _base_event("2026-05-21T23:00:00Z", time_valid=None)

    result = _parse_event(event)

    assert result is not None
    assert result["date"] == "2026-05-21"
    assert result["time"] == "7:00 PM ET"
    assert result["time_utc"] == "2026-05-21T23:00:00+00:00"


def test_parse_event_treats_missing_time_valid_at_midnight_utc_as_tbd():
    # Legacy fallback: ESPN's pre-timeValid TBD convention was midnight
    # UTC of the scheduled ET date. If the flag is absent AND the
    # timestamp is midnight UTC, treat as TBD (mirrors pre-PR behavior).
    event = _base_event("2026-05-21T00:00:00Z", time_valid=None)

    result = _parse_event(event)

    assert result is not None
    assert result["date"] == "2026-05-21"
    assert result["time"] == ""
    assert result["time_utc"] is None


# --- scoreboard date parameter (ESPN dropped the range format 2026-09-16) ---


def _range_fetch(monkeypatch, start, end, events_by_param=None):
    """Run fetch_games_for_range with _get stubbed; return (games, params)."""
    from src.data import espn_api

    params: list[str] = []

    def fake_get(url, **kwargs):
        param = kwargs.get("dates", "")
        params.append(param)
        return {"events": (events_by_param or {}).get(param, [])}

    monkeypatch.setattr(espn_api, "_get", fake_get)
    monkeypatch.setattr(
        espn_api,
        "fetch_team_id_map",
        lambda: {"1": "Connecticut Sun", "2": "New York Liberty"},
    )
    games = espn_api.fetch_games_for_range(start, end)
    return games, params


def test_scoreboard_is_queried_by_month_not_by_date_range(monkeypatch):
    """ESPN began 400ing dates=YYYYMMDD-YYYYMMDD on 2026-09-16.

    A range param silently returned zero games for every window, which took the
    daily job down without tripping any alert.
    """
    from datetime import date

    _, params = _range_fetch(monkeypatch, date(2026, 8, 15), date(2026, 9, 17))

    assert params == ["202608", "202609"]
    assert not any("-" in p for p in params)


def test_single_month_window_queries_that_month_once(monkeypatch):
    from datetime import date

    _, params = _range_fetch(monkeypatch, date(2026, 9, 1), date(2026, 9, 17))

    assert params == ["202609"]


def test_events_outside_the_requested_window_are_dropped(monkeypatch):
    """A month query returns the WHOLE month, so the window filter moves
    client-side. Without it, a bounded caller would silently widen."""
    from datetime import date

    events = [
        _base_event("2026-09-05T23:00:00Z"),  # before start
        _base_event("2026-09-17T23:00:00Z"),  # inside
        _base_event("2026-09-28T23:00:00Z"),  # after end
    ]
    for i, e in enumerate(events):
        e["id"] = f"40170000{i}"

    games, _ = _range_fetch(
        monkeypatch,
        date(2026, 9, 10),
        date(2026, 9, 20),
        events_by_param={"202609": events},
    )

    assert [g["date"] for g in games] == ["2026-09-17"]


def test_a_game_returned_by_a_neighbouring_month_still_counts(monkeypatch):
    """Filtering is against the OVERALL window, not the per-month sub-window,
    so a late-night game ESPN buckets into the next month is not lost."""
    from datetime import date

    sept = _base_event("2026-09-30T23:30:00Z")
    sept["id"] = "401700010"
    oct_bucket = _base_event("2026-09-30T23:30:00Z")  # same ET date, Oct bucket
    oct_bucket["id"] = "401700010"

    games, params = _range_fetch(
        monkeypatch,
        date(2026, 9, 1),
        date(2026, 10, 31),
        events_by_param={"202609": [], "202610": [oct_bucket]},
    )

    assert params == ["202609", "202610"]
    assert [g["date"] for g in games] == ["2026-09-30"]
