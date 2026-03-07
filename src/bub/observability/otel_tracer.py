


"""OpenTelemetry tracing backend with Bub-specific span types."""

from __future__ import annotations

from typing import Any

from loguru import logger

from bub.observability.tracer import (
    ChannelSpan,
    GenerationSpan,
    RouterSpan,
    Span,
    TapeSpan,
    ToolSpan,
    TracerBackend,
    _gen_id,
)

# OpenTelemetry semantic conventions for GenAI.
# See: https://opentelemetry.io/docs/specs/semconv/gen-ai/
_ATTR_GEN_AI_SYSTEM = "gen_ai.system"
_ATTR_GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
_ATTR_GEN_AI_USAGE_INPUT = "gen_ai.usage.input_tokens"
_ATTR_GEN_AI_USAGE_OUTPUT = "gen_ai.usage.output_tokens"
_ATTR_GEN_AI_USAGE_TOTAL = "gen_ai.usage.total_tokens"

# Bub-specific attributes
_ATTR_BUB_SPAN_TYPE = "bub.span_type"
_ATTR_BUB_TOOL_NAME = "bub.tool.name"
_ATTR_BUB_TOOL_INPUT = "bub.tool.input"
_ATTR_BUB_ROUTER_COMMAND = "bub.router.command"
_ATTR_BUB_ROUTER_INPUT = "bub.router.input"
_ATTR_BUB_CHANNEL_TYPE = "bub.channel.type"
_ATTR_BUB_CHANNEL_MESSAGE_ID = "bub.channel.message_id"
_ATTR_BUB_CHANNEL_SENDER_ID = "bub.channel.sender_id"
_ATTR_BUB_TAPE_OPERATION = "bub.tape.operation"


