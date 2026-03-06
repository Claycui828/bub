"""Tests for the slash command registry."""

from __future__ import annotations

import pytest

from bub.cli.commands import CommandContext, CommandRegistry, SlashCommand


def _make_ctx(**overrides) -> CommandContext:
    defaults = {
        "renderer": None,
        "session": None,
        "channel": None,
        "agent_running": False,
    }
    defaults.update(overrides)
    return CommandContext(**defaults)  # type: ignore[arg-type]


class TestCommandRegistry:
    def test_register_and_get(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return "ok"

        registry.register(SlashCommand(name="test", description="A test", handler=handler))
        assert registry.get("test") is not None
        assert registry.get("test").name == "test"

    def test_alias_resolution(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return None

        registry.register(SlashCommand(name="fold", description="Fold", handler=handler, aliases=["f"]))
        assert registry.get("f") is not None
        assert registry.get("f").name == "fold"

    def test_get_missing_returns_none(self) -> None:
        registry = CommandRegistry()
        assert registry.get("nonexistent") is None

    def test_is_command(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return None

        registry.register(SlashCommand(name="test", description="Test", handler=handler))
        assert registry.is_command("/test") is True
        assert registry.is_command("/test arg1") is True
        assert registry.is_command("/unknown") is False
        assert registry.is_command("test") is False
        assert registry.is_command("") is False

    @pytest.mark.asyncio
    async def test_execute_calls_handler(self) -> None:
        registry = CommandRegistry()
        called_with: list[str] = []

        async def handler(args: str, ctx: CommandContext) -> str | None:
            called_with.append(args)
            return "result"

        registry.register(SlashCommand(name="test", description="Test", handler=handler))
        ctx = _make_ctx()
        result = await registry.execute("/test hello world", ctx)
        assert result == "result"
        assert called_with == ["hello world"]

    def test_execute_non_command_returns_none(self) -> None:
        registry = CommandRegistry()
        ctx = _make_ctx()
        result = registry.execute("not a command", ctx)
        assert result is None

    def test_execute_unknown_command_returns_none(self) -> None:
        registry = CommandRegistry()
        ctx = _make_ctx()
        result = registry.execute("/unknown", ctx)
        assert result is None

    def test_not_available_while_running(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return "ok"

        registry.register(SlashCommand(
            name="test", description="Test", handler=handler,
            available_while_running=False,
        ))
        ctx = _make_ctx(agent_running=True)
        # execute() returns a plain string (not awaitable) for the "not available" case.
        result = registry.execute("/test", ctx)
        assert "not available" in result

    def test_list_commands_excludes_hidden(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return None

        registry.register(SlashCommand(name="visible", description="V", handler=handler))
        registry.register(SlashCommand(name="hidden", description="H", handler=handler, hidden=True))
        commands = registry.list_commands()
        names = [c.name for c in commands]
        assert "visible" in names
        assert "hidden" not in names

    def test_list_commands_by_category(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return None

        registry.register(SlashCommand(name="a", description="A", handler=handler, category="cat1"))
        registry.register(SlashCommand(name="b", description="B", handler=handler, category="cat2"))
        assert len(registry.list_commands("cat1")) == 1
        assert registry.list_commands("cat1")[0].name == "a"

    def test_categories(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return None

        registry.register(SlashCommand(name="a", description="A", handler=handler, category="display"))
        registry.register(SlashCommand(name="b", description="B", handler=handler, category="control"))
        assert registry.categories() == ["control", "display"]

    def test_help_text(self) -> None:
        registry = CommandRegistry()

        async def handler(args: str, ctx: CommandContext) -> str | None:
            return None

        registry.register(SlashCommand(
            name="fold", description="Fold panel", handler=handler,
            aliases=["f"], category="display",
        ))
        text = registry.help_text()
        assert "DISPLAY" in text
        assert "/fold" in text
        assert "/f" in text
