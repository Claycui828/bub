"""Structured system prompt builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptBlock:
    """One named block in the system prompt."""

    name: str
    content: str
    priority: int
    mutable: bool


class PromptBuilder:
    """Builds system prompts from named, prioritised blocks."""

    def __init__(self) -> None:
        self._blocks: list[PromptBlock] = []

    def add(self, name: str, content: str, *, priority: int = 50, mutable: bool = False) -> None:
        if content and content.strip():
            self._blocks.append(PromptBlock(name=name, content=content, priority=priority, mutable=mutable))

    def render(self) -> str:
        sorted_blocks = sorted(self._blocks, key=lambda b: b.priority)
        return "\n\n".join(b.content for b in sorted_blocks)

    def debug_info(self) -> list[dict[str, Any]]:
        """Return block metadata for tracing / debugging."""
        return [
            {
                "name": b.name,
                "priority": b.priority,
                "chars": len(b.content),
                "mutable": b.mutable,
            }
            for b in sorted(self._blocks, key=lambda b: b.priority)
        ]
