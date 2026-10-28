"""Bot Setup Workflow: register the webhook, command menus and descriptions with
Telegram. Runs from .github/workflows/bot-setup.yml with repository secrets.

Usage: python -m job.bot_setup https://storerating-bot.vercel.app
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PUBLIC_COMMANDS = [
    ("add", "Watch a new app"),
    ("apps", "View and manage your apps"),
    ("pause", "Pause all notifications"),
    ("resume", "Resume all notifications"),
    ("settings", "Digest time and timezone"),
    ("delete", "Delete all your data"),
]
ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    ("stats", "Admin: users and usage"),
    ("budget", "Admin: Claude and platform budget"),
    ("setbudget", "Admin: set monthly Claude budget"),
]
NAME = "StoreRating"
SHORT_DESCRIPTION = "Watches Google Play apps: rating moves, releases, top complaints and 1★ spikes. Free."
DESCRIPTION = (
    "StoreRating watches Google Play apps for you.\n\n"
    "• Daily card per app: rating trend, release markers, top complaints and praise\n"
    "• Instant alerts for new releases, rating drops and 1★ spikes\n"
    "• Watch up to 10 apps, pause anytime\n\n"
    "Privacy: we store your chat ID, timezone, settings and watchlist. Nothing else. /delete removes it all.\n"
    "Not affiliated with Google."
)
ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]


def call(token: str, method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            body = json.load(res)
    except urllib.error.HTTPError as e:  # never print the URL: it contains the token
        body = json.loads(e.read() or b"{}")
    if not body.get("ok"):
        raise SystemExit(f"{method} failed: {body.get('description', 'unknown error')}")
    return body


def commands(pairs: list[tuple[str, str]]) -> list[dict]:
    return [{"command": c, "description": d} for c, d in pairs]


def main(base_url: str) -> None:
    token = os.environ["BOT_TOKEN"]
    secret = os.environ["WEBHOOK_SECRET"]
    admin = int(os.environ["ADMIN_CHAT_ID"])
    url = base_url.rstrip("/") + "/api/telegram"

    call(token, "setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ALLOWED_UPDATES,
        "drop_pending_updates": False,
    })
    print(f"Webhook set to {url}")

    call(token, "setMyName", {"name": NAME})
    call(token, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION})
    call(token, "setMyDescription", {"description": DESCRIPTION})
    print("Name and descriptions set")

    call(token, "setMyCommands", {"commands": commands(PUBLIC_COMMANDS), "scope": {"type": "all_private_chats"}})
    call(token, "setMyCommands", {"commands": commands(ADMIN_COMMANDS), "scope": {"type": "chat", "chat_id": admin}})
    print("Command menus set: public, and Admin-only for ADMIN_CHAT_ID")

    info = call(token, "getWebhookInfo", {})["result"]
    print(f"Webhook check: url ok={info.get('url') == url}, pending={info.get('pending_update_count')}, "
          f"last error={info.get('last_error_message') or 'none'}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        raise SystemExit("usage: python -m job.bot_setup https://<web project domain>")
    main(sys.argv[1])
