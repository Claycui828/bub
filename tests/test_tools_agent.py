import asyncio
import inspect
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from republic import ToolContext

from bub.tools.agent import AgentManager, get_agent_manager, register_agent_tools
from bub.tools.registry import ToolRegistry


@dataclass
class _FakeLoopResult:
    visible_text: str = ""
    error: str | None = None


class _FakeRuntime:
    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}
        self.handle_input = AsyncMock(return_value=_FakeLoopResult(visible_text="sub-agent done"))


def _build_registry(runtime: _FakeRuntime) -> ToolRegistry:
    registry = ToolRegistry()
    register_agent_tools(registry, runtime=runtime)  # type: ignore[arg-type]
    return registry


def _ctx(session_id: str = "sess1") -> ToolContext:
    return ToolContext("test", "test", state={"session_id": session_id})


async def _run(registry: ToolRegistry, name: str, context: ToolContext | None = None, **kwargs: object) -> str:
    descriptor = registry.get(name)
    assert descriptor is not None, f"tool {name} not found"
    if descriptor.tool.context:
        result = descriptor.tool.run(context=context, **kwargs)
    else:
        result = descriptor.tool.run(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


@pytest.fixture(autouse=True)
def _reset_agent_manager() -> None:
    """Reset the global agent manager between tests."""
    mgr = get_agent_manager()
    mgr._records.clear()
    mgr._background_tasks.clear()
    mgr._counter = 0


class TestAgentDelegation:
    @pytest.mark.asyncio
    async def test_foreground_returns_output(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        result = await _run(registry, "agent", context=_ctx(), prompt="find TODOs", description="find TODOs")

        assert "sub-agent done" in result
        assert "agent_id: agent-1" in result
        runtime.handle_input.assert_called_once()
        args = runtime.handle_input.call_args
        assert "sess1:sub:agent-1" in args[0][0]
        assert args[0][1] == "find TODOs"

    @pytest.mark.asyncio
    async def test_foreground_with_error(self) -> None:
        runtime = _FakeRuntime()
        runtime.handle_input = AsyncMock(return_value=_FakeLoopResult(visible_text="", error="timeout"))
        registry = _build_registry(runtime)

        result = await _run(registry, "agent", context=_ctx(), prompt="slow task", description="slow")

        assert "agent error: timeout" in result

    @pytest.mark.asyncio
    async def test_foreground_partial_output_with_error(self) -> None:
        runtime = _FakeRuntime()
        runtime.handle_input = AsyncMock(
            return_value=_FakeLoopResult(visible_text="partial", error="max_steps")
        )
        registry = _build_registry(runtime)

        result = await _run(registry, "agent", context=_ctx(), prompt="big task", description="big")

        assert "partial" in result
        assert "max_steps" in result

    @pytest.mark.asyncio
    async def test_foreground_empty_output(self) -> None:
        runtime = _FakeRuntime()
        runtime.handle_input = AsyncMock(return_value=_FakeLoopResult(visible_text=""))
        registry = _build_registry(runtime)

        result = await _run(registry, "agent", context=_ctx(), prompt="quiet", description="quiet")

        assert "no output" in result


class TestAgentModelOverride:
    @pytest.mark.asyncio
    async def test_model_passed_through(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        await _run(
            registry, "agent", context=_ctx(),
            prompt="analyze code", description="analyze", model="openrouter:anthropic/claude-sonnet-4",
        )

        kwargs = runtime.handle_input.call_args.kwargs
        assert kwargs["model"] == "openrouter:anthropic/claude-sonnet-4"

    @pytest.mark.asyncio
    async def test_system_prompt_passed_through(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        await _run(
            registry, "agent", context=_ctx(),
            prompt="task", description="test", system_prompt="You are a code reviewer.",
        )

        kwargs = runtime.handle_input.call_args.kwargs
        assert kwargs["system_prompt"] == "You are a code reviewer."

    @pytest.mark.asyncio
    async def test_allowed_tools_passed_through(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        await _run(
            registry, "agent", context=_ctx(),
            prompt="task", description="test", allowed_tools=["fs.read", "bash"],
        )

        kwargs = runtime.handle_input.call_args.kwargs
        assert kwargs["allowed_tools"] == {"fs.read", "bash"}


class TestAgentBackground:
    @pytest.mark.asyncio
    async def test_background_returns_id_immediately(self) -> None:
        runtime = _FakeRuntime()
        # Make handle_input slow so we can verify it returns before completion.
        async def slow_input(*args: Any, **kwargs: Any) -> _FakeLoopResult:
            await asyncio.sleep(0.5)
            return _FakeLoopResult(visible_text="bg done")

        runtime.handle_input = AsyncMock(side_effect=slow_input)
        registry = _build_registry(runtime)

        result = await _run(
            registry, "agent", context=_ctx(),
            prompt="slow task", description="bg test", run_in_background=True,
        )

        assert "agent-1" in result
        assert "background" in result

        # Wait for the background task to complete.
        mgr = get_agent_manager()
        bg_task = mgr._background_tasks.get("agent-1")
        assert bg_task is not None
        await bg_task

        record = mgr.get("agent-1")
        assert record is not None
        assert record.status == "completed"
        assert record.result == "bg done"

    @pytest.mark.asyncio
    async def test_background_error_captured(self) -> None:
        runtime = _FakeRuntime()
        runtime.handle_input = AsyncMock(return_value=_FakeLoopResult(visible_text="", error="crash"))
        registry = _build_registry(runtime)

        await _run(
            registry, "agent", context=_ctx(),
            prompt="crash task", description="crash", run_in_background=True,
        )

        mgr = get_agent_manager()
        bg_task = mgr._background_tasks.get("agent-1")
        await bg_task

        record = mgr.get("agent-1")
        assert record.status == "error"
        assert record.error == "crash"


class TestAgentResume:
    @pytest.mark.asyncio
    async def test_resume_continues_session(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        # First call.
        result1 = await _run(
            registry, "agent", context=_ctx(),
            prompt="start research", description="research",
        )
        assert "agent-1" in result1

        # Resume with follow-up prompt.
        runtime.handle_input = AsyncMock(return_value=_FakeLoopResult(visible_text="continued result"))
        result2 = await _run(
            registry, "agent", context=_ctx(),
            prompt="now summarize", description="summarize", resume="agent-1",
        )

        assert "continued result" in result2
        # Verify same session_id was reused.
        session_id_used = runtime.handle_input.call_args[0][0]
        assert "sess1:sub:agent-1" == session_id_used

    @pytest.mark.asyncio
    async def test_resume_not_found(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        with pytest.raises(RuntimeError, match="agent not found"):
            await _run(
                registry, "agent", context=_ctx(),
                prompt="continue", description="test", resume="nonexistent",
            )

    @pytest.mark.asyncio
    async def test_resume_still_running(self) -> None:
        runtime = _FakeRuntime()
        async def slow(*args: Any, **kwargs: Any) -> _FakeLoopResult:
            await asyncio.sleep(10)
            return _FakeLoopResult(visible_text="done")

        runtime.handle_input = AsyncMock(side_effect=slow)
        registry = _build_registry(runtime)

        # Start a background agent.
        await _run(
            registry, "agent", context=_ctx(),
            prompt="long task", description="long", run_in_background=True,
        )

        # Try to resume while still running.
        with pytest.raises(RuntimeError, match="still running"):
            await _run(
                registry, "agent", context=_ctx(),
                prompt="continue", description="test", resume="agent-1",
            )

        # Cancel the background task.
        mgr = get_agent_manager()
        bg_task = mgr._background_tasks.get("agent-1")
        bg_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bg_task


class TestAgentStatusAndList:
    @pytest.mark.asyncio
    async def test_status_shows_completed(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        await _run(registry, "agent", context=_ctx(), prompt="task", description="test task")

        status = await _run(registry, "agent.status", agent_id="agent-1")
        assert "completed" in status
        assert "sub-agent done" in status
        assert "test task" in status

    @pytest.mark.asyncio
    async def test_status_not_found(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        with pytest.raises(RuntimeError, match="agent not found"):
            await _run(registry, "agent.status", agent_id="nonexistent")

    @pytest.mark.asyncio
    async def test_list_agents(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        await _run(registry, "agent", context=_ctx(), prompt="task1", description="first")
        await _run(registry, "agent", context=_ctx(), prompt="task2", description="second")

        listing = await _run(registry, "agent.list")
        assert "agent-1" in listing
        assert "agent-2" in listing
        assert "first" in listing
        assert "second" in listing

    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        runtime = _FakeRuntime()
        registry = _build_registry(runtime)

        listing = await _run(registry, "agent.list")
        assert listing == "(no agents)"
