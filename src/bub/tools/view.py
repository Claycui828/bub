"""Tool view helpers."""

from __future__ import annotations

from bub.tools.progressive import ProgressiveToolView


def render_tool_compact_block(view: ProgressiveToolView) -> str:
    """Render the stable compact tool list (cache-friendly prefix)."""
    return view.compact_block()


def render_tool_expanded_block(view: ProgressiveToolView) -> str:
    """Render expanded tool details (appended at end of system prompt)."""
    return view.expanded_block()


def render_tool_prompt_block(view: ProgressiveToolView) -> str:
    """Render the combined tool prompt section (legacy)."""
    compact = render_tool_compact_block(view)
    expanded = render_tool_expanded_block(view)
    if not expanded:
        return compact
    return f"{compact}\n\n{expanded}"
