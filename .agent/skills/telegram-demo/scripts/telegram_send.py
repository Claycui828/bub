

#!/usr/bin/env python3
"""
Send Telegram messages via Bot API.
Usage:
  uv run ./scripts/telegram_send.py --chat-id <ID> --message "<TEXT>"
  uv run ./scripts/telegram_send.py --chat-id <ID> --message "<TEXT>" --reply-to <MSG_ID>
"""

import argparse
import os
import sys

import requests


def main():
    parser = argparse.ArgumentParser(description="Send Telegram message")
    parser.add_argument("--chat-id", "-c", required=True, help="Chat ID")
    parser.add_argument("--message", "-m", required=True, help="Message text")
    parser.add_argument("--reply-to", "-r", type=int, help="Reply to message ID")
    parser.add_argument("--token", "-t", default=None, help="Bot token (optional)")
    parser.add_argument("--source-is-bot", action="store_true", help="Source is a bot (no reply)")
    parser.add_argument("--source-user-id", type=int, help="Source user ID when source-is-bot")
    args = parser.parse_args()

    token = args.token or os.environ.get("BUB_TELEGRAM_TOKEN")
    if not token:
        print("Error: BUB_TELEGRAM_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    chat_id = args.chat_id
    text = args.message

    # Build reply markup
    reply_markup = None
    if args.source_is_bot:
        # Don't reply to bots, prefix with @username
        if args.source_user_id:
            text = f"@{args.source_user_id} {text}"
    elif args.reply_to:
        reply_markup = {"reply_to_message_id": args.reply_to}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        print("Message sent successfully")
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
