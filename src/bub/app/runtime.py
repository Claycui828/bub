"""Application runtime and session management."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import signal
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from hashlib import md5
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler
from loguru import logger

from bub.app.jobstore import JSONJobStore
from bub.config.settings import Settings
from bub.core import AgentLoop, InputRouter, LoopResult, ModelRunner
from bub.integrations.republic_client import build_llm, build_tape_store, read_workspace_agents_prompt
from bub.mcp.client import McpClientManager, load_mcp_configs
from bub.observability import current_tracer
from bub.observability.factory import build_tracer
from bub.observability.tracer import set_tracer
from bub.skills.loader import SkillMetadata, discover_skills
from bub.tape import TapeService, default_tape_context
from bub.tools import ProgressiveToolView, ToolRegistry
from bub.tools.builtin import register_builtin_tools

if TYPE_CHECKING:
    from bub.channels.manager import ChannelManager


def _session_slug(session_id: str) -> str:
    return md5(session_id.encode("utf-8")).hexdigest()[:16]  # noqa: S324


@dataclass
class SessionRuntime:
    """Runtime state for one deterministic session."""

    session_id: str
    loop: AgentLoop
    tape: TapeService
    model_runner: ModelRunner
    tool_view: ProgressiveToolView

    async def handle_input(self, text: str) -> LoopResult:
        from bub.tape.context import HANDOFF_STATE_KEY

        await self.tape.ensure_bootstrap_anchor()
        with self.tape.fork_tape() as tape:
            ctx_state: dict[str, object] = {"session_id": self.session_id}
            anchor_state = self.tape.last_anchor_state()
            if anchor_state:
                ctx_state[HANDOFF_STATE_KEY] = anchor_state
            tape.context = default_tape_context(ctx_state)
            return await self.loop.handle_input(text)

    def reset_context(self) -> None:
        """Clear volatile in-memory context while keeping the same session identity."""
        self.model_runner.reset_context()
        self.tool_view.reset()

    def inject_message(self, text: str) -> None:
        """Queue a user message to be injected into the agent's next loop step."""
        self.model_runner.inject_message(text)


