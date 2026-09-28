"""The standings check's mismatch log line is an alerting contract.

GCP log metric `standings-mismatch` matches textPayload=~"Standings disagree
with ESPN", and alert policy 1946887543697430515 pages on it. The literal below
is copied from that filter on purpose: comparing against the constant would
pass after a reword that silently disarms the alert.
"""

_GCP_FILTER_TEXT = "Standings disagree with ESPN"


def test_a_mismatch_logs_the_string_the_alert_policy_matches(monkeypatch, caplog):
    import scripts.daily_update as du

    monkeypatch.setattr(
        du, "fetch_team_records_from_standings", lambda season: {"A": (1, 0)}
    )
    monkeypatch.setattr(du, "get_team_names_with_games", lambda *a, **k: {"A"})

    with caplog.at_level("ERROR"):
        du.check_standings_against_espn(None, {"A": {"wins": 2, "losses": 0}})

    assert _GCP_FILTER_TEXT in caplog.text
