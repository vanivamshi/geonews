import asyncio
import logging
import re
import smtplib
from email.message import EmailMessage

import httpx

from geonews.config import get_settings
from geonews.db import session_scope
from geonews.models import User, UserProfile

log = logging.getLogger(__name__)


def _with_links(body: str, base: str, briefing_id: int) -> str:
    # [A123] -> link through our redirect so clicks are logged as events.
    return re.sub(r"\[(A\d+)\]", lambda m: f"[{m.group(1)}]({base}/r/{briefing_id}/{m.group(1)})", body)


def _send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


async def _send_telegram(chat_id: str, text: str) -> None:
    token = get_settings().telegram_bot_token
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
        )
        r.raise_for_status()


async def deliver(user_id: int, briefing_id: int, body: str) -> None:
    s = get_settings()
    async with session_scope() as sess:
        user = await sess.get(User, user_id)
        profile = await sess.get(UserProfile, user_id)
    linked = _with_links(body, s.web_base_url, briefing_id)
    channels = profile.channels or ["web"]
    if "email" in channels and s.smtp_host:
        try:
            await asyncio.to_thread(_send_email, user.email, "Your GeoNews briefing", linked)
        except Exception:
            log.exception("email delivery failed for user %s", user_id)
    if "telegram" in channels and s.telegram_bot_token and profile.telegram_chat_id:
        try:
            await _send_telegram(profile.telegram_chat_id, linked)
        except Exception:
            log.exception("telegram delivery failed for user %s", user_id)
