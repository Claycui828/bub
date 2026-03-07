"""Progressive tool prompt rendering."""

from __future__ import annotations

from dataclasses import dataclass, field

from bub.tools.registry import ToolRegistry


@dataclass
class ProgressiveToolView:
    """Renders compact tool view and expands schema on demand."""

    registry: ToolRegistry
    expanded: set[str] = field(default_factory=set)

    def note_selected(self, name: str) -> None:
        if self.registry.has(name):
            self.expanded.add(name)

    def all_tools(self) -> list[str]:
        return [descriptor.name for descriptor in self.registry.descriptors()]

    def reset(self) -> None:
        """Clear expanded tool details for a fresh prompt context."""
        self.expanded.clear()

    def reset_to(self, names: list[str]) -> None:
        """Reset expanded set to only the given tool names (validated against registry)."""
        self.expanded = {n for n in names if self.registry.has(n)}

    def _effective_expanded(self) -> set[str]:
        """Return expanded set union with always-expanded tools."""
        always = {d.name for d in self.registry.descriptors() if d.always_expand}
        return self.expanded | always

    def always_expanded_names(self) -> list[str]:
        """Return names of tools that are always expanded."""
        return [d.name for d in self.registry.descriptors() if d.always_expand]

    def compact_block(self) -> str:
        always = {d.name for d in self.registry.descriptors() if d.always_expand}
        lines = ["<tool_view>"]
        for descriptor in self.registry.descriptors():
            display_name = self.registry.to_model_name(descriptor.name)
            suffix = " [always-expanded]" if descriptor.name in always else ""
            if display_name != descriptor.name:
                lines.append(f"  - {display_name} (command: {descriptor.name}): {descriptor.short_description}{suffix}")
            else:
                lines.append(f"  - {display_name}: {descriptor.short_description}{suffix}")
        lines.append("</tool_view>")
        return "\n".join(lines)

    def expanded_block(self) -> str:
        effective = self._effective_expanded()
        if not effective:
            return ""

        lines = ["<tool_details>"]
        for name in sorted(effective):
            model_name = self.registry.to_model_name(name)
            try:
                detail = self.registry.detail(name, for_model=True)
            except KeyError:
                continue
            lines.append(f'  <tool name="{model_name}">')
            for line in detail.splitlines():
                lines.append(f"    {line}")
            lines.append("  </tool>")
        lines.append("</tool_details>")
        return "\n".join(lines)
