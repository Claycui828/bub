from republic import TapeEntry
from republic.tape.context import LAST_ANCHOR

from bub.tape.context import HANDOFF_STATE_KEY, _render_handoff_context, default_tape_context


def test_default_tape_context_uses_last_anchor() -> None:
    """Context uses LAST_ANCHOR so Republic handles anchor slicing."""
    context = default_tape_context()
    assert isinstance(context.anchor, type(LAST_ANCHOR))


def test_default_tape_context_includes_tool_messages() -> None:
    context = default_tape_context()
    assert context.select is not None

    entries = [
        TapeEntry.message({"role": "user", "content": "create a file"}),
        TapeEntry.tool_call([
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "fs.write", "arguments": '{"path":"a.txt","content":"hi"}'},
            },
            {
                "id": "call-2",
                "type": "function",
                "function": {"name": "fs.read", "arguments": '{"path":"a.txt"}'},
            },
        ]),
        TapeEntry.tool_result(["ok", {"content": "hi"}]),
        TapeEntry.message({"role": "assistant", "content": "done"}),
    ]

    messages = context.select(entries, context)
    assert messages[0] == {"role": "user", "content": "create a file"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["id"] == "call-1"
    assert messages[2] == {"role": "tool", "content": "ok", "tool_call_id": "call-1", "name": "fs.write"}
    assert messages[3] == {
        "role": "tool",
        "content": '{"content": "hi"}',
        "tool_call_id": "call-2",
        "name": "fs.read",
    }
    assert messages[4] == {"role": "assistant", "content": "done"}


def test_default_tape_context_handles_result_without_calls() -> None:
    context = default_tape_context()
    assert context.select is not None

    entries = [TapeEntry.tool_result([{"status": "ok"}])]
    messages = context.select(entries, context)

    assert messages == [{"role": "tool", "content": '{"status": "ok"}'}]


class TestHandoffContextInjection:
    """Verify that handoff anchor state is injected as a system message via context.state."""

    def test_handoff_state_injected_as_first_message(self) -> None:
        """When HANDOFF_STATE_KEY is set in context.state, a system message is prepended."""
        context = default_tape_context({
            HANDOFF_STATE_KEY: {"summary": "did some work", "next_steps": "do more"},
        })
        # Republic would have already sliced to entries after the anchor.
        entries = [
            TapeEntry.message({"role": "user", "content": "continue"}),
        ]
        messages = context.select(entries, context)
        assert messages[0]["role"] == "system"
        assert "did some work" in messages[0]["content"]
        assert "do more" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "continue"}
        assert len(messages) == 2

    def test_no_handoff_state_no_injection(self) -> None:
        """When context.state has no handoff key, no system message is injected."""
        context = default_tape_context({"session_id": "cli"})
        entries = [
            TapeEntry.message({"role": "user", "content": "hello"}),
        ]
        messages = context.select(entries, context)
        assert messages[0] == {"role": "user", "content": "hello"}

    def test_owner_only_state_no_injection(self) -> None:
        """State with only 'owner' key should not produce a system message."""
        context = default_tape_context({
            HANDOFF_STATE_KEY: {"owner": "human"},
        })
        entries = [
            TapeEntry.message({"role": "user", "content": "hello"}),
        ]
        messages = context.select(entries, context)
        assert messages[0] == {"role": "user", "content": "hello"}

    def test_empty_handoff_state_no_injection(self) -> None:
        context = default_tape_context({HANDOFF_STATE_KEY: {}})
        entries = [
            TapeEntry.message({"role": "user", "content": "go"}),
        ]
        messages = context.select(entries, context)
        assert messages[0] == {"role": "user", "content": "go"}

    def test_handoff_with_extra_state_keys(self) -> None:
        context = default_tape_context({
            HANDOFF_STATE_KEY: {"summary": "code review done", "findings": 3},
        })
        entries = [
            TapeEntry.message({"role": "user", "content": "next"}),
        ]
        messages = context.select(entries, context)
        assert messages[0]["role"] == "system"
        assert "code review done" in messages[0]["content"]
        assert "findings" in messages[0]["content"]

    def test_no_entries_only_handoff(self) -> None:
        """Handoff state is injected even when there are no tape entries."""
        context = default_tape_context({
            HANDOFF_STATE_KEY: {"summary": "phase complete"},
        })
        messages = context.select([], context)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert "phase complete" in messages[0]["content"]


class TestRenderHandoffContext:
    def test_summary_and_next_steps(self) -> None:
        result = _render_handoff_context({"summary": "done A", "next_steps": "do B"})
        assert result is not None
        assert "## Summary" in result
        assert "done A" in result
        assert "## Next Steps" in result
        assert "do B" in result

    def test_owner_excluded(self) -> None:
        result = _render_handoff_context({"owner": "human", "summary": "hello"})
        assert result is not None
        assert "owner" not in result
        assert "hello" in result

    def test_extra_keys_in_state(self) -> None:
        result = _render_handoff_context({"custom_key": "val"})
        assert result is not None
        assert "custom_key" in result

    def test_owner_only_returns_none(self) -> None:
        result = _render_handoff_context({"owner": "human"})
        assert result is None

    def test_files_modified_and_decisions(self) -> None:
        result = _render_handoff_context({
            "summary": "built API",
            "files_modified": ["src/main.py", "src/models.py"],
            "decisions": ["Chose FastAPI", "Using SQLite"],
        })
        assert result is not None
        assert "## Files Modified" in result
        assert "- src/main.py" in result
        assert "## Key Decisions" in result
        assert "- Chose FastAPI" in result

    def test_footer_present(self) -> None:
        result = _render_handoff_context({"summary": "test"})
        assert result is not None
        assert "Re-read files before modifying them" in result
