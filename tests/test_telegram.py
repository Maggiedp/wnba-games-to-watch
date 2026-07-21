import requests

from src.notify import telegram


class _Resp:
    def __init__(self, ok=True):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("boom")


def test_send_returns_true_on_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    sent = {}

    def fake_post(url, json, timeout):
        sent["url"] = url
        sent["json"] = json
        return _Resp(ok=True)

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    assert telegram.send_telegram("hi") is True
    assert "bottok/sendMessage" in sent["url"]
    assert sent["json"]["chat_id"] == "123"
    assert sent["json"]["text"] == "hi"


def test_send_returns_false_on_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: _Resp(ok=False))
    assert telegram.send_telegram("hi") is False


def test_send_returns_false_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert telegram.send_telegram("hi") is False
