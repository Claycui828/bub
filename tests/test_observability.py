"""Tests for the observability tracer abstraction."""

from __future__ import annotations

from typing import Any

import pytest

from bub.observability.tracer import (
    GenerationSpan,
    NullBackend,
    NullTracer,
    Span,
    Tracer,
    TracerBackend,
    _current_span,
    _gen_id,
    _push_span,
    _span_parents,
    current_tracer,
    set_tracer,
)


@pytest.fixture(autouse=True)
def _reset_context() -> None:
    """Reset context vars between tests."""
    _current_span.set(None)
    _span_parents.set({})


class RecordingBackend:
    """Backend that records all calls for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def start_trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:
        span = Span(trace_id=_gen_id(), span_id=_gen_id(), name=name, _backend=self)
        self.events.append(("start_trace", {"name": name, "input": input, "span_id": span.span_id}))
        return span

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> Span:
        span = Span(
            trace_id=parent.trace_id if parent else _gen_id(),
            span_id=_gen_id(),
            name=name,
            _backend=self,
        )
        self.events.append(("start_span", {"name": name, "parent": parent.span_id if parent else None}))
        return span

    def start_generation(
        self,
        name: str,
        *,
        parent: Span | None = None,
        model: str = "",
        input_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationSpan:
        span = GenerationSpan(
            trace_id=parent.trace_id if parent else _gen_id(),
            span_id=_gen_id(),
            name=name,
            _backend=self,
            model=model,
            input_messages=input_messages or [],
        )
        self.events.append(("start_generation", {"name": name, "model": model}))
        return span

    def end_span(self, span: Span, *, output: Any = None, metadata: dict[str, Any] | None = None, level: str = "DEFAULT") -> None:
        self.events.append(("end_span", {"span_id": span.span_id, "output": output, "level": level}))

    def end_generation(
        self,
        span: GenerationSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        usage: dict[str, int] | None = None,
    ) -> None:
        self.events.append(("end_generation", {"span_id": span.span_id, "output": output, "usage": usage}))

    def update_span(self, span: Span, *, metadata: dict[str, Any] | None = None, output: Any = None) -> None:
        self.events.append(("update_span", {"span_id": span.span_id}))

    def flush(self) -> None:
        self.events.append(("flush", {}))

    def shutdown(self) -> None:
        self.events.append(("shutdown", {}))


class TestNullTracer:
    def test_null_tracer_is_noop(self) -> None:
        tracer = NullTracer()
        assert not tracer.enabled

        with tracer.trace("test", input="hello") as span:
            assert isinstance(span, Span)
            with tracer.span("child") as child:
                assert isinstance(child, Span)
            with tracer.generation("gen", model="test-model") as gen:
                assert isinstance(gen, GenerationSpan)
                gen.end(usage={"total_tokens": 100})

        tracer.flush()
        tracer.shutdown()


class TestTracerContextPropagation:
    def test_trace_sets_current_span(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("root") as root:
            assert _current_span.get() is root
        assert _current_span.get() is None

    def test_nested_spans_propagate_parent(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("root") as root:
            with tracer.span("child") as child:
                assert _current_span.get() is child
                # The start_span should have received the root as parent.
                start_span_event = [e for e in backend.events if e[0] == "start_span"][0]
                assert start_span_event[1]["parent"] == root.span_id
            # After child exits, current should be root again.
            assert _current_span.get() is root
        assert _current_span.get() is None

    def test_generation_span_nested(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("root"):
            with tracer.generation("llm.chat", model="gpt-4") as gen:
                assert _current_span.get() is gen
                gen.end(output="hello", usage={"total_tokens": 42})

        gen_events = [e for e in backend.events if e[0] == "end_generation"]
        assert len(gen_events) == 1
        assert gen_events[0][1]["usage"] == {"total_tokens": 42}


class TestSpanContextManager:
    def test_span_end_on_exception(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with pytest.raises(ValueError, match="boom"):
            with tracer.trace("root"):
                with tracer.span("will-fail"):
                    raise ValueError("boom")

        end_events = [e for e in backend.events if e[0] == "end_span"]
        # Both spans should be ended: child (error) + root (error).
        assert len(end_events) == 2
        assert end_events[0][1]["level"] == "ERROR"
        assert "boom" in end_events[0][1]["output"]

    def test_double_end_is_safe(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("root") as span:
            span.end(output="done")
            span.end(output="again")  # Should be ignored.

        end_events = [e for e in backend.events if e[0] == "end_span"]
        assert len(end_events) == 1


class TestTracerEnabled:
    def test_null_not_enabled(self) -> None:
        assert not NullTracer().enabled

    def test_recording_is_enabled(self) -> None:
        assert Tracer(RecordingBackend()).enabled


class TestGlobalTracer:
    def test_set_and_get_tracer(self) -> None:
        original = current_tracer()
        backend = RecordingBackend()
        new_tracer = Tracer(backend)
        set_tracer(new_tracer)
        assert current_tracer() is new_tracer
        # Restore.
        set_tracer(original)


class TestFlushAndShutdown:
    def test_flush_and_shutdown(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)
        tracer.flush()
        tracer.shutdown()
        event_types = [e[0] for e in backend.events]
        assert "flush" in event_types
        assert "shutdown" in event_types


class TestFullTraceScenario:
    def test_agent_like_trace(self) -> None:
        """Simulate a full agent trace: input -> loop step -> llm call -> tool call."""
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("session.handle_input", input="hello", metadata={"session": "s1"}):
            with tracer.span("loop.step.1", metadata={"step": 1}):
                with tracer.generation("llm.chat", model="qwen/coder", metadata={"max_tokens": 1024}) as gen:
                    gen.end(output="I'll search for that", usage={"input_tokens": 100, "output_tokens": 20})
                with tracer.span("tool.bash", input={"cmd": "ls"}) as tool_span:
                    tool_span.end(output="file1.py\nfile2.py")

        event_types = [e[0] for e in backend.events]
        assert event_types == [
            "start_trace",
            "start_span",       # loop.step.1
            "start_generation", # llm.chat
            "end_generation",
            "start_span",       # tool.bash
            "end_span",         # tool.bash
            "end_span",         # loop.step.1
            "end_span",         # trace root
        ]
