"""Tests for ESPN scoreboard event parsing."""

from src.data.espn_api import _parse_event


def _base_event(date: str, time_valid: bool) -> dict:
    """Minimal ESPN event dict shape consumed by _parse_event."""
    return {
        "date": date,
        "season": {"type": 2},
        "competitions": [
            {
                "timeValid": time_valid,
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
        ],
        "id": "401717000",
    }


def test_parse_event_populates_time_utc_when_time_valid():
    event = _base_event("2026-05-21T23:00:00Z", time_valid=True)

    result = _parse_event(event)

    assert result is not None
    assert result["time"] == "7:00 PM ET"
    assert result["time_utc"] == "2026-05-21T23:00:00+00:00"


def test_parse_event_time_utc_none_when_time_tbd():
    # ESPN midnight-UTC + timeValid=False is the TBD sentinel.
    event = _base_event("2026-05-21T00:00:00Z", time_valid=False)

    result = _parse_event(event)

    assert result is not None
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
    # authoritative — don't persist the placeholder.
    event = _base_event("2026-05-22T19:30:00Z", time_valid=False)

    result = _parse_event(event)

    assert result is not None
    assert result["time"] == ""
    assert result["time_utc"] is None
