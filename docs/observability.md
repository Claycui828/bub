# Observability

Bub provides a pluggable observability system for tracing and monitoring agent execution.

## Overview

The observability module (`bub.observability`) provides:

- **Tracer abstraction** - A unified interface for creating trace spans
- **Multiple backends** - Support for different tracing providers
- **Bub-specific span types** - Specialized spans for Bub's core operations

## Architecture

```
┌─────────────────────────────────────────┐
│           Application Code              │
│  (model_runner, tools, channels, etc.)  │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│           Tracer API                    │
│  tracer.trace()                         │
│  tracer.span()                          │
│  tracer.generation()                    │
│  tracer.tool()                         │
│  tracer.router()                       │
│  tracer.channel()                      │
│  tracer.tape()                         │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│         TracerBackend (Protocol)        │
├─────────────────────────────────────────┤
│  • NullBackend (no-op)                 │
│  • OtelBackend (OpenTelemetry)         │
│  • LangfuseBackend (Langfuse)          │
└─────────────────────────────────────────┘
```

## Span Types

Bub defines several specialized span types for different operations:

| Span Type | Use Case | Key Attributes |
|-----------|----------|----------------|
| `Span` | Generic operation | trace_id, span_id, name |
| `GenerationSpan` | LLM API calls | model, input_messages, usage |
| `ToolSpan` | Tool execution | tool_name, tool_input |
| `RouterSpan` | Input routing | input_text, detected_command |
| `ChannelSpan` | Channel messages | channel_type, message_id, sender_id |
| `TapeSpan` | Tape operations | operation |

## Usage

### Basic Usage

```python
from bub.observability import current_tracer, set_tracer, Tracer, NullBackend

# Get the current tracer
tracer = current_tracer()

# Start a root trace
with tracer.trace("my.operation") as span:
    # Do work...
    span.update(metadata={"key": "value"})
# Span automatically ended on exit
```

### Using Specialized Spans

```python
# LLM generation span
with tracer.generation("llm.chat", model="gpt-4", input_messages=[...]) as gen:
    # Call LLM...
    gen.end(output="response", usage={"total_tokens": 100})

# Tool execution span
with tracer.tool("tool.bash", tool_name="bash", tool_input={"cmd": "ls"}) as span:
    # Execute tool...
    span.end(output="file1.py")

# Router span
with tracer.router("router.detect", input_text="/help") as span:
    # Detect command...
    span.end(output={"command": "help"})

# Channel span
with tracer.channel("channel.feishu", channel_type="feishu", message_id="om_123") as span:
    # Handle message...
    span.end(output="handled")

# Tape span
with tracer.tape("tape.append", operation="append") as span:
    # Append to tape...
    span.end(output={"events": 2})
```

### Configuring a Backend

```python
from bub.observability import set_tracer, Tracer
from bub.observability.langfuse_tracer import LangfuseBackend
from bub.observability.otel_tracer import OtelBackend

# Use Langfuse
backend = LangfuseBackend()
set_tracer(Tracer(backend))

# Use OpenTelemetry
backend = OtelBackend(service_name="my-bub")
set_tracer(Tracer(backend))
```

## Environment Variables

### OpenTelemetry

| Variable | Description | Default |
|----------|-------------|---------|
| `OTEL_SERVICE_NAME` | Service name for traces | `bub` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint | `http://localhost:4317` |

### Langfuse

| Variable | Description |
|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Public key |
| `LANGFUSE_SECRET_KEY` | Secret key |
| `LANGFUSE_HOST` | API host |

## Adding a New Backend

Implement the `TracerBackend` protocol:

```python
from bub.observability.tracer import (
    Span, GenerationSpan, ToolSpan, RouterSpan, 
    ChannelSpan, TapeSpan, TracerBackend, _gen_id
)
from typing import Any

class MyBackend(TracerBackend):
    def start_trace(self, name: str, *, input: Any = None, 
                    metadata: dict[str, Any] | None = None) -> Span:
        # Create and return a Span
        ...

    def start_span(self, name: str, *, parent: Span | None = None,
                   input: Any = None, 
                   metadata: dict[str, Any] | None = None) -> Span:
        ...

    def start_generation(self, name: str, *, parent: Span | None = None,
                         model: str = "",
                         input_messages: list[dict[str, Any]] | None = None,
                         metadata: dict[str, Any] | None = None) -> GenerationSpan:
        ...

    def start_tool(self, name: str, *, parent: Span | None = None,
                   tool_name: str = "",
                   tool_input: dict[str, Any] | None = None,
                   metadata: dict[str, Any] | None = None) -> ToolSpan:
        ...

    def start_router(self, name: str, *, parent: Span | None = None,
                     input_text: str = "",
                     metadata: dict[str, Any] | None = None) -> RouterSpan:
        ...

    def start_channel(self, name: str, *, parent: Span | None = None,
                      channel_type: str = "",
                      message_id: str = "",
                      sender_id: str = "",
                      metadata: dict[str, Any] | None = None) -> ChannelSpan:
        ...

    def start_tape(self, name: str, *, parent: Span | None = None,
                   operation: str = "",
                   metadata: dict[str, Any] | None = None) -> TapeSpan:
        ...

    def end_span(self, span: Span, *, output: Any = None,
                 metadata: dict[str, Any] | None = None,
                 level: str = "DEFAULT") -> None:
        ...

    def end_generation(self, span: GenerationSpan, *, output: Any = None,
                       metadata: dict[str, Any] | None = None,
                       level: str = "DEFAULT",
                       usage: dict[str, int] | None = None) -> None:
        ...

    # ... implement all end_* methods

    def update_span(self, span: Span, *, metadata: dict[str, Any] | None = None,
                    output: Any = None) -> None:
        ...

    def flush(self) -> None:
        ...

    def shutdown(self) -> None:
        ...
```

## Testing

Run observability tests:

```bash
pytest tests/test_observability.py -v
```
