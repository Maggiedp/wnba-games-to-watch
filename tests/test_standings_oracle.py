"""ESPN's standings rollup as an external oracle for our derived records.

The monitoring gap this closes: three consecutive silent degradations raised
no alert because every existing layer watches for a crash or for staleness,
and all three incidents produced data that was fresh, well-formed, and wrong.
ESPN publishes the same aggregate we derive independently, so disagreement is
a defect signal no internal consistency check can provide.
"""

import pytest

from src.data.espn_api import ESPNAPIError, fetch_team_records_from_standings


# The ids our own teams table is keyed from — patched in below so no test
# reaches ESPN's /teams for them.
_ID_MAP = {5: "Minnesota Lynx", 14: "Seattle Storm", 18: "Connecticut Sun"}


@pytest.fixture(autouse=True)
def _stub_team_id_map(monkeypatch):
    monkeypatch.setattr("src.data.espn_api.fetch_team_id_map", lambda: dict(_ID_MAP))


def _entry(
    display_name: str, wins: int, losses: int, team_id: int | None = None
) -> dict:
    team: dict = {"displayName": display_name, "abbreviation": display_name[:3]}
    if team_id is not None:
        team["id"] = str(team_id)
    return {
        "team": team,
        "stats": [
            {"name": "wins", "value": float(wins), "displayValue": str(wins)},
            {"name": "losses", "value": float(losses), "displayValue": str(losses)},
            {"name": "playoffSeed", "value": 1.0, "displayValue": "1"},
        ],
    }


def _payload(*entries: dict) -> dict:
    return {"standings": {"entries": list(entries)}}


def test_resolves_team_names_by_id_not_by_display_name(monkeypatch):
    """Join on `team.id` through the same /teams map our teams table is built
    from, so the key matches by construction.

    `src/data/CLAUDE.md` records that PR #106 moved cross-endpoint matching
    OFF displayName precisely because capitalization varies by endpoint
    ("Connecticut SUN"). A name that drifted here would mismatch every night
    — a false alert, which is the muting failure this whole layer exists to
    avoid. The display name below is deliberately wrong to prove the id wins.
    """
    monkeypatch.setattr(
        "src.data.espn_api._get",
        lambda url, **kw: _payload(_entry("MINNESOTA LYNX!!", 32, 10, team_id=5)),
    )

    assert fetch_team_records_from_standings(2026) == {"Minnesota Lynx": (32, 10)}


def test_falls_back_to_the_display_name_when_the_id_does_not_resolve(monkeypatch):
    """An id we do not carry must not silence the team.

    Falling back keeps behaviour identical to the pre-id version for this
    case: the name either matches our standings, or it does not and the
    check reports an unjoinable team. Raising instead would let one
    unknown id take the whole oracle down, which is a vacuousness hole.
    """
    monkeypatch.setattr(
        "src.data.espn_api._get",
        lambda url, **kw: _payload(_entry("Atlanta Dream", 29, 14, team_id=9999)),
    )

    assert fetch_team_records_from_standings(2026) == {"Atlanta Dream": (29, 14)}


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


# --- the comparison ----------------------------------------------------------
#
# `_standings_problems` is pure: three plain collections in, strings out. No
# session, no patched fetch. Only the outage test below needs the I/O shell.


def _ours(records: dict) -> dict:
    """Our standings shape, as `compute_standings` returns it."""
    return {
        name: {"wins": w, "losses": lo, "bpi": 0.0, "elo": 1500, "h2h": {}}
        for name, (w, lo) in records.items()
    }


def test_agreement_yields_no_problems():
    from scripts.daily_update import _standings_problems

    ours = _ours({"Minnesota Lynx": (32, 10), "Seattle Storm": (8, 35)})
    espn = {"Minnesota Lynx": (32, 10), "Seattle Storm": (8, 35)}

    assert _standings_problems(ours, espn, set(ours)) == []


def test_a_differing_record_is_reported_with_both_sides():
    """Exactly the 2026-09-22 defect: a phantom Cup win for New York, and a
    phantom Cup loss plus a stranded game for Las Vegas."""
    from scripts.daily_update import _standings_problems

    ours = _ours({"New York Liberty": (27, 17), "Las Vegas Aces": (28, 14)})
    espn = {"New York Liberty": (26, 17), "Las Vegas Aces": (29, 13)}

    problems = _standings_problems(ours, espn, set(ours))

    assert len(problems) == 2
    assert "New York Liberty: we have 27-17, ESPN has 26-17" in problems
    assert "Las Vegas Aces: we have 28-14, ESPN has 29-13" in problems


