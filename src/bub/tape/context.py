"""Tape context helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from republic import TapeContext, TapeEntry

# Key used to carry handoff state through TapeContext.state.
HANDOFF_STATE_KEY = "_handoff_state"


def default_tape_context(state: dict[str, Any] | None = None) -> TapeContext:
    """Return the default context selection for Bub.

    Uses the default ``LAST_ANCHOR`` so Republic handles anchor slicing.
    Handoff state is passed via ``TapeContext.state[HANDOFF_STATE_KEY]``
    which is populated by ``TapeService.fork_tape()`` before the loop runs.
    """

    return TapeContext(select=_select_messages, state=state or {})


def _select_messages(entries: Iterable[TapeEntry], context: TapeContext) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    # Inject handoff context carried via TapeContext.state.
    anchor_state = context.state.get(HANDOFF_STATE_KEY)
    if isinstance(anchor_state, dict) and anchor_state:
        rendered = _render_handoff_context(anchor_state)
        if rendered:
            messages.append({"role": "system", "content": rendered})

    pending_calls: list[dict[str, Any]] = []

    for entry in entries:
        if entry.kind == "message":
            _append_message_entry(messages, entry)
            continue

        if entry.kind == "tool_call":
            pending_calls = _append_tool_call_entry(messages, entry)
            continue

        if entry.kind == "tool_result":
            _append_tool_result_entry(messages, pending_calls, entry)
            pending_calls = []

    return messages


_HANDOFF_KNOWN_KEYS = {"summary", "next_steps", "owner", "files_modified", "decisions"}


def _render_handoff_context(state: dict[str, Any]) -> str | None:
    """Render anchor state as a structured context block.

    Returns None if the state contains no meaningful content to inject.
    """
    sections: list[str] = []
    if summary := state.get("summary"):
        sections.append(f"## Summary\n{summary}")
    if next_steps := state.get("next_steps"):
        sections.append(f"## Next Steps\n{next_steps}")
    if (files_modified := state.get("files_modified")) and isinstance(files_modified, list):
        sections.append("## Files Modified\n" + "\n".join(f"- {f}" for f in files_modified))
    if (decisions := state.get("decisions")) and isinstance(decisions, list):
        sections.append("## Key Decisions\n" + "\n".join(f"- {d}" for d in decisions))
    # Include any other state keys (skip known ones).
    extra = {k: v for k, v in state.items() if k not in _HANDOFF_KNOWN_KEYS}
    if extra:
        sections.append(f"## Additional State\n{json.dumps(extra, ensure_ascii=False)}")
    if not sections:
        return None
    header = "[Context checkpoint from previous phase]"
    footer = "NOTE: This is your only context from the previous phase. Re-read files before modifying them."
    return f"{header}\n\n" + "\n\n".join(sections) + f"\n\n{footer}"


def _append_message_entry(messages: list[dict[str, Any]], entry: TapeEntry) -> None:
    payload = entry.payload
    if isinstance(payload, dict):
        messages.append(dict(payload))


def _append_tool_call_entry(messages: list[dict[str, Any]], entry: TapeEntry) -> list[dict[str, Any]]:
    calls = _normalize_tool_calls(entry.payload.get("calls"))
    if calls:
        messages.append({"role": "assistant", "content": "", "tool_calls": calls})
    return calls


def _append_tool_result_entry(
    messages: list[dict[str, Any]],
    pending_calls: list[dict[str, Any]],
    entry: TapeEntry,
) -> None:
    results = entry.payload.get("results")
    if not isinstance(results, list):
        return
    for index, result in enumerate(results):
        messages.append(_build_tool_result_message(result, pending_calls, index))


def _build_tool_result_message(
    result: object,
    pending_calls: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "tool", "content": _render_tool_result(result)}
    if index >= len(pending_calls):
        return message

    call = pending_calls[index]
    call_id = call.get("id")
    if isinstance(call_id, str) and call_id:
        message["tool_call_id"] = call_id

    function = call.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            message["name"] = name
    return message


def _normalize_tool_calls(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            calls.append(dict(item))
    return calls


def _render_tool_result(result: object) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except TypeError:
        return str(result)
