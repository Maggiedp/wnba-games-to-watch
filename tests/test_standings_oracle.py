"""ESPN's standings rollup as an external oracle for our derived records.

The monitoring gap this closes: three consecutive silent degradations raised
no alert because every existing layer watches for a crash or for staleness,
and all three incidents produced data that was fresh, well-formed, and wrong.
ESPN publishes the same aggregate we derive independently, so disagreement is
a defect signal no internal consistency check can provide.
"""

import pytest

from src.data.espn_api import ESPNAPIError, fetch_team_records_from_standings


def _entry(display_name: str, wins: int, losses: int) -> dict:
    return {
        "team": {"displayName": display_name, "abbreviation": display_name[:3]},
        "stats": [
            {"name": "wins", "value": float(wins), "displayValue": str(wins)},
            {"name": "losses", "value": float(losses), "displayValue": str(losses)},
            {"name": "playoffSeed", "value": 1.0, "displayValue": "1"},
        ],
    }


def _payload(*entries: dict) -> dict:
    return {"standings": {"entries": list(entries)}}


def test_parses_wins_and_losses_as_ints_keyed_by_team_name(monkeypatch):
    monkeypatch.setattr(
        "src.data.espn_api._get",
        lambda url, **kw: _payload(
            _entry("Minnesota Lynx", 32, 10), _entry("Seattle Storm", 8, 35)
        ),
    )

    assert fetch_team_records_from_standings(2026) == {
        "Minnesota Lynx": (32, 10),
        "Seattle Storm": (8, 35),
    }


def test_applies_the_same_team_name_canonicalization_as_the_scoreboard(monkeypatch):
    """ESPN sends all-caps variants on some endpoints; the join must survive it.

    Without this the oracle would report a team it cannot match as a defect
    on every single run, which is how a monitor gets muted.
    """
    monkeypatch.setattr(
        "src.data.espn_api._get",
        lambda url, **kw: _payload(_entry("Connecticut SUN", 10, 32)),
    )

    assert fetch_team_records_from_standings(2026) == {"Connecticut Sun": (10, 32)}


def test_finds_entries_nested_under_children(monkeypatch):
    """ESPN nests entries under `children` when not flattened by level=1."""
    monkeypatch.setattr(
        "src.data.espn_api._get",
        lambda url, **kw: {
            "children": [{"standings": {"entries": [_entry("Atlanta Dream", 29, 14)]}}]
        },
    )

    assert fetch_team_records_from_standings(2026) == {"Atlanta Dream": (29, 14)}


def test_raises_when_the_payload_carries_no_usable_entries(monkeypatch):
    """An empty or reshaped payload is the oracle being unusable, NOT every
    team disagreeing. The caller must be able to tell those apart."""
    monkeypatch.setattr("src.data.espn_api._get", lambda url, **kw: {"standings": {}})

    with pytest.raises(ESPNAPIError):
        fetch_team_records_from_standings(2026)


def test_raises_when_an_entry_is_missing_its_record(monkeypatch):
    """A half-parsed rollup would silently shrink the comparison set and let
    a real mismatch through unexamined."""
    monkeypatch.setattr(
        "src.data.espn_api._get",
        lambda url, **kw: _payload(
            {"team": {"displayName": "Indiana Fever"}, "stats": [{"name": "wins"}]}
        ),
    )

    with pytest.raises(ESPNAPIError):
        fetch_team_records_from_standings(2026)


# --- the daily check ---------------------------------------------------------
#
# Three outcomes, deliberately distinct. The alert string is what a GCP
# log-based metric keys on, so which branch emits it IS the contract.


def _standings(**records) -> dict:
    return {
        name: {"wins": w, "losses": lo, "bpi": 0.0, "elo": 1500, "h2h": {}}
        for name, (w, lo) in records.items()
    }


def test_agreement_logs_no_error(monkeypatch, caplog):
    import scripts.daily_update as du

    monkeypatch.setattr(
        du,
        "fetch_team_records_from_standings",
        lambda season: {"Minnesota Lynx": (32, 10), "Seattle Storm": (8, 35)},
    )

    with caplog.at_level("WARNING"):
        du.check_standings_against_espn(
            _standings(**{"Minnesota Lynx": (32, 10), "Seattle Storm": (8, 35)})
        )

    assert du.STANDINGS_MISMATCH_ALERT not in caplog.text


def test_disagreement_emits_the_alert_string_and_names_the_teams(monkeypatch, caplog):
    import scripts.daily_update as du

    monkeypatch.setattr(
        du,
        "fetch_team_records_from_standings",
        lambda season: {"New York Liberty": (26, 17), "Las Vegas Aces": (29, 13)},
    )

    with caplog.at_level("ERROR"):
        du.check_standings_against_espn(
            # Exactly the 2026-09-22 defect: a phantom Cup win for NY, and a
            # phantom Cup loss plus a stranded game for LV.
            _standings(**{"New York Liberty": (27, 17), "Las Vegas Aces": (28, 14)})
        )

    assert du.STANDINGS_MISMATCH_ALERT in caplog.text
    assert "New York Liberty" in caplog.text
    assert "Las Vegas Aces" in caplog.text
    assert "27-17" in caplog.text and "26-17" in caplog.text


