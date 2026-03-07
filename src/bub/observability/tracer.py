







"""Enhanced observability tracer with Bub-specific span types."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol, Self, runtime_checkable


@dataclass
class Span:
    """A traced operation span."""

    trace_id: str
    span_id: str
    name: str
    _backend: TracerBackend
    _handle: Any = None
    _ended: bool = False

    def end(self, *, output: Any = None, metadata: dict[str, Any] | None = None, level: str = "DEFAULT") -> None:
        if self._ended:
            return
        self._ended = True
        self._backend.end_span(self, output=output, metadata=metadata, level=level)
        # Restore parent span.
        _current_span.set(_get_span_parents().pop(self.span_id, None))

    def update(self, *, metadata: dict[str, Any] | None = None, output: Any = None) -> None:
        self._backend.update_span(self, metadata=metadata, output=output)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if not self._ended:
            error_output = f"error: {exc_val!s}" if exc_val else None
            level = "ERROR" if exc_val else "DEFAULT"
            self.end(output=error_output, level=level)


@dataclass
class GenerationSpan(Span):
    """A span specifically for LLM generation calls."""

    model: str = ""
    input_messages: list[dict[str, Any]] = field(default_factory=list)

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        usage: dict[str, int] | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True
        self._backend.end_generation(self, output=output, metadata=metadata, level=level, usage=usage)
        _current_span.set(_get_span_parents().pop(self.span_id, None))

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if not self._ended:
            error_output = f"error: {exc_val!s}" if exc_val else None
            level = "ERROR" if exc_val else "DEFAULT"
            self.end(output=error_output, level=level)


@dataclass
class ToolSpan(Span):
    """A span specifically for tool execution in Bub."""

    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        if self._ended:
            return
        self._ended = True
        self._backend.end_tool(self, output=output, metadata=metadata, level=level)
        _current_span.set(_get_span_parents().pop(self.span_id, None))

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if not self._ended:
            error_output = f"error: {exc_val!s}" if exc_val else None
            level = "ERROR" if exc_val else "DEFAULT"
            self.end(output=error_output, level=level)


@dataclass
class RouterSpan(Span):
    """A span specifically for input routing/command detection."""

    input_text: str = ""
    detected_command: str | None = None

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        if self._ended:
            return
        self._ended = True
        self._backend.end_router(self, output=output, metadata=metadata, level=level)
        _current_span.set(_get_span_parents().pop(self.span_id, None))

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if not self._ended:
            error_output = f"error: {exc_val!s}" if exc_val else None
            level = "ERROR" if exc_val else "DEFAULT"
            self.end(output=error_output, level=level)


@dataclass
class ChannelSpan(Span):
    """A span specifically for channel message handling."""

    channel_type: str = ""  # feishu, telegram, discord, cli
    message_id: str = ""
    sender_id: str = ""

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        if self._ended:
            return
        self._ended = True
        self._backend.end_channel(self, output=output, metadata=metadata, level=level)
        _current_span.set(_get_span_parents().pop(self.span_id, None))

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if not self._ended:
            error_output = f"error: {exc_val!s}" if exc_val else None
            level = "ERROR" if exc_val else "DEFAULT"
            self.end(output=error_output, level=level)


@dataclass
class TapeSpan(Span):
    """A span specifically for tape operations (append, search, etc.)."""

    operation: str = ""  # append, search, reset, handoff

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        if self._ended:
            return
        self._ended = True
        self._backend.end_tape(self, output=output, metadata=metadata, level=level)
        _current_span.set(_get_span_parents().pop(self.span_id, None))

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if not self._ended:
            error_output = f"error: {exc_val!s}" if exc_val else None
            level = "ERROR" if exc_val else "DEFAULT"
            self.end(output=error_output, level=level)


@runtime_checkable
class TracerBackend(Protocol):
    """Backend protocol for tracing implementations."""

    def start_trace(
        self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None  # noqa: A002
    ) -> Span: ...

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None  # noqa: A002
    ) -> Span: ...

    def start_generation(
        self,
        name: str,
        *,
        parent: Span | None = None,
        model: str = "",
        input_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationSpan: ...

    def start_tool(
        self,
        name: str,
        *,
        parent: Span | None = None,
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolSpan: ...

    def start_router(
        self,
        name: str,
        *,
        parent: Span | None = None,
        input_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RouterSpan: ...

    def start_channel(
        self,
        name: str,
        *,
        parent: Span | None = None,
        channel_type: str = "",
        message_id: str = "",
        sender_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ChannelSpan: ...

    def start_tape(
        self,
        name: str,
        *,
        parent: Span | None = None,
        operation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TapeSpan: ...

    def end_span(
        self, span: Span, *, output: Any = None, metadata: dict[str, Any] | None = None, level: str = "DEFAULT"
    ) -> None: ...

    def end_generation(
        self,
        span: GenerationSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        usage: dict[str, int] | None = None,
    ) -> None: ...

    def end_tool(
        self,
        span: ToolSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None: ...

    def end_router(
        self,
        span: RouterSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None: ...

    def end_channel(
        self,
        span: ChannelSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None: ...

    def end_tape(
        self,
        span: TapeSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None: ...

    def update_span(self, span: Span, *, metadata: dict[str, Any] | None = None, output: Any = None) -> None: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


# -- Context propagation --

_current_span: ContextVar[Span | None] = ContextVar("current_span", default=None)
_span_parents: ContextVar[dict[str, Span | None] | None] = ContextVar("span_parents", default=None)


def _get_span_parents() -> dict[str, Span | None]:
    """Get the span parents dict, creating if needed."""
    parents = _span_parents.get(None)
    if parents is None:
        parents = {}
        _span_parents.set(parents)
    return parents


def _push_span(span: Span) -> None:
    """Push span onto context stack, tracking its parent."""
    parent = _current_span.get(None)
    parents = _get_span_parents()
    parents[span.span_id] = parent
    _current_span.set(span)


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


# -- Null tracer (no-op) --


class NullBackend:
    """No-op tracer backend."""

    def start_trace(self, name: str, **kwargs: Any) -> Span:
        return Span(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)

    def start_span(self, name: str, **kwargs: Any) -> Span:
        return Span(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)

    def start_generation(self, name: str, **kwargs: Any) -> GenerationSpan:
        return GenerationSpan(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)

    def start_tool(self, name: str, **kwargs: Any) -> ToolSpan:
        return ToolSpan(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)

    def start_router(self, name: str, **kwargs: Any) -> RouterSpan:
        return RouterSpan(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)

    def start_channel(self, name: str, **kwargs: Any) -> ChannelSpan:
        return ChannelSpan(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)

    def start_tape(self, name: str, **kwargs: Any) -> TapeSpan:
        return TapeSpan(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)

    def end_span(self, span: Span, **kwargs: Any) -> None:
        pass

    def end_generation(self, span: GenerationSpan, **kwargs: Any) -> None:
        pass

    def end_tool(self, span: ToolSpan, **kwargs: Any) -> None:
        pass

    def end_router(self, span: RouterSpan, **kwargs: Any) -> None:
        pass

    def end_channel(self, span: ChannelSpan, **kwargs: Any) -> None:
        pass

    def end_tape(self, span: TapeSpan, **kwargs: Any) -> None:
        pass

    def update_span(self, span: Span, **kwargs: Any) -> None:
        pass

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class Tracer:
    """High-level tracer with automatic context propagation and Bub-specific spans."""

    def __init__(self, backend: TracerBackend | None = None) -> None:
        self._backend: TracerBackend = backend or NullBackend()

    @property
    def enabled(self) -> bool:
        return not isinstance(self._backend, NullBackend)

    def trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:  # noqa: A002
        """Start a top-level trace (root span)."""
        span = self._backend.start_trace(name, input=input, metadata=metadata)
        _push_span(span)
        return span

    def span(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:  # noqa: A002
        """Start a child Span under the current span."""
        parent = _current_span.get(None)
        span = self._backend.start_span(name, parent=parent, input=input, metadata=metadata)
        _push_span(span)
        return span

    def generation(
        self,
        name: str,
        *,
        model: str = "",
        input_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationSpan:
        """Start a generation span for an LLM call."""
        parent = _current_span.get(None)
        span = self._backend.start_generation(
            name, parent=parent, model=model, input_messages=input_messages, metadata=metadata
        )
        _push_span(span)
        return span

    def tool(
        self,
        name: str,
        *,
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolSpan:
        """Start a tool execution span."""
        parent = _current_span.get(None)
        span = self._backend.start_tool(
            name, parent=parent, tool_name=tool_name, tool_input=tool_input, metadata=metadata
        )
        _push_span(span)
        return span

    def router(
        self,
        name: str,
        *,
        input_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RouterSpan:
        """Start a router/command detection span."""
        parent = _current_span.get(None)
        span = self._backend.start_router(name, parent=parent, input_text=input_text, metadata=metadata)
        _push_span(span)
        return span

    def channel(
        self,
        name: str,
        *,
        channel_type: str = "",
        message_id: str = "",
        sender_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ChannelSpan:
        """Start a channel message handling span."""
        parent = _current_span.get(None)
        span = self._backend.start_channel(
            name,
            parent=parent,
            channel_type=channel_type,
            message_id=message_id,
            sender_id=sender_id,
            metadata=metadata,
        )
        _push_span(span)
        return span

    def tape(
        self,
        name: str,
        *,
        operation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TapeSpan:
        """Start a tape operation span."""
        parent = _current_span.get(None)
        span = self._backend.start_tape(name, parent=parent, operation=operation, metadata=metadata)
        _push_span(span)
        return span

    def flush(self) -> None:
        self._backend.flush()

    def shutdown(self) -> None:
        self._backend.shutdown()


# -- Module-level singleton --

_tracer: Tracer = Tracer()


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer


def current_tracer() -> Tracer:
    return _tracer


class NullTracer(Tracer):
    """Explicit null tracer for clarity."""

    def __init__(self) -> None:
        super().__init__(NullBackend())


# Export all span types for type checking
__all__ = [
    "ChannelSpan",
    "GenerationSpan",
    "NullBackend",
    "NullTracer",
    "RouterSpan",
    "Span",
    "TapeSpan",
    "ToolSpan",
    "Tracer",
    "TracerBackend",
    "current_tracer",
    "set_tracer",
]