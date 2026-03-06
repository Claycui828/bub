"""Langfuse tracing backend (v3 API)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from bub.observability.tracer import GenerationSpan, Span, TracerBackend, _gen_id


class LangfuseBackend(TracerBackend):
    """Langfuse tracing backend for LLM-specific observability.

    Requires: pip install langfuse>=3
    Config via env or explicit args:
      LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
    """

    def __init__(
        self,
        *,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
    ) -> None:
        try:
            from langfuse import Langfuse
        except ImportError as exc:
            raise ImportError("langfuse is required: pip install 'bub[langfuse]'") from exc

        kwargs: dict[str, Any] = {}
        if public_key:
            kwargs["public_key"] = public_key
        if secret_key:
            kwargs["secret_key"] = secret_key
        if host:
            kwargs["host"] = host

        self._client = Langfuse(**kwargs)
        # Map span_id -> langfuse handle (LangfuseSpan / LangfuseGeneration).
        self._handles: dict[str, Any] = {}
        logger.info("observability.langfuse.init host={}", host or "default")

    def start_trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:
        span_id = _gen_id()
        # In Langfuse v3, a top-level span implicitly creates a trace.
        handle = self._client.start_span(name=name, input=input, metadata=metadata or {})
        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return Span(trace_id=trace_id, span_id=span_id, name=name, _backend=self, _handle=handle)

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> Span:
        span_id = _gen_id()
        parent_handle = self._handles.get(parent.span_id) if parent else None

        if parent_handle is not None:
            handle = parent_handle.start_span(name=name, input=input, metadata=metadata or {})
        else:
            handle = self._client.start_span(name=name, input=input, metadata=metadata or {})

        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return Span(trace_id=trace_id, span_id=span_id, name=name, _backend=self, _handle=handle)

    def start_generation(
        self,
        name: str,
        *,
        parent: Span | None = None,
        model: str = "",
        input_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationSpan:
        span_id = _gen_id()
        parent_handle = self._handles.get(parent.span_id) if parent else None

        gen_kwargs: dict[str, Any] = {
            "name": name,
            "metadata": metadata or {},
        }
        if model:
            gen_kwargs["model"] = model
        if input_messages:
            gen_kwargs["input"] = input_messages

        if parent_handle is not None:
            handle = parent_handle.start_generation(**gen_kwargs)
        else:
            handle = self._client.start_generation(**gen_kwargs)

        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return GenerationSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=handle,
            model=model,
            input_messages=input_messages or [],
        )

    def end_span(
        self, span: Span, *, output: Any = None, metadata: dict[str, Any] | None = None, level: str = "DEFAULT"
    ) -> None:
        handle = self._handles.pop(span.span_id, None)
        if handle is None:
            return
        # Langfuse v3: update() sets fields, end() just marks completion.
        update_kwargs: dict[str, Any] = {}
        if output is not None:
            update_kwargs["output"] = output
        if metadata:
            update_kwargs["metadata"] = metadata
        if level != "DEFAULT":
            update_kwargs["level"] = level
        if level == "ERROR" and output is not None:
            update_kwargs["status_message"] = str(output)[:1024]
        if update_kwargs:
            handle.update(**update_kwargs)
        handle.end()

    def end_generation(
        self,
        span: GenerationSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        usage: dict[str, int] | None = None,
    ) -> None:
        handle = self._handles.pop(span.span_id, None)
        if handle is None:
            return
        update_kwargs: dict[str, Any] = {}
        if output is not None:
            update_kwargs["output"] = output
        if metadata:
            update_kwargs["metadata"] = metadata
        if level != "DEFAULT":
            update_kwargs["level"] = level
        if level == "ERROR" and output is not None:
            update_kwargs["status_message"] = str(output)[:1024]
        if usage:
            update_kwargs["usage_details"] = usage
        if update_kwargs:
            handle.update(**update_kwargs)
        handle.end()

    def update_span(self, span: Span, *, metadata: dict[str, Any] | None = None, output: Any = None) -> None:
        handle = self._handles.get(span.span_id)
        if handle is None:
            return
        update_kwargs: dict[str, Any] = {}
        if metadata:
            update_kwargs["metadata"] = metadata
        if output is not None:
            update_kwargs["output"] = output
        if update_kwargs:
            handle.update(**update_kwargs)

    def flush(self) -> None:
        self._client.flush()

    def shutdown(self) -> None:
        self._client.flush()
        self._client.shutdown()