def test_a_team_espn_has_that_we_do_not_is_reported_not_skipped():
    """Lost evidence is not absent evidence.

    Silently skipping a team we cannot join is how this check would become
    vacuous: a rename upstream would disable it while it kept reporting OK.
    """
    from scripts.daily_update import _standings_problems

    ours = _ours({"Minnesota Lynx": (32, 10)})
    espn = {"Minnesota Lynx": (32, 10), "Portland Fire": (16, 27)}

    problems = _standings_problems(ours, espn, set(ours))

    assert problems == ["Portland Fire: ESPN has 16-27, we have no such team"]


def test_a_truncated_espn_payload_is_reported_for_every_omitted_team():
    """A well-formed but PARTIAL rollup must not read as a passing check.

    The parser only fails closed on zero entries or a malformed one, so a
    degraded response carrying a single valid team would otherwise be
    accepted and logged as "all 1 teams agree" -- leaving the rest
    unvalidated while the monitor reported healthy.
    """
    from scripts.daily_update import _standings_problems

    ours = _ours(
        {
            "Minnesota Lynx": (32, 10),
            "Las Vegas Aces": (29, 13),
            "Seattle Storm": (8, 35),
        }
    )

    problems = _standings_problems(ours, {"Minnesota Lynx": (32, 10)}, set(ours))

    assert len(problems) == 2
    assert any("Las Vegas Aces" in p for p in problems)
    assert any("Seattle Storm" in p for p in problems)


def test_a_scheduled_team_omitted_by_espn_is_reported_even_at_0_0():
    """The blind spot a non-zero-record rule leaves open.

    `compute_standings` seeds every known team at 0-0 before applying
    results, so a team whose games all failed to ingest sits at 0-0 locally.
    If ESPN's rollup also omits it, a rule keyed on "we have a real record"
    never fires and that team goes unvalidated -- the same vacuous-monitor
    failure, one layer deeper. Keyed on schedule presence instead.
    """
    from scripts.daily_update import _standings_problems

    ours = _ours({"Minnesota Lynx": (32, 10), "Seattle Storm": (0, 0)})

    problems = _standings_problems(ours, {"Minnesota Lynx": (32, 10)}, set(ours))

    assert len(problems) == 1
    assert "Seattle Storm" in problems[0]
    assert "0-0" in problems[0]


def test_a_team_with_no_schedule_is_not_expected_in_the_rollup():
    """Self-calibrating, with no expected-team-count constant.

    A hardcoded league size goes stale the day the league expands and then
    alerts nightly until someone edits it -- the "trains the reader to mute
    it" failure this layer exists to avoid. Schedule presence needs no
    expansion-team exception: a brand-new club sitting at 0-0 before its
    schedule drops is simply not expected yet.
    """
    from scripts.daily_update import _standings_problems

    ours = _ours({"Minnesota Lynx": (32, 10), "Expansion Club": (0, 0)})

    problems = _standings_problems(
        ours, {"Minnesota Lynx": (32, 10)}, {"Minnesota Lynx"}
    )

    assert problems == []


# --- the I/O and log-policy shell --------------------------------------------


def test_disagreement_emits_the_alert_string(monkeypatch, caplog):
    """The alert string is what a GCP log-based metric keys on, so which
    branch emits it IS the contract."""
    import scripts.daily_update as du

    monkeypatch.setattr(
        du, "fetch_team_records_from_standings", lambda season: {"A": (1, 0)}
    )
    monkeypatch.setattr(du, "get_team_names_with_games", lambda *a, **k: {"A"})

    with caplog.at_level("ERROR"):
        du.check_standings_against_espn(None, _ours({"A": (2, 0)}))

    assert du.STANDINGS_MISMATCH_ALERT in caplog.text


def test_agreement_logs_success_without_the_alert_string(monkeypatch, caplog):
    import scripts.daily_update as du

    monkeypatch.setattr(
        du, "fetch_team_records_from_standings", lambda season: {"A": (1, 0)}
    )
    monkeypatch.setattr(du, "get_team_names_with_games", lambda *a, **k: {"A"})

    with caplog.at_level("INFO"):
        du.check_standings_against_espn(None, _ours({"A": (1, 0)}))

    assert du.STANDINGS_MISMATCH_ALERT not in caplog.text
    assert "Standings check: all 1 teams agree" in caplog.text


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
        du.check_standings_against_espn(None, _ours({"A": (1, 0)}))

    assert du.STANDINGS_MISMATCH_ALERT not in caplog.text
    assert "could not run" in caplog.text.lower()


def test_the_coverage_query_is_not_consulted_when_the_oracle_is_down(monkeypatch):
    """Bail before touching the DB -- there is nothing to compare against."""
    import scripts.daily_update as du

    def boom(season):
        raise ESPNAPIError("ESPN down")

    def must_not_run(*a, **k):
        raise AssertionError("coverage query ran with no oracle to compare to")

    monkeypatch.setattr(du, "fetch_team_records_from_standings", boom)
    monkeypatch.setattr(du, "get_team_names_with_games", must_not_run)

    du.check_standings_against_espn(None, _ours({"A": (1, 0)}))
