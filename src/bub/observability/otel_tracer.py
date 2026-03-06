"""OpenTelemetry tracing backend."""

from __future__ import annotations

from typing import Any

from loguru import logger

from bub.observability.tracer import GenerationSpan, Span, TracerBackend, _gen_id

# OpenTelemetry semantic conventions for GenAI.
# See: https://opentelemetry.io/docs/specs/semconv/gen-ai/
_ATTR_GEN_AI_SYSTEM = "gen_ai.system"
_ATTR_GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
_ATTR_GEN_AI_USAGE_INPUT = "gen_ai.usage.input_tokens"
_ATTR_GEN_AI_USAGE_OUTPUT = "gen_ai.usage.output_tokens"
_ATTR_GEN_AI_USAGE_TOTAL = "gen_ai.usage.total_tokens"


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

    def start_trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:
        from opentelemetry import context, trace

        span_id = _gen_id()
        otel_span = self._tracer.start_span(name)
        if input is not None:
            otel_span.set_attribute("input", str(input)[:4096])
        if metadata:
            for key, value in metadata.items():
                otel_span.set_attribute(f"bub.{key}", str(value))
        otel_span.set_attribute("bub.span_type", "trace")

        ctx = trace.set_span_in_context(otel_span)
        token = context.attach(ctx)
        self._otel_spans[span_id] = otel_span
        self._otel_contexts[span_id] = token
        trace_id = format(otel_span.get_span_context().trace_id, "032x")
        return Span(trace_id=trace_id, span_id=span_id, name=name, _backend=self, _handle=otel_span)

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None
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
        otel_span.set_attribute("bub.span_type", "span")

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

        otel_span.set_attribute("bub.span_type", "generation")
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
        if output is not None:
            otel_span.set_attribute("output", str(output)[:4096])
        if usage:
            if "input_tokens" in usage:
                otel_span.set_attribute(_ATTR_GEN_AI_USAGE_INPUT, usage["input_tokens"])
            if "output_tokens" in usage:
                otel_span.set_attribute(_ATTR_GEN_AI_USAGE_OUTPUT, usage["output_tokens"])
            if "total_tokens" in usage:
                otel_span.set_attribute(_ATTR_GEN_AI_USAGE_TOTAL, usage["total_tokens"])
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
