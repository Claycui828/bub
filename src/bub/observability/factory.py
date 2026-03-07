

"""Build tracer from settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from bub.observability.tracer import NullTracer, Tracer, TracerBackend

if TYPE_CHECKING:
    from bub.config.settings import Settings


def build_tracer(settings: Settings) -> Tracer:
    """Build tracer based on settings."""
    if not settings.trace_enabled:
        return NullTracer()

    backend_name = settings.trace_backend.lower()

    if backend_name == "langfuse":
        from bub.observability.langfuse_tracer import LangfuseBackend

        backend: TracerBackend = LangfuseBackend(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        return Tracer(backend)

    if backend_name == "otel":
        from bub.observability.otel_tracer import OtelBackend

        backend = OtelBackend(
            service_name=settings.otel_service_name,
            endpoint=settings.otel_endpoint,
        )
        return Tracer(backend)

    logger.warning("observability.unknown_backend backend={}, falling back to null", backend_name)
    return NullTracer()