class OtelBackend(TracerBackend):
    """OpenTelemetry tracing backend.

    Requires: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp
    Config via env:
      OTEL_SERVICE_NAME (default: bub)
      OTEL_EXPORTER_OTLP_ENDPOINT (default: http://localhost:4317)
    """

    def __init__(
        self,
        *,
        service_name: str = "bub",
        endpoint: str | None = None,
    ) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as exc:
            raise ImportError("opentelemetry is required: pip install 'bub[otel]'") from exc

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        exporter_kwargs: dict[str, Any] = {}
        if endpoint:
            exporter_kwargs["endpoint"] = endpoint
        exporter = OTLPSpanExporter(**exporter_kwargs)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("bub")
        self._provider = provider
        # Map span_id -> otel span.
        self._otel_spans: dict[str, Any] = {}
        self._otel_contexts: dict[str, Any] = {}
        logger.info("observability.otel.init service={}", service_name)

    def start_trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:  # noqa: A002
        from opentelemetry import context, trace

        span_id = _gen_id()
        otel_span = self._tracer.start_span(name)
        if input is not None:
            otel_span.set_attribute("input", str(input)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        otel_span.set_attribute(_ATTR_BUB_SPAN_TYPE, "trace")

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return Span(trace_id=trace_id, span_id=span_id, name=name, _backend=self, _handle=otel_span)

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None  # noqa: A002
    ) -> Span:
        from opentelemetry import context, trace

        span_id = _gen_id()
        parent_otel = self._otel_spans.get(parent.span_id) if parent else None
        if parent_otel:
            ctx = trace.set_span_in_context(parent_otel)
            otel_span = self._tracer.start_span(name, context=ctx)
        else:
            otel_span = self._tracer.start_span(name)

        if input is not None:
            otel_span.set_attribute("input", str(input)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        otel_span.set_attribute(_ATTR_BUB_SPAN_TYPE, "span")

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return Span(trace_id=trace_id, span_id=span_id, name=name, _backend=self, _handle=otel_span)

    def start_generation(
        self,
        name: str,
        *,
        parent: Span | None = None,
        model: str = "",
        input_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationSpan:
        from opentelemetry import context, trace

        span_id = _gen_id()
        parent_otel = self._otel_spans.get(parent.span_id) if parent else None
        if parent_otel:
            ctx = trace.set_span_in_context(parent_otel)
            otel_span = self._tracer.start_span(name, context=ctx)
        else:
            otel_span = self._tracer.start_span(name)

        otel_span.set_attribute(_ATTR_BUB_SPAN_TYPE, "generation")
        otel_span.set_attribute(_ATTR_GEN_AI_SYSTEM, "bub")
        if model:
            otel_span.set_attribute(_ATTR_GEN_AI_REQUEST_MODEL, model)
        if input_messages:
            otel_span.set_attribute("gen_ai.input_messages", str(input_messages)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return GenerationSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=otel_span,
            model=model,
            input_messages=input_messages or [],
        )

    def start_tool(
        self,
        name: str,
        *,
        parent: Span | None = None,
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolSpan:
        from opentelemetry import context, trace

        span_id = _gen_id()
        parent_otel = self._otel_spans.get(parent.span_id) if parent else None
        if parent_otel:
            ctx = trace.set_span_in_context(parent_otel)
            otel_span = self._tracer.start_span(name, context=ctx)
        else:
            otel_span = self._tracer.start_span(name)

        otel_span.set_attribute(_ATTR_BUB_SPAN_TYPE, "tool")
        if tool_name:
            otel_span.set_attribute(_ATTR_BUB_TOOL_NAME, tool_name)
        if tool_input:
            otel_span.set_attribute(_ATTR_BUB_TOOL_INPUT, str(tool_input)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return ToolSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=otel_span,
            tool_name=tool_name,
            tool_input=tool_input or {},
        )

    def start_router(
        self,
        name: str,
        *,
        parent: Span | None = None,
        input_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RouterSpan:
        from opentelemetry import context, trace

        span_id = _gen_id()
        parent_otel = self._otel_spans.get(parent.span_id) if parent else None
        if parent_otel:
            ctx = trace.set_span_in_context(parent_otel)
            otel_span = self._tracer.start_span(name, context=ctx)
        else:
            otel_span = self._tracer.start_span(name)

        otel_span.set_attribute(_ATTR_BUB_SPAN_TYPE, "router")
        if input_text:
            otel_span.set_attribute(_ATTR_BUB_ROUTER_INPUT, input_text[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return RouterSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=otel_span,
            input_text=input_text,
        )

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
        from opentelemetry import context, trace

        span_id = _gen_id()
        parent_otel = self._otel_spans.get(parent.span_id) if parent else None
        if parent_otel:
            ctx = trace.set_span_in_context(parent_otel)
            otel_span = self._tracer.start_span(name, context=ctx)
        else:
            otel_span = self._tracer.start_span(name)

        otel_span.set_attribute(_ATTR_BUB_SPAN_TYPE, "channel")
        if channel_type:
            otel_span.set_attribute(_ATTR_BUB_CHANNEL_TYPE, channel_type)
        if message_id:
            otel_span.set_attribute(_ATTR_BUB_CHANNEL_MESSAGE_ID, message_id)
        if sender_id:
            otel_span.set_attribute(_ATTR_BUB_CHANNEL_SENDER_ID, sender_id)
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return ChannelSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=otel_span,
            channel_type=channel_type,
            message_id=message_id,
            sender_id=sender_id,
        )

    def start_tape(
        self,
        name: str,
        *,
        parent: Span | None = None,
        operation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TapeSpan:
        from opentelemetry import context, trace

        span_id = _gen_id()
        parent_otel = self._otel_spans.get(parent.span_id) if parent else None
        if parent_otel:
            ctx = trace.set_span_in_context(parent_otel)
            otel_span = self._tracer.start_span(name, context=ctx)
        else:
            otel_span = self._tracer.start_span(name)

        otel_span.set_attribute(_ATTR_BUB_SPAN_TYPE, "tape")
        if operation:
            otel_span.set_attribute(_ATTR_BUB_TAPE_OPERATION, operation)
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return TapeSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=otel_span,
            operation=operation,
        )

    def end_span(
        self, span: Span, *, output: Any = None, metadata: dict[str, Any] | None = None, level: str = "DEFAULT"
    ) -> None:
        otel_span = self._otel_spans.pop(span.span_id, None)
        token = self._otel_contexts.pop(span.span_id, None)
        if otel_span is None:
            return
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        if level == "ERROR":
            from opentelemetry.trace import StatusCode

            otel_span.set_status(StatusCode.ERROR, str(output or ""))
        otel_span.end()
        if token:
            from opentelemetry import context

            context.detach(token)

    def end_generation(
        self,
        span: GenerationSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        usage: dict[str, int] | None = None,
    ) -> None:
        otel_span = self._otel_spans.pop(span.span_id, None)
        token = self._otel_contexts.pop(span.span_id, None)
        if otel_span is None:
            return
        self._set_span_attributes(otel_span, output, metadata, level)
        self._set_usage_attributes(otel_span, usage)
        otel_span.end()
        if token:
            from opentelemetry import context

            context.detach(token)

    def _set_span_attributes(
        self,
        otel_span: Any,
        output: Any,
        metadata: dict[str, Any] | None,
        level: str,
    ) -> None:
        """Set common span attributes."""
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        if level == "ERROR":
            from opentelemetry.trace import StatusCode

            otel_span.set_status(StatusCode.ERROR, str(output or ""))

    def _set_usage_attributes(self, otel_span: Any, usage: dict[str, int] | None) -> None:
        """Set usage attributes for generation spans."""
        if usage:
            if "input_tokens" in usage:
                otel_span.set_attribute(_ATTR_GEN_AI_USAGE_INPUT, usage["input_tokens"])
            if "output_tokens" in usage:
                otel_span.set_attribute(_ATTR_GEN_AI_USAGE_OUTPUT, usage["output_tokens"])
            if "total_tokens" in usage:
                otel_span.set_attribute(_ATTR_GEN_AI_USAGE_TOTAL, usage["total_tokens"])

    def end_tool(
        self,
        span: ToolSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        otel_span = self._otel_spans.pop(span.span_id, None)
        token = self._otel_contexts.pop(span.span_id, None)
        if otel_span is None:
            return
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        if level == "ERROR":
            from opentelemetry.trace import StatusCode

            otel_span.set_status(StatusCode.ERROR, str(output or ""))
        otel_span.end()
        if token:
            from opentelemetry import context

            context.detach(token)

    def end_router(
        self,
        span: RouterSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        otel_span = self._otel_spans.pop(span.span_id, None)
        token = self._otel_contexts.pop(span.span_id, None)
        if otel_span is None:
            return
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if span.detected_command:
            otel_span.set_attribute(_ATTR_BUB_ROUTER_COMMAND, span.detected_command)
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        if level == "ERROR":
            from opentelemetry.trace import StatusCode

            otel_span.set_status(StatusCode.ERROR, str(output or ""))
        otel_span.end()
        if token:
            from opentelemetry import context

            context.detach(token)

    def end_channel(
        self,
        span: ChannelSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        otel_span = self._otel_spans.pop(span.span_id, None)
        token = self._otel_contexts.pop(span.span_id, None)
        if otel_span is None:
            return
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        if level == "ERROR":
            from opentelemetry.trace import StatusCode

            otel_span.set_status(StatusCode.ERROR, str(output or ""))
        otel_span.end()
        if token:
            from opentelemetry import context

            context.detach(token)

    def end_tape(
        self,
        span: TapeSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        otel_span = self._otel_spans.pop(span.span_id, None)
        token = self._otel_contexts.pop(span.span_id, None)
        if otel_span is None:
            return
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        if level == "ERROR":
            from opentelemetry.trace import StatusCode

            otel_span.set_status(StatusCode.ERROR, str(output or ""))
        otel_span.end()
        if token:
            from opentelemetry import context

            context.detach(token)

    def update_span(self, span: Span, *, metadata: dict[str, Any] | None = None, output: Any = None) -> None:
        otel_span = self._otel_spans.get(span.span_id)
        if otel_span is None:
            return
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))

    def flush(self) -> None:
        self._provider.force_flush()

    def shutdown(self) -> None:
        self._provider.shutdown()