def test_a_team_espn_has_that_we_do_not_is_alerted_not_skipped(monkeypatch, caplog):
    """Lost evidence is not absent evidence.

    Silently skipping a team we cannot join is how this check would become
    vacuous: a rename upstream would disable it while it kept reporting OK.
    """
    import scripts.daily_update as du

    monkeypatch.setattr(
        du,
        "fetch_team_records_from_standings",
        lambda season: {"Minnesota Lynx": (32, 10), "Portland Fire": (16, 27)},
    )

    with caplog.at_level("ERROR"):
        du.check_standings_against_espn(_standings(**{"Minnesota Lynx": (32, 10)}))

    assert du.STANDINGS_MISMATCH_ALERT in caplog.text
    assert "Portland Fire" in caplog.text


def test_oracle_unavailable_warns_and_does_not_alert(monkeypatch, caplog):
    """An ESPN outage must not page as a data defect.

    Accepted cost, stated plainly: during an ESPN outage the oracle is blind
    exactly when ingest is most likely broken, so a divergence is caught the
    following day rather than instantly.
    """
    import scripts.daily_update as du

    def boom(season):
        raise ESPNAPIError("ESPN down")

    monkeypatch.setattr(du, "fetch_team_records_from_standings", boom)

    with caplog.at_level("WARNING"):
        du.check_standings_against_espn(_standings(**{"Minnesota Lynx": (32, 10)}))

    assert du.STANDINGS_MISMATCH_ALERT not in caplog.text
    assert "could not run" in caplog.text.lower()


def test_teams_with_no_espn_row_are_ignored(monkeypatch, caplog):
    """Our standings dict seeds every known team at 0-0, including one ESPN
    has not listed yet (an expansion club before its first season). That is
    not a disagreement — only teams ESPN actually reports are compared."""
    import scripts.daily_update as du

    monkeypatch.setattr(
        du,
        "fetch_team_records_from_standings",
        lambda season: {"Minnesota Lynx": (32, 10)},
    )

    with caplog.at_level("ERROR"):
        du.check_standings_against_espn(
            _standings(**{"Minnesota Lynx": (32, 10), "Future Expansion": (0, 0)})
        )

    assert du.STANDINGS_MISMATCH_ALERT not in caplog.text


def test_a_truncated_espn_payload_alerts_instead_of_reporting_success(
    monkeypatch, caplog
):
    """A well-formed but PARTIAL rollup must not read as a passing check.

    The parser only fails closed on zero entries or a malformed one, so a
    degraded ESPN response carrying a single valid team would otherwise be
    accepted and logged as "all 1 teams agree" — leaving fourteen teams
    unvalidated while the monitor reported healthy. Same vacuous-monitor
    failure as an unjoinable team, approached from the other side.
    """
    import scripts.daily_update as du

    monkeypatch.setattr(
        du,
        "fetch_team_records_from_standings",
        lambda season: {"Minnesota Lynx": (32, 10)},
    )

    with caplog.at_level("ERROR"):
        du.check_standings_against_espn(
            _standings(
                **{
                    "Minnesota Lynx": (32, 10),
                    "Las Vegas Aces": (29, 13),
                    "Seattle Storm": (8, 35),
                }
            )
        )

    assert du.STANDINGS_MISMATCH_ALERT in caplog.text
    assert "Las Vegas Aces" in caplog.text
    assert "Seattle Storm" in caplog.text
    # And it must not simultaneously claim success. Matched on the success
    # log's own prefix, not on "agree with ESPN" — that is a substring of
    # "disagree with ESPN" and the assertion would fire on the alert itself.
    assert "Standings check: all" not in caplog.text


def test_coverage_rule_is_self_calibrating_not_a_team_count(monkeypatch, caplog):
    """No expected-team-count constant.

    A hardcoded league size goes stale the day the league expands and then
    alerts every night until someone edits it — the exact "trains the reader
    to mute it" failure this layer exists to avoid. The rule keys on whether
    ESPN covers the teams we actually make claims about, so a brand-new club
    sitting at 0-0 before ESPN lists it stays silent while a real record
    going uncovered does not.
    """
    import scripts.daily_update as du

    monkeypatch.setattr(
        du,
        "fetch_team_records_from_standings",
        lambda season: {"Minnesota Lynx": (32, 10)},
    )

    with caplog.at_level("ERROR"):
        du.check_standings_against_espn(
            _standings(**{"Minnesota Lynx": (32, 10), "Expansion Club": (0, 0)})
        )

    assert du.STANDINGS_MISMATCH_ALERT not in caplog.text
