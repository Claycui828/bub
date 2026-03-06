"""Agent delegation tool."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field
from republic import ToolContext

from bub.tools.registry import ToolGuidance, ToolRegistry

if TYPE_CHECKING:
    from bub.app.runtime import AppRuntime


class AgentInput(BaseModel):
    prompt: str = Field(
        ...,
        description="Complete task description for the sub-agent. Must be self-contained — include ALL necessary context, file paths, requirements, and constraints. The sub-agent has NO access to the parent conversation history.",
    )
    description: str = Field(default="", description="Short label for this delegation (3-5 words), shown in agent.list output")
    model: str | None = Field(
        default=None,
        description="Override model for this agent. E.g. 'openrouter:anthropic/claude-sonnet-4'. Defaults to parent's model.",
    )
    system_prompt: str | None = Field(default=None, description="Override system prompt. Defaults to parent's system prompt.")
    allowed_tools: list[str] | None = Field(
        default=None,
        description="Restrict which tools are available. E.g. ['bash', 'fs.read', 'fs.write']. Defaults to all tools.",
    )
    run_in_background: bool = Field(
        default=False,
        description="If true, return immediately with an agent_id. Check progress with agent.status. Use for parallel work.",
    )
    resume: str | None = Field(
        default=None,
        description="Agent ID from a previous invocation to resume. The agent keeps its full conversation history.",
    )


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
        short_description="Delegate a task to an isolated sub-agent and return its result",
        model=AgentInput,
        context=True,
        always_expand=True,
        guidance=ToolGuidance(
            when_to_use="Complex subtasks that benefit from isolation, parallel work via run_in_background, or tasks requiring a different model/tools.",
            when_not_to="Simple queries or tasks that need access to the parent conversation context. Sub-agents start with a clean tape.",
            constraints="Sub-agent has no access to the parent's conversation history. Provide all necessary context in the prompt. Results are returned as text.",
        ),
    )
    async def agent_delegate(params: AgentInput, context: ToolContext) -> str:
        """Spawn an isolated sub-agent session to handle a complex task autonomously.

        The sub-agent runs in a completely isolated session with a fresh conversation tape.
        It has NO access to the parent's conversation history — you must provide all necessary
        context in the prompt.

        How it works:
        1. A new session is created with its own tape and tool set.
        2. The prompt is sent as the user message to the sub-agent.
        3. The sub-agent executes its think-act loop until completion.
        4. The result (final text output + action summary) is returned to you.
        5. The sub-session is cleaned up (tape is preserved on disk).

        Features:
        - model: use a different model (e.g. cheaper/faster for simple tasks).
        - run_in_background: return immediately with an agent_id for parallel work.
          Check status later with agent.status. Launch multiple background agents for parallelism.
        - resume: continue a previous agent by passing its agent_id. The agent keeps
          its full conversation history, enabling multi-turn interactions.
        - allowed_tools: restrict tool access for safety or focus.
        - system_prompt: customize the agent's behavior.

        The result includes a structured action summary: which tools were called,
        which files were modified, and which commands were run.
        """
        parent_session_id = context.state.get("session_id", "")

        # Emit live events to parent session for CLI visibility.
        parent_session = runtime._sessions.get(parent_session_id)
        emit = parent_session.model_runner._emit_live if parent_session else lambda *a, **k: None

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
            record.result = result.assistant_output.strip() if result.assistant_output else ""
            record.error = result.error
            record.finished_at = time.time()
            tape_name = _get_session_tape_name(runtime, record.session_id)
            action_summary = _extract_action_summary(runtime, record.session_id)
            return _format_result(record, tape_name=tape_name, action_summary=action_summary)

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
        emit("sub_agent.start", {
            "agent_id": agent_id,
            "description": record.description,
            "prompt": params.prompt,
            "model": params.model or "",
        })

        result = await runtime.handle_input(
            sub_session_id,
            params.prompt,
            model=params.model,
            system_prompt=params.system_prompt,
            allowed_tools=tool_set,
        )
        record.status = "completed" if not result.error else "error"
        record.result = result.assistant_output.strip() if result.assistant_output else ""
        record.error = result.error
        record.finished_at = time.time()

        # Capture tape name and action summary before removing session.
        tape_name = _get_session_tape_name(runtime, sub_session_id)
        action_summary = _extract_action_summary(runtime, sub_session_id)

        emit("sub_agent.end", {
            "agent_id": agent_id,
            "status": record.status,
            "result": record.result,
            "error": record.error,
        })

        # Clean up sub-session tape and memory; resume will create a fresh session.
        runtime.remove_session(sub_session_id)

        return _format_result(record, tape_name=tape_name, action_summary=action_summary)

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


def _get_session_tape_name(runtime: AppRuntime, session_id: str) -> str | None:
    """Get the tape name for a session, if it exists."""
    session = runtime._sessions.get(session_id)
    if session is None:
        return None
    return session.tape._tape.name


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
        record.result = result.assistant_output.strip() if result.assistant_output else ""
        record.error = result.error
    except Exception as exc:
        logger.exception("agent.background.error agent_id={}", record.agent_id)
        record.status = "error"
        record.error = str(exc)
    finally:
        record.finished_at = time.time()
        tape_name = _get_session_tape_name(runtime, record.session_id)
        if tape_name:
            record.result = f"tape: {tape_name}\n{record.result}" if record.result else f"tape: {tape_name}"
        logger.info(
            "agent.background.done agent_id={} status={} elapsed={:.1f}s",
            record.agent_id,
            record.status,
            record.finished_at - record.started_at,
        )


def _parse_tool_call(call: object) -> tuple[str, dict[str, object]] | None:
    """Extract (name, args) from a single tool call dict."""
    if not isinstance(call, dict):
        return None
    func = call.get("function")
    if not isinstance(func, dict):
        return None
    name = func.get("name", "")
    args_raw = func.get("arguments", "")
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
    except (json.JSONDecodeError, TypeError):
        args = {}
    return (name, args) if isinstance(args, dict) else (name, {})


def _collect_tool_calls(entries: list[object]) -> list[tuple[str, dict[str, object]]]:
    """Collect parsed (name, args) pairs from tape entries."""
    from republic.tape import TapeEntry as _TE

    results: list[tuple[str, dict[str, object]]] = []
    for entry in entries:
        if not isinstance(entry, _TE) or entry.kind != "tool_call":
            continue
        calls = entry.payload.get("calls")
        if not isinstance(calls, list):
            continue
        for raw_call in calls:
            parsed = _parse_tool_call(raw_call)
            if parsed is not None:
                results.append(parsed)
    return results


def _extract_action_summary(runtime: AppRuntime, session_id: str) -> str | None:
    """Extract a structured summary of actions from a sub-agent's tape."""
    session = runtime._sessions.get(session_id)
    if session is None:
        return None
    entries = session.tape._store.read(session.tape._tape.name)
    if not entries:
        return None

    tool_counts: dict[str, int] = {}
    files_modified: list[str] = []
    commands_run: list[str] = []

    for name, args in _collect_tool_calls(entries):
        tool_counts[name] = tool_counts.get(name, 0) + 1
        if name == "bash" and "cmd" in args:
            commands_run.append(str(args["cmd"])[:80])
        elif name in ("fs_write", "fs_edit") and "path" in args:
            path = str(args["path"])
            if path not in files_modified:
                files_modified.append(path)

    if not tool_counts:
        return None

    lines = [f"tools: {', '.join(f'{n}({c})' for n, c in sorted(tool_counts.items()))}"]
    if files_modified:
        lines.append(f"files: {', '.join(files_modified)}")
    if commands_run:
        lines.append(f"commands: {'; '.join(commands_run[:5])}")
    return "\n".join(lines)


def _format_result(record: AgentRecord, *, tape_name: str | None = None, action_summary: str | None = None) -> str:
    """Format agent result for tool output."""
    parts: list[str] = [f"agent_id: {record.agent_id}"]
    if tape_name:
        parts.append(f"tape: {tape_name}")
    if action_summary:
        parts.append(action_summary)
    if record.result:
        parts.append(record.result)
    if record.error:
        parts.append(f"(agent error: {record.error})")
    if not record.result and not record.error:
        parts.append("(agent returned no output)")
    return "\n".join(parts)
