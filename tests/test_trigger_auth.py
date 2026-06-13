"""Auth boundary tests for the /internal/daily-update write endpoint.

The endpoint runs the daily job, so its only protection is the
X-Trigger-Secret header. These tests pin the auth behavior without
running the real job (scripts.daily_update.main is stubbed).
"""


def _stub_main(monkeypatch):
    """Replace the daily-update job with a call-recording no-op.

    Returns the call log so a test can assert whether the job was reached.
    """
    calls = []

    def fake_main():
        calls.append(True)
        return 0

    monkeypatch.setattr("scripts.daily_update.main", fake_main)
    return calls


def test_rejects_when_secret_unconfigured(client, monkeypatch):
    """Fail closed: an empty/unset TRIGGER_SECRET must reject the request,
    not run the job unauthenticated."""
    monkeypatch.setattr("src.api.app._TRIGGER_SECRET", "")
    calls = _stub_main(monkeypatch)

    resp = client.post("/internal/daily-update")

    assert resp.status_code == 403
    assert calls == [], "job must not run when no secret is configured"


def test_rejects_wrong_secret(client, monkeypatch):
    """A configured secret rejects a mismatched header."""
    monkeypatch.setattr("src.api.app._TRIGGER_SECRET", "right-secret")
    calls = _stub_main(monkeypatch)

    resp = client.post(
        "/internal/daily-update", headers={"X-Trigger-Secret": "wrong-secret"}
    )

    assert resp.status_code == 403
    assert calls == []


def test_accepts_correct_secret(client, monkeypatch):
    """A configured secret runs the job when the header matches."""
    monkeypatch.setattr("src.api.app._TRIGGER_SECRET", "right-secret")
    calls = _stub_main(monkeypatch)

    resp = client.post(
        "/internal/daily-update", headers={"X-Trigger-Secret": "right-secret"}
    )

    assert resp.status_code == 200
    assert calls == [True]
