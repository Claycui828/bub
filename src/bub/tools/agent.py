"""Agent delegation tool."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field
from republic import ToolContext

from bub.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from bub.app.runtime import AppRuntime


class AgentInput(BaseModel):
    prompt: str = Field(..., description="Task description for the sub-agent")
    description: str = Field(default="", description="Short label for this delegation (3-5 words)")
    model: str | None = Field(default=None, description="Override model for this agent (e.g. 'openrouter:anthropic/claude-sonnet-4')")
    system_prompt: str | None = Field(default=None, description="Override system prompt for this agent")
    allowed_tools: list[str] | None = Field(default=None, description="Restrict tools available to this agent")
    run_in_background: bool = Field(default=False, description="Run agent in background, return agent_id immediately")
    resume: str | None = Field(default=None, description="Agent ID to resume from a previous invocation")


@dataclass
class AgentRecord:
    """Tracks one sub-agent invocation."""

    agent_id: str
    session_id: str
    description: str
    status: str = "running"  # running | completed | error
    result: str = ""
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class AgentManager:
    """Manages sub-agent lifecycle and background tasks."""

    def __init__(self) -> None:
        self._records: dict[str, AgentRecord] = {}
        self._background_tasks: dict[str, asyncio.Task[None]] = {}
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"agent-{self._counter}"

    def get(self, agent_id: str) -> AgentRecord | None:
        return self._records.get(agent_id)

    def register(self, record: AgentRecord) -> None:
        self._records[record.agent_id] = record

    def set_background_task(self, agent_id: str, task: asyncio.Task[None]) -> None:
        self._background_tasks[agent_id] = task

    def list_agents(self) -> list[AgentRecord]:
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)


# Module-level singleton; reset-friendly for tests.
_manager = AgentManager()


def get_agent_manager() -> AgentManager:
    return _manager


def register_agent_tools(
    registry: ToolRegistry,
    *,
    runtime: AppRuntime,
) -> None:
    """Register agent delegation tool."""

    manager = get_agent_manager()
    register = registry.register

    @register(
        name="agent",
        short_description="Delegate a task to a sub-agent and return result",
        model=AgentInput,
        context=True,
    )
    async def agent_delegate(params: AgentInput, context: ToolContext) -> str:
        """Spawn a sub-agent session to handle a complex task autonomously.

        The sub-agent runs with the same model and tools (unless overridden) in an
        isolated session. It receives the prompt, executes until completion, and
        returns the final visible output.

        Features:
        - model: use a different model for this agent.
        - run_in_background: return immediately with an agent_id; check status later
          with agent.status.
        - resume: continue a previous agent session by passing its agent_id; the
          agent keeps its full conversation history.
        - allowed_tools: restrict which tools are available to the sub-agent.
        - system_prompt: override the system prompt for the sub-agent.
        """
        parent_session_id = context.state.get("session_id", "")

        # --- Resume existing agent ---
        if params.resume:
            record = manager.get(params.resume)
            if record is None:
                raise RuntimeError(f"agent not found: {params.resume}")
            if record.status == "running":
                raise RuntimeError(f"agent {params.resume} is still running")

            # Re-use same session_id so tape history is preserved.
            result = await runtime.handle_input(
                record.session_id,
                params.prompt,
                model=params.model,
                system_prompt=params.system_prompt,
                allowed_tools=set(params.allowed_tools) if params.allowed_tools else None,
            )
            record.status = "completed" if not result.error else "error"
            record.result = result.visible_text.strip() if result.visible_text else ""
            record.error = result.error
            record.finished_at = time.time()
            return _format_result(record)

        # --- New agent ---
        agent_id = manager.next_id()
        sub_session_id = f"{parent_session_id}:sub:{agent_id}"
        record = AgentRecord(
            agent_id=agent_id,
            session_id=sub_session_id,
            description=params.description or "sub-agent",
        )
        manager.register(record)

        tool_set = set(params.allowed_tools) if params.allowed_tools else None

        if params.run_in_background:
            task = asyncio.create_task(
                _run_background(runtime, manager, record, params.prompt, params.model, params.system_prompt, tool_set)
            )
            manager.set_background_task(agent_id, task)
            logger.info("agent.background.start agent_id={} description={}", agent_id, params.description)
            return f"agent started in background: {agent_id}\nUse agent.status with agent_id={agent_id} to check progress."

        # --- Foreground execution ---
        result = await runtime.handle_input(
            sub_session_id,
            params.prompt,
            model=params.model,
            system_prompt=params.system_prompt,
            allowed_tools=tool_set,
        )
        record.status = "completed" if not result.error else "error"
        record.result = result.visible_text.strip() if result.visible_text else ""
        record.error = result.error
        record.finished_at = time.time()
        return _format_result(record)

    # --- agent.status tool ---

    class AgentStatusInput(BaseModel):
        agent_id: str = Field(..., description="Agent ID to check")

    @register(name="agent.status", short_description="Check sub-agent status", model=AgentStatusInput)
    def agent_status(params: AgentStatusInput) -> str:
        """Check the status and result of a sub-agent by its ID."""
        record = manager.get(params.agent_id)
        if record is None:
            raise RuntimeError(f"agent not found: {params.agent_id}")
        lines = [
            f"agent_id: {record.agent_id}",
            f"description: {record.description}",
            f"status: {record.status}",
        ]
        if record.finished_at:
            elapsed = record.finished_at - record.started_at
            lines.append(f"elapsed: {elapsed:.1f}s")
        if record.error:
            lines.append(f"error: {record.error}")
        if record.result:
            lines.append(f"result:\n{record.result}")
        elif record.status == "completed":
            lines.append("result: (no output)")
        return "\n".join(lines)

    # --- agent.list tool ---

    class AgentListInput(BaseModel):
        pass

    @register(name="agent.list", short_description="List all sub-agents", model=AgentListInput)
    def agent_list(_params: AgentListInput) -> str:
        """List all sub-agent invocations with their status."""
        records = manager.list_agents()
        if not records:
            return "(no agents)"
        rows: list[str] = []
        for rec in records:
            elapsed = ""
            if rec.finished_at:
                elapsed = f" ({rec.finished_at - rec.started_at:.1f}s)"
            rows.append(f"{rec.agent_id} [{rec.status}]{elapsed} {rec.description}")
        return "\n".join(rows)


async def _run_background(
    runtime: AppRuntime,
    manager: AgentManager,
    record: AgentRecord,
    prompt: str,
    model: str | None,
    system_prompt: str | None,
    allowed_tools: set[str] | None,
) -> None:
    """Execute a sub-agent in the background and update its record on completion."""
    try:
        result = await runtime.handle_input(
            record.session_id,
            prompt,
            model=model,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
        )
        record.status = "completed" if not result.error else "error"
        record.result = result.visible_text.strip() if result.visible_text else ""
        record.error = result.error
    except Exception as exc:
        logger.exception("agent.background.error agent_id={}", record.agent_id)
        record.status = "error"
        record.error = str(exc)
    finally:
        record.finished_at = time.time()
        logger.info(
            "agent.background.done agent_id={} status={} elapsed={:.1f}s",
            record.agent_id,
            record.status,
            record.finished_at - record.started_at,
        )


def _format_result(record: AgentRecord) -> str:
    """Format agent result for tool output."""
    parts: list[str] = [f"agent_id: {record.agent_id}"]
    if record.result:
        parts.append(record.result)
    if record.error:
        parts.append(f"(agent error: {record.error})")
    if not record.result and not record.error:
        parts.append("(agent returned no output)")
    return "\n".join(parts)
