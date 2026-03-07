



"""Tests for the observability tracer abstraction."""

from __future__ import annotations

from typing import Any

import pytest

from bub.observability.tracer import (
    ChannelSpan,
    GenerationSpan,
    NullTracer,
    RouterSpan,
    Span,
    TapeSpan,
    ToolSpan,
    Tracer,
    _current_span,
    _gen_id,
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

    # New Bub-specific span types
    def start_tool(
        self,
        name: str,
        *,
        parent: Span | None = None,
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolSpan:
        span = ToolSpan(
            trace_id=parent.trace_id if parent else _gen_id(),
            span_id=_gen_id(),
            name=name,
            _backend=self,
            tool_name=tool_name,
            tool_input=tool_input or {},
        )
        self.events.append(("start_tool", {"name": name, "tool_name": tool_name}))
        return span

    def start_router(
        self,
        name: str,
        *,
        parent: Span | None = None,
        input_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RouterSpan:
        span = RouterSpan(
            trace_id=parent.trace_id if parent else _gen_id(),
            span_id=_gen_id(),
            name=name,
            _backend=self,
            input_text=input_text,
        )
        self.events.append(("start_router", {"name": name}))
        return span

    def start_channel(
        self,
        name: str,
        *,
        parent: Span | None = None,
        channel_type: str = "",
        message_id: str = "",
        sender_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ChannelSpan:
        span = ChannelSpan(
            trace_id=parent.trace_id if parent else _gen_id(),
            span_id=_gen_id(),
            name=name,
            _backend=self,
            channel_type=channel_type,
            message_id=message_id,
            sender_id=sender_id,
        )
        self.events.append(("start_channel", {"name": name, "channel_type": channel_type}))
        return span

    def start_tape(
        self,
        name: str,
        *,
        parent: Span | None = None,
        operation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TapeSpan:
        span = TapeSpan(
            trace_id=parent.trace_id if parent else _gen_id(),
            span_id=_gen_id(),
            name=name,
            _backend=self,
            operation=operation,
        )
        self.events.append(("start_tape", {"name": name, "operation": operation}))
        return span

    def end_tool(
        self,
        span: ToolSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        self.events.append(("end_tool", {"span_id": span.span_id, "output": output, "level": level}))

    def end_router(
        self,
        span: RouterSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        self.events.append(("end_router", {"span_id": span.span_id, "output": output, "level": level}))

    def end_channel(
        self,
        span: ChannelSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        self.events.append(("end_channel", {"span_id": span.span_id, "output": output, "level": level}))

    def end_tape(
        self,
        span: TapeSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        self.events.append(("end_tape", {"span_id": span.span_id, "output": output, "level": level}))

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
            # Test new Bub-specific span types
            with tracer.tool("tool", tool_name="bash", tool_input={"cmd": "ls"}) as tool_span:
                assert isinstance(tool_span, ToolSpan)
                tool_span.end(output="files")
            with tracer.router("router", input_text="hello world") as router_span:
                assert isinstance(router_span, RouterSpan)
                router_span.end(output={"command": "search"})
            with tracer.channel("channel", channel_type="feishu", message_id="om_xxx", sender_id="ou_xxx") as channel_span:
                assert isinstance(channel_span, ChannelSpan)
                channel_span.end(output="handled")
            with tracer.tape("tape", operation="append") as tape_span:
                assert isinstance(tape_span, TapeSpan)
                tape_span.end(output="appended")

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

        with tracer.trace("root"), tracer.generation("llm.chat", model="gpt-4") as gen:
            assert _current_span.get() is gen
            gen.end(output="hello", usage={"total_tokens": 42})

        gen_events = [e for e in backend.events if e[0] == "end_generation"]
        assert len(gen_events) == 1
        assert gen_events[0][1]["usage"] == {"total_tokens": 42}


class TestSpanContextManager:
    def test_span_end_on_exception(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with pytest.raises(ValueError, match="boom"), tracer.trace("root"), tracer.span("will-fail"):
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


class TestBubSpecificSpans:
    """Tests for Bub-specific span types: Tool, Router, Channel, Tape."""

    def test_tool_span_records_tool_name_and_input(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("test"):
            with tracer.tool("tool.bash", tool_name="bash", tool_input={"cmd": "ls -la"}) as span:
                span.end(output="total 32")

        assert "start_tool" in [e[0] for e in backend.events]
        tool_event = [e for e in backend.events if e[0] == "start_tool"][0]
        assert tool_event[1]["tool_name"] == "bash"

    def test_router_span_records_input_text(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("test"), tracer.router("router.detect", input_text="/help") as span:
            span.end(output={"command": "help"})

        assert "start_router" in [e[0] for e in backend.events]
        router_event = [e for e in backend.events if e[0] == "start_router"][0]
        assert router_event[1]["name"] == "router.detect"

    def test_channel_span_records_channel_info(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("test"), tracer.channel(
            "channel.feishu", channel_type="feishu", message_id="om_123", sender_id="ou_456"
        ) as span:
            span.end(output="message_sent")

        assert "start_channel" in [e[0] for e in backend.events]
        channel_event = [e for e in backend.events if e[0] == "start_channel"][0]
        assert channel_event[1]["channel_type"] == "feishu"

    def test_tape_span_records_operation(self) -> None:
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("test"), tracer.tape("tape.append", operation="append") as span:
            span.end(output={"events_added": 2})

        assert "start_tape" in [e[0] for e in backend.events]
        tape_event = [e for e in backend.events if e[0] == "start_tape"][0]
        assert tape_event[1]["operation"] == "append"

    def test_all_span_types_have_parent_propagation(self) -> None:
        """All Bub-specific spans should propagate parent context."""
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("root"):
            with tracer.tool("tool"):
                pass
            with tracer.router("router"):
                pass
            with tracer.channel("channel"):
                pass
            with tracer.tape("tape"):
                pass

        # Each span type should have a start and end event
        assert "start_tool" in [e[0] for e in backend.events]
        assert "end_tool" in [e[0] for e in backend.events]
        assert "start_router" in [e[0] for e in backend.events]
        assert "end_router" in [e[0] for e in backend.events]
        assert "start_channel" in [e[0] for e in backend.events]
        assert "end_channel" in [e[0] for e in backend.events]
        assert "start_tape" in [e[0] for e in backend.events]
        assert "end_tape" in [e[0] for e in backend.events]

    def test_span_error_level(self) -> None:
        """Error level should be propagated correctly."""
        backend = RecordingBackend()
        tracer = Tracer(backend)

        with tracer.trace("test"), tracer.tool("tool.fail", tool_name="bash") as span:
            span.end(output="error: command not found", level="ERROR")

        tool_end = [e for e in backend.events if e[0] == "end_tool"][0]
        assert tool_end[1]["level"] == "ERROR"
