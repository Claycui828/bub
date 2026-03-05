"""Abstract tracer interface and context propagation."""

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
        _current_span.set(_span_parents.get({}).pop(self.span_id, None))

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

    def end(  # type: ignore[override]
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
        _current_span.set(_span_parents.get({}).pop(self.span_id, None))

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if not self._ended:
            error_output = f"error: {exc_val!s}" if exc_val else None
            level = "ERROR" if exc_val else "DEFAULT"
            self.end(output=error_output, level=level)


@runtime_checkable
class TracerBackend(Protocol):
    """Backend protocol for tracing implementations."""

    def start_trace(
        self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> Span: ...

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None
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

    def update_span(self, span: Span, *, metadata: dict[str, Any] | None = None, output: Any = None) -> None: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


# -- Context propagation --

_current_span: ContextVar[Span | None] = ContextVar("current_span", default=None)
_span_parents: ContextVar[dict[str, Span | None]] = ContextVar("span_parents", default={})


def _push_span(span: Span) -> None:
    """Push span onto context stack, tracking its parent."""
    parent = _current_span.get(None)
    parents = _span_parents.get({})
    parents[span.span_id] = parent
    _span_parents.set(parents)
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

    def end_span(self, span: Span, **kwargs: Any) -> None:
        pass

    def end_generation(self, span: GenerationSpan, **kwargs: Any) -> None:
        pass

    def update_span(self, span: Span, **kwargs: Any) -> None:
        pass

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class Tracer:
    """High-level tracer with automatic context propagation."""

    def __init__(self, backend: TracerBackend | None = None) -> None:
        self._backend: TracerBackend = backend or NullBackend()

    @property
    def enabled(self) -> bool:
        return not isinstance(self._backend, NullBackend)

    def trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:
        """Start a top-level trace (root span)."""
        span = self._backend.start_trace(name, input=input, metadata=metadata)
        _push_span(span)
        return span

    def span(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:
        """Start a child span under the current span."""
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
