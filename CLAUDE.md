# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development

```bash
# Install dependencies (uses uv)
uv sync

# Run all tests
just test              # or: uv run pytest

# Run a single test file
uv run pytest tests/test_observability.py

# Run a single test by name
uv run pytest tests/test_observability.py::TestTracerContextPropagation::test_nested_spans_propagate_parent

# Lint & format
just lint              # or: uv run ruff check src/ tests/
just format            # or: uv run ruff format src/ tests/

# Type check
uv run mypy

# Run the CLI
uv run bub
```

## Architecture

Bub is a collaborative coding agent built on the **Republic** framework. Republic provides `LLM`, `Tool`, `ToolContext`, `Tape`, and `TapeEntry` primitives.

### Core Pipeline

```
Channel (CLI/Telegram/Discord)
  → AppRuntime.handle_input()
    → SessionRuntime (per-session isolation)
      → InputRouter (decides: agent loop vs direct response)
        → AgentLoop (think-act cycle)
          → ModelRunner._chat() (LLM call)
          → ToolRegistry.execute() (tool dispatch)
```

### Key Modules

- **`src/bub/app/runtime.py`** — `AppRuntime` manages sessions, tracer lifecycle, and the main `handle_input()` entry point. `SessionRuntime` holds per-session state (tape, tools, LLM).
- **`src/bub/core/model_runner.py`** — `ModelRunner` runs the agent loop: LLM call → tool execution → repeat. Each step is traced.
- **`src/bub/core/input_router.py`** — Routes user input to agent loop or direct response based on heuristics.
- **`src/bub/tools/registry.py`** — `ToolRegistry` with decorator-based registration. Tools use Pydantic models for schema, converted via `tool_from_model()`.
- **`src/bub/tools/builtin.py`** — Registers all built-in tools (bash, file ops, web, task, agent).
- **`src/bub/config/settings.py`** — Pydantic Settings with `BUB_` env prefix. All config via environment variables.
- **`src/bub/channels/`** — `BaseChannel` ABC with CLI, Telegram, Discord adapters.
- **`src/bub/skills/`** — SKILL.md-based discovery from project/global/builtin roots.

### Tape System

Bub uses an append-only `Tape` for conversation history. Key operations:
- `anchor()`/`handoff()` — phase transitions
- `fork()`/`merge()` — sub-agent isolation (used by agent delegation tool)

### Observability

Abstract `Tracer` with `ContextVar`-based span propagation. Three backends:
- `NullTracer` — zero overhead when disabled
- `LangfuseBackend` — Langfuse v3 API (uses `start_span()`, not `client.trace()`)
- `OtelBackend` — OpenTelemetry with OTLP gRPC exporter

Configured via `BUB_TRACE_ENABLED`, `BUB_TRACE_BACKEND`, and backend-specific env vars.

### Tool Registration Pattern

```python
from republic import Tool
from pydantic import BaseModel
from bub.tools.registry import ToolRegistry, tool_from_model

class MyToolInput(BaseModel):
    arg: str

def register_my_tools(registry: ToolRegistry):
    @registry.register(tool_from_model("my.tool", MyToolInput, description="..."))
    async def my_tool(ctx, arg: str) -> str:
        return "result"
```

### ProgressiveToolView

Tools are prompted in two phases: compact list first, then expanded schema only when the model requests `$hint` for a specific tool. This reduces prompt size.

## Code Style

- Python 3.12+, line length 120
- Ruff for linting and formatting (`ruff check`, `ruff format`)
- `from __future__ import annotations` in all modules
- Mypy with `ignore_missing_imports = true`; `src/bub/skills/` is excluded from type checking
- Tests use `pytest` with `pytest-asyncio`; test files in `tests/`

## Langfuse v3 API Notes

When working with the Langfuse backend (`langfuse_tracer.py`):
- No `client.trace()` in v3 — use `client.start_span()` which implicitly creates a trace
- `handle.end()` takes **no arguments** — call `handle.update(output=..., level=...)` first, then `handle.end()`
- Nesting: use `parent_handle.start_span()` / `parent_handle.start_generation()`
