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
