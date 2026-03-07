

"""Langfuse tracing backend (v3 API) with Bub-specific span types."""

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

    def start_trace(self, name: str, *, input: Any = None, metadata: dict[str, Any] | None = None) -> Span:  # noqa: A002
        span_id = _gen_id()
        # In Langfuse v3, a top-level span implicitly creates a trace.
        handle = self._client.start_span(name=name, input=input, metadata=metadata or {})
        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return Span(trace_id=trace_id, span_id=span_id, name=name, _backend=self, _handle=handle)

    def start_span(
        self, name: str, *, parent: Span | None = None, input: Any = None, metadata: dict[str, Any] | None = None  # noqa: A002
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

    def start_tool(
        self,
        name: str,
        *,
        parent: Span | None = None,
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolSpan:
        span_id = _gen_id()
        parent_handle = self._handles.get(parent.span_id) if parent else None

        span_metadata = metadata or {}
        if tool_name:
            span_metadata["bub.tool.name"] = tool_name
        if tool_input:
            span_metadata["bub.tool.input"] = str(tool_input)[:4096]

        if parent_handle is not None:
            handle = parent_handle.start_span(name=name, input=tool_input, metadata=span_metadata)
        else:
            handle = self._client.start_span(name=name, input=tool_input, metadata=span_metadata)

        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return ToolSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=handle,
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
        span_id = _gen_id()
        parent_handle = self._handles.get(parent.span_id) if parent else None

        span_metadata = metadata or {}
        if input_text:
            span_metadata["bub.router.input"] = input_text[:4096]

        if parent_handle is not None:
            handle = parent_handle.start_span(name=name, input=input_text, metadata=span_metadata)
        else:
            handle = self._client.start_span(name=name, input=input_text, metadata=span_metadata)

        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return RouterSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=handle,
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
        span_id = _gen_id()
        parent_handle = self._handles.get(parent.span_id) if parent else None

        span_metadata = metadata or {}
        if channel_type:
            span_metadata["bub.channel.type"] = channel_type
        if message_id:
            span_metadata["bub.channel.message_id"] = message_id
        if sender_id:
            span_metadata["bub.channel.sender_id"] = sender_id

        if parent_handle is not None:
            handle = parent_handle.start_span(name=name, metadata=span_metadata)
        else:
            handle = self._client.start_span(name=name, metadata=span_metadata)

        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return ChannelSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=handle,
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
        span_id = _gen_id()
        parent_handle = self._handles.get(parent.span_id) if parent else None

        span_metadata = metadata or {}
        if operation:
            span_metadata["bub.tape.operation"] = operation

        if parent_handle is not None:
            handle = parent_handle.start_span(name=name, metadata=span_metadata)
        else:
            handle = self._client.start_span(name=name, metadata=span_metadata)

        trace_id = handle.trace_id
        self._handles[span_id] = handle
        return TapeSpan(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            _backend=self,
            _handle=handle,
            operation=operation,
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

    def end_tool(
        self,
        span: ToolSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
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
        if update_kwargs:
            handle.update(**update_kwargs)
        handle.end()

    def end_router(
        self,
        span: RouterSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        handle = self._handles.pop(span.span_id, None)
        if handle is None:
            return
        update_kwargs: dict[str, Any] = {}
        if output is not None:
            update_kwargs["output"] = output
        if span.detected_command:
            update_kwargs["metadata"] = {"bub.router.command": span.detected_command}
        if metadata:
            if "metadata" in update_kwargs:
                update_kwargs["metadata"].update(metadata)
            else:
                update_kwargs["metadata"] = metadata
        if level != "DEFAULT":
            update_kwargs["level"] = level
        if level == "ERROR" and output is not None:
            update_kwargs["status_message"] = str(output)[:1024]
        if update_kwargs:
            handle.update(**update_kwargs)
        handle.end()

    def end_channel(
        self,
        span: ChannelSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
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
        if update_kwargs:
            handle.update(**update_kwargs)
        handle.end()

    def end_tape(
        self,
        span: TapeSpan,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
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
