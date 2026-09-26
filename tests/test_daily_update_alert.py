"""The daily job's failure log line is an alerting contract.

GCP log metric `daily-update-failure` matches textPayload=~"Daily update job
failed", and alert policy 3847624768262173145 pages on it. The literal below is
copied from that filter on purpose: comparing against the constant would pass
after a reword that silently disarms the alert.
"""

_GCP_FILTER_TEXT = "Daily update job failed"


def test_a_failed_run_logs_the_string_the_alert_policy_matches(monkeypatch, caplog):
    import scripts.daily_update as du

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(du, "init_db", boom)

    with caplog.at_level("ERROR"):
        assert du.main() == 1

    assert _GCP_FILTER_TEXT in caplog.text
