"""Send a message to a private Telegram chat. Config via env
(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID); on Cloud Run these come from Secret
Manager. Never raises — a Telegram outage must not crash the poller."""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"


def send_telegram(text: str, timeout: int = 10) -> bool:
    """POST `text` to the configured chat. Returns True on 2xx, else False."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("telegram: token/chat_id not configured; skipping send")
        return False
    try:
        r = requests.post(
            f"{_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=timeout,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        # NEVER log the raw exception: requests embeds the full request URL
        # (which contains the bot token) in HTTPError/connection error strings,
        # so `%s` on the exception would leak the token into logs. Log only the
        # exception type + HTTP status.
        status = getattr(getattr(e, "response", None), "status_code", None)
        logger.warning(
            "telegram: send failed (%s, status=%s)", type(e).__name__, status
        )
        return False
