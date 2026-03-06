"""Extensible slash command registry for the CLI channel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bub.app.runtime import SessionRuntime
    from bub.channels.cli import CliChannel
    from bub.cli.render import CliRenderer


@dataclass
class CommandContext:
    """Context passed to every slash command handler."""

    renderer: CliRenderer
    session: SessionRuntime | None
    channel: CliChannel
    agent_running: bool


# Handler signature: async (args: str, ctx: CommandContext) -> optional message
CommandHandler = Callable[[str, CommandContext], Awaitable[str | None]]


@dataclass(frozen=True)
class SlashCommand:
    """One registered slash command."""

    name: str
    description: str
    handler: CommandHandler
    aliases: list[str] = field(default_factory=list)
    category: str = "general"
    available_while_running: bool = True
    hidden: bool = False


class CommandRegistry:
    """Registry for slash commands with alias support."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._aliases: dict[str, str] = {}  # alias -> canonical name

    def register(self, cmd: SlashCommand) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._aliases[alias] = cmd.name

    def get(self, name: str) -> SlashCommand | None:
        canonical = self._aliases.get(name, name)
        return self._commands.get(canonical)

    def execute(self, raw_input: str, ctx: CommandContext) -> str | None | Awaitable[str | None]:
        """Parse and dispatch a slash command. Returns None if not a command."""
        stripped = raw_input.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped[1:].split(None, 1)
        if not parts:
            return None

        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        cmd = self.get(cmd_name)
        if cmd is None:
            return None

        if not cmd.available_while_running and ctx.agent_running:
            return f"/{cmd.name} is not available while agent is running."

        return cmd.handler(args, ctx)

    def is_command(self, raw_input: str) -> bool:
        """Check if input is a registered slash command."""
        stripped = raw_input.strip()
        if not stripped.startswith("/"):
            return False
        parts = stripped[1:].split(None, 1)
        if not parts:
            return False
        return self.get(parts[0].lower()) is not None

    def list_commands(self, category: str | None = None) -> list[SlashCommand]:
        commands = [c for c in self._commands.values() if not c.hidden]
        if category:
            commands = [c for c in commands if c.category == category]
        return sorted(commands, key=lambda c: (c.category, c.name))

    def categories(self) -> list[str]:
        cats = {c.category for c in self._commands.values() if not c.hidden}
        return sorted(cats)

    def help_text(self) -> str:
        lines: list[str] = []
        for cat in self.categories():
            commands = self.list_commands(cat)
            if not commands:
                continue
            lines.append(f"\n  {cat.upper()}")
            for cmd in commands:
                aliases = ""
                if cmd.aliases:
                    aliases = f" ({', '.join('/' + a for a in cmd.aliases)})"
                lines.append(f"    /{cmd.name:<16}{cmd.description}{aliases}")
        return "\n".join(lines)
