---
name: telegram-demo
description: |
  Demonstrates the proper way to send Telegram messages in the Bub agent framework.
  Use when Bub needs to: (1) Show correct syntax and method names for sending Telegram messages,
  (2) Demonstrate the telegram skill usage patterns, or (3) Provide examples of Telegram message sending.
---

# Telegram Skill Demonstration

This skill demonstrates the proper way to send Telegram messages using the Bub agent framework.

## Core Pattern for Sending Telegram Messages

The correct way to send a Telegram message is to use the `telegram_send.py` script:

### Basic Message Send

```bash
uv run ./scripts/telegram_send.py \
  --chat-id <CHAT_ID> \
  --message "<TEXT>"
```

### Reply to a Specific Message

```bash
uv run ./scripts/telegram_send.py \
  --chat-id <CHAT_ID> \
  --message "<TEXT>" \
  --reply-to <MESSAGE_ID>
```

### Handling Bot-Originated Messages

When the source message sender is a bot (`sender_is_bot=true`), do NOT use reply mode.
Instead, send a normal message and prefix content with `@<sender_username>`:

```bash
uv run ./scripts/telegram_send.py \
  --chat-id <CHAT_ID> \
  --message "<TEXT>" \
  --source-is-bot \
  --source-user-id <USER_ID>
```

### Edit an Existing Message

```bash
uv run ./scripts/telegram_edit.py \
  --chat-id <CHAT_ID> \
  --message-id <MESSAGE_ID> \
  --text "<TEXT>"
```

## Key Method Names and Syntax

| Action | Command | Required Flags |
|--------|---------|----------------|
| Send message | `telegram_send.py` | `--chat-id`, `--message` |
| Reply to message | `telegram_send.py` | `--chat-id`, `--message`, `--reply-to` |
| Edit message | `telegram_edit.py` | `--chat-id`, `--message-id`, `--text` |
| Bot-originated message | `telegram_send.py` | `--chat-id`, `--message`, `--source-is-bot`, `--source-user-id` |

## Common Mistakes to Avoid

1. ❌ **Never use comma-prefixed commands** like `,@telegram ...` - these are deprecated
2. ❌ **Never emit `<command ...>` blocks yourself** - these are runtime-generated only
3. ❌ **Don't forget `--message` flag** when sending - it's required
4. ❌ **Don't use HTML tags** - use Markdown formatting instead
5. ❌ **Don't reply to bot-originated messages** - use `--source-is-bot` pattern instead

## Script Interface Reference

### `telegram_send.py`

- `--chat-id`, `-c`: required, supports comma-separated ids
- `--message`, `-m`: required
- `--reply-to`, `-r`: optional, for threaded replies
- `--token`, `-t`: optional (normally not needed)
- `--source-is-bot`: optional flag, disables reply mode
- `--source-user-id`, `-u`: optional, required when `--source-is-bot` is set

### `telegram_edit.py`

- `--chat-id`, `-c`: required
- `--message-id`, `-m`: required
- `--text`: required
- `--token`: optional (normally not needed)

## Example Usage in Practice

### Scenario 1: Responding to user message
```bash
# User sends: "Hello, what's the weather?"
# You should reply:
uv run ./scripts/telegram_send.py \
  --chat-id 123456789 \
  --message "I'm checking the weather for you..." \
  --reply-to 987654321
```

### Scenario 2: Bot-originated message
```bash
# A bot sent a message, you need to respond
uv run ./scripts/telegram_send.py \
  --chat-id 123456789 \
  --message "@123456 Here is the information you requested" \
  --source-is-bot \
  --source-user-id 123456
```

### Scenario 3: Progress update then final result
```bash
# Start long task
uv run ./scripts/telegram_send.py \
  --chat-id 123456789 \
  --message "Starting long-running task..." \
  --reply-to 987654321

# Later, edit same message with result
uv run ./scripts/telegram_edit.py \
  --chat-id 123456789 \
  --message-id 111111111 \
  --text "Task completed successfully! Here are the results..."
```

## Proactive Notifications

When working outside an active Telegram session, use `telegram_send.py` with `--chat-id`:

```bash
uv run ./scripts/telegram_send.py \
  --chat-id 123456789 \
  --message "Important: Task completed with warnings"
```

## Quick Reference Card

```
✅ CORRECT:
uv run ./scripts/telegram_send.py --chat-id ID --message "text"
uv run ./scripts/telegram_send.py --chat-id ID --message "text" --reply-to MSG_ID
uv run ./scripts/telegram_edit.py --chat-id ID --message-id ID --text "text"

❌ WRONG:
,@telegram send text
<command telegram_send ...>
```

## When to Use This Skill

Use this skill when:
- Teaching others how to send Telegram messages in Bub
- Demonstrating the correct command syntax
- Debugging Telegram message sending issues
- Creating documentation about Telegram integration
