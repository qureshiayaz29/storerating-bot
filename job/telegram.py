"""Telegram sending for the Daily Job. Admin alerts and Updates go through here.

Without BOT_TOKEN (local runs, tests) messages are printed instead of sent.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class TelegramSendError(Exception):
    def __init__(self, method: str, status: int, description: str, retry_after: int | None = None):
        self.method, self.status, self.description, self.retry_after = method, status, description, retry_after
        super().__init__(f"{method} failed: {status} {description}")

    @property
    def blocked(self) -> bool:
        return self.status == 403


@dataclass
class Telegram:
    token: str | None = field(default_factory=lambda: os.environ.get("BOT_TOKEN"))
    sent: list[dict] = field(default_factory=list)  # dry-run record

    def call(self, method: str, payload: dict) -> dict:
        if not self.token:
            self.sent.append({"method": method, **payload})
            print(f"[dry-run] {method}: {str(payload.get('text') or payload.get('caption') or '')[:120]}")
            return {"ok": True, "result": {}}
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:  # never surface the URL: it holds the token
            body = json.loads(e.read() or b"{}")
            retry = (body.get("parameters") or {}).get("retry_after")
            raise TelegramSendError(method, e.code, body.get("description", "unknown"), retry) from None

    def send_message(self, chat_id: int, text: str, *, silent: bool = False) -> dict:
        return self.call("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True}, "disable_notification": silent,
        })


def admin_alert(text: str, tg: Telegram | None = None) -> None:
    """Send an Admin alert. Never raises: a failed alert must not fail the job."""
    tg = tg or Telegram()
    admin = os.environ.get("ADMIN_CHAT_ID")
    if not admin:
        print(f"[admin alert, no ADMIN_CHAT_ID] {text}")
        return
    for attempt in range(3):
        try:
            tg.send_message(int(admin), text)
            return
        except TelegramSendError as e:
            if e.retry_after and attempt < 2:
                time.sleep(min(e.retry_after, 30))
                continue
            print(f"Admin alert failed: {e}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"Admin alert failed: {type(e).__name__}: {e}")
            return
