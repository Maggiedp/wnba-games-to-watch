"""Security response headers are set on every response (PR: pre-share launch hardening)."""

import pytest


@pytest.mark.parametrize(
    "path,status",
    [
        ("/", 200),  # static HTML page
        ("/api/games/upcoming", 200),  # DB-backed JSON API
        ("/game/does-not-exist", 404),  # framework-generated error response
    ],
)
def test_security_headers_on_every_response(env, client, path, status):
    resp = client.get(path)
    assert resp.status_code == status
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    hsts = resp.headers["Strict-Transport-Security"]
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts
