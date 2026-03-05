"""Langfuse tracing backend."""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from bub.observability.tracer import GenerationSpan, Span, TracerBackend, _gen_id


class LangfuseBackend(TracerBackend):
    """Langfuse tracing backend for LLM-specific observability.

    Requires: pip install langfuse
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
        # Map span_id -> langfuse object (trace/span/generation).
        self._handles: dict[str, Any] = {}
        self._trace_ids: dict[str, str] = {}  # span_id -> langfuse trace_id
        logger.info("observability.langfuse.init host={}", self._client.base_url)

    def start_trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:
        trace_id = _gen_id()
        span_id = trace_id  # Root span shares trace_id.
        trace = self._client.trace(
            id=trace_id,
            name=name,
            input=input,
            metadata=metadata or {},
        )
        self._handles[span_id] = trace
        self._trace_ids[span_id] = trace_id
        return Span(trace_id=trace_id, span_id=span_id, name=name, _backend=self, _handle=trace)

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None
    ) -> Span:
        span_id = _gen_id()
        parent_handle = self._handles.get(parent.span_id) if parent else None
        trace_id = (parent.trace_id if parent else None) or _gen_id()

        if parent_handle is not None:
            handle = parent_handle.span(
                id=span_id,
                name=name,
                input=input,
                metadata=metadata or {},
            )
        else:
            handle = self._client.trace(id=trace_id, name="orphan").span(
                id=span_id, name=name, input=input, metadata=metadata or {}
            )

        self._handles[span_id] = handle
        self._trace_ids[span_id] = trace_id
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
        trace_id = (parent.trace_id if parent else None) or _gen_id()

        gen_kwargs: dict[str, Any] = {
            "id": span_id,
            "name": name,
            "model": model,
            "metadata": metadata or {},
        }
        if input_messages:
            gen_kwargs["input"] = input_messages

        if parent_handle is not None:
            handle = parent_handle.generation(**gen_kwargs)
        else:
            handle = self._client.trace(id=trace_id, name="orphan").generation(**gen_kwargs)

        self._handles[span_id] = handle
        self._trace_ids[span_id] = trace_id
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
        update_kwargs: dict[str, Any] = {"end_time": _now()}
        if output is not None:
            update_kwargs["output"] = output
        if metadata:
            update_kwargs["metadata"] = metadata
        if level != "DEFAULT":
            update_kwargs["level"] = level
        handle.end(**update_kwargs) if hasattr(handle, "end") else handle.update(**update_kwargs)

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
        update_kwargs: dict[str, Any] = {"end_time": _now()}
        if output is not None:
            update_kwargs["output"] = output
        if metadata:
            update_kwargs["metadata"] = metadata
        if level != "DEFAULT":
            update_kwargs["level"] = level
        if usage:
            update_kwargs["usage"] = usage
        handle.end(**update_kwargs) if hasattr(handle, "end") else handle.update(**update_kwargs)

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


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
