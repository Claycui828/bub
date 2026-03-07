
#!/usr/bin/env python3
"""
Edit Telegram messages via Bot API.
Usage:
  uv run ./scripts/telegram_edit.py --chat-id <ID> --message-id <ID> --text "<TEXT>"
"""

import argparse
import os
import sys

import requests


def main():
    parser = argparse.ArgumentParser(description="Edit Telegram message")
    parser.add_argument("--chat-id", "-c", required=True, help="Chat ID")
    parser.add_argument("--message-id", "-m", required=True, help="Message ID to edit")
    parser.add_argument("--text", required=True, help="New text content")
    parser.add_argument("--token", default=None, help="Bot token (optional)")
    args = parser.parse_args()

    token = args.token or os.environ.get("BUB_TELEGRAM_TOKEN")
    if not token:
        print("Error: BUB_TELEGRAM_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    chat_id = args.chat_id
    message_id = args.message_id
    text = args.text

    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        print("Message edited successfully")
    except requests.exceptions.RequestException as e:
        print(f"Error editing message: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