class AppRuntime:
    """Global runtime that manages multiple session loops."""

    def __init__(
        self,
        workspace: Path,
        settings: Settings,
        *,
        allowed_tools: set[str] | None = None,
        allowed_skills: set[str] | None = None,
        enable_scheduler: bool = True,
    ) -> None:
        self.workspace = workspace.resolve()
        self.settings = settings
        self._allowed_skills = _normalize_name_set(allowed_skills)
        self._allowed_tools = _normalize_name_set(allowed_tools)
        self._store = build_tape_store(settings, self.workspace)
        self.scheduler = self._default_scheduler()
        self._llm = build_llm(settings, self._store)
        self._sessions: dict[str, SessionRuntime] = {}
        self._active_inputs: set[asyncio.Task[LoopResult]] = set()
        self._enable_scheduler = enable_scheduler

        # Observability.
        self._tracer = build_tracer(settings)
        set_tracer(self._tracer)

        # MCP client manager.
        mcp_configs = load_mcp_configs(self.workspace, settings.resolve_home())
        self._mcp = McpClientManager(mcp_configs)
        self._mcp_connected = False

    def _default_scheduler(self) -> BaseScheduler:
        job_store = JSONJobStore(self.settings.resolve_home() / "jobs.json")
        return BackgroundScheduler(daemon=True, jobstores={"default": job_store})

    def __enter__(self) -> AppRuntime:
        if not self.scheduler.running and self._enable_scheduler:
            self.scheduler.start()
            self._register_tape_cleanup_job()
        return self

    def _register_tape_cleanup_job(self) -> None:
        """Register a weekly job to clean up stale sub-agent tapes."""
        from apscheduler.triggers.interval import IntervalTrigger

        job_id = "bub.tape.cleanup"
        if self.scheduler.get_job(job_id):
            return
        self.scheduler.add_job(
            _run_tape_cleanup,
            trigger=IntervalTrigger(weeks=1),
            id=job_id,
            kwargs={
                "home": str(self.settings.resolve_home()),
                "workspace": str(self.workspace),
                "max_age_days": 7,
            },
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._mcp_connected:
            with suppress(Exception):
                asyncio.get_event_loop().run_until_complete(self._mcp.close())
        if self.scheduler.running and self._enable_scheduler:
            with suppress(Exception):
                self.scheduler.shutdown()
        with suppress(Exception):
            self._tracer.shutdown()

    async def connect_mcp(self) -> int:
        """Connect to configured MCP servers. Returns number of tools discovered."""
        if self._mcp_connected or not self._mcp._configs:
            return 0
        tools = await self._mcp.connect_all()
        self._mcp_connected = True
        logger.info("mcp.client.ready tools={}", len(tools))
        return len(tools)

    def _register_mcp_tools(self, registry: ToolRegistry) -> None:
        """Register discovered MCP tools into a session's registry."""
        if not self._mcp_connected:
            return
        from bub.mcp.bridge import register_mcp_tools

        count = register_mcp_tools(registry, self._mcp)
        if count:
            logger.info("mcp.tools.registered count={}", count)

    def discover_skills(self) -> list[SkillMetadata]:
        discovered = discover_skills(self.workspace)
        if self._allowed_skills is None:
            return discovered
        return [skill for skill in discovered if skill.name.casefold() in self._allowed_skills]

    def get_session(
        self,
        session_id: str,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: set[str] | None = None,
    ) -> SessionRuntime:
        existing = self._sessions.get(session_id)
        if existing is not None:
            return existing

        effective_model = model or self.settings.model
        effective_system_prompt = system_prompt if system_prompt is not None else self.settings.system_prompt

        # For sub-agent sessions with a different model, build a dedicated LLM.
        if model and model != self.settings.model:
            from bub.config.settings import Settings

            sub_settings = Settings(
                model=model,
                api_key=self.settings.api_key,
                api_base=self.settings.api_base,
                llm_api_key=self.settings.llm_api_key,
                openrouter_api_key=self.settings.openrouter_api_key,
            )
            llm = build_llm(sub_settings, self._store)
        else:
            llm = self._llm

        tape_name = f"{self.settings.tape_name}:{_session_slug(session_id)}"
        tape = TapeService(llm, tape_name, store=self._store)

        effective_allowed_tools = _normalize_name_set(allowed_tools) if allowed_tools is not None else self._allowed_tools
        registry = ToolRegistry(effective_allowed_tools)
        register_builtin_tools(registry, workspace=self.workspace, tape=tape, runtime=self)
        self._register_mcp_tools(registry)
        tool_view = ProgressiveToolView(registry)
        router = InputRouter(registry, tool_view, tape, self.workspace)
        runner = ModelRunner(
            tape=tape,
            router=router,
            tool_view=tool_view,
            tools=registry.model_tools(),
            list_skills=self.discover_skills,
            model=effective_model,
            max_steps=self.settings.max_steps,
            max_tokens=self.settings.max_tokens,
            model_timeout_seconds=self.settings.model_timeout_seconds,
            base_system_prompt=effective_system_prompt,
            get_workspace_system_prompt=lambda: read_workspace_agents_prompt(self.workspace),
        )
        loop = AgentLoop(router=router, model_runner=runner, tape=tape)
        runtime = SessionRuntime(session_id=session_id, loop=loop, tape=tape, model_runner=runner, tool_view=tool_view)
        self._sessions[session_id] = runtime
        return runtime

    async def handle_input(
        self,
        session_id: str,
        text: str,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: set[str] | None = None,
    ) -> LoopResult:
        session = self.get_session(
            session_id, model=model, system_prompt=system_prompt, allowed_tools=allowed_tools
        )
        tracer = current_tracer()
        with tracer.trace(
            "session.handle_input",
            input=text,
            metadata={"session_id": session_id, "model": model or self.settings.model},
        ) as trace_span:
            task = asyncio.create_task(session.handle_input(text))
            self._active_inputs.add(task)
            try:
                result = await task
                trace_span.end(
                    output=result.assistant_output[:4096] if result.assistant_output else None,
                    metadata={"steps": result.steps, "error": result.error},
                    level="ERROR" if result.error else "DEFAULT",
                )
                return result
            except Exception as exc:
                trace_span.end(output=f"exception: {exc!s}", level="ERROR")
                raise
            finally:
                self._active_inputs.discard(task)
                tracer.flush()

    async def _cancel_active_inputs(self) -> int:
        """Cancel all in-flight input tasks and return canceled count."""
        count = 0
        while self._active_inputs:
            task = self._active_inputs.pop()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            count += 1
        return count

    def remove_session(self, session_id: str, *, keep_tape: bool = True) -> None:
        """Remove a session from memory, optionally preserving its tape on disk.

        keep_tape=True (default): only frees in-memory objects; tape file stays
        on disk so resume can rebuild the session with full history.
        keep_tape=False: also deletes the tape file.
        """
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        if not keep_tape:
            self._store.reset(session.tape._tape.name)
        logger.info("session.removed session_id={} keep_tape={}", session_id, keep_tape)

    def reset_session_context(self, session_id: str) -> None:
        """Reset volatile context for an already-created session."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.reset_context()

    @contextlib.asynccontextmanager
    async def graceful_shutdown(self) -> AsyncGenerator[asyncio.Event, None]:
        """Run the runtime indefinitely with graceful shutdown."""
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        handled_signals: list[signal.Signals] = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
                handled_signals.append(sig)
            except (NotImplementedError, RuntimeError):
                continue
        current_task = asyncio.current_task()
        future = asyncio.ensure_future(stop_event.wait())
        future.add_done_callback(lambda _, task=current_task: task and task.cancel())  # type: ignore[misc]
        try:
            yield stop_event
        finally:
            future.cancel()
            cancelled = await self._cancel_active_inputs()
            if cancelled:
                logger.info("runtime.cancel_inflight count={}", cancelled)
            for sig in handled_signals:
                with suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)

    def install_hooks(self, channel_manager: ChannelManager) -> None:
        """Install hooks for cross-cutting concerns like channel integration."""

        hooks_module_str = os.getenv("BUB_HOOKS_MODULE")
        if not hooks_module_str:
            return
        try:
            module = importlib.import_module(hooks_module_str)
        except ImportError as e:
            raise ImportError(f"Failed to import hooks module '{hooks_module_str}'") from e
        if not hasattr(module, "install"):
            raise AttributeError(f"Hooks module '{hooks_module_str}' does not have an 'install' function")
        hooks_context = SimpleNamespace(
            runtime=self,
            register_channel=channel_manager.register,
            default_channels=channel_manager.default_channels,
        )
        module.install(hooks_context)


def _run_tape_cleanup(home: str, workspace: str, max_age_days: int = 7) -> None:
    """Standalone function for scheduled tape cleanup (must be picklable)."""
    from bub.tape.store import FileTapeStore

    store = FileTapeStore(Path(home), Path(workspace))
    removed = store.cleanup_stale_tapes(max_age_days=max_age_days)
    if removed:
        logger.info("tape.cleanup.scheduled removed={} tapes", len(removed))


def _normalize_name_set(raw: set[str] | None) -> set[str] | None:
    if raw is None:
        return None

    normalized = {name.strip().casefold() for name in raw if name.strip()}
    return normalized or None
