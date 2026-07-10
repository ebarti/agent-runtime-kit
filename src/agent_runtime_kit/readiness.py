"""Bounded, explicit runtime-readiness probes."""

from __future__ import annotations

import asyncio
from math import isfinite

from agent_runtime_kit._types import (
    AgentRuntime,
    AvailabilityReason,
    ReadinessStatus,
    RuntimeAvailability,
    RuntimeReadiness,
    runtime_kind_value,
)

DEFAULT_READINESS_TIMEOUT = 5.0


async def check_readiness(
    runtime: AgentRuntime,
    *,
    timeout: float = DEFAULT_READINESS_TIMEOUT,
) -> RuntimeReadiness:
    """Return bounded execution readiness without running an agent task.

    Runtimes may implement the optional :class:`RuntimeReadinessProvider`
    extension. Older third-party runtimes retain compatibility: a negative
    package availability result maps to ``NOT_READY`` while a positive one maps
    conservatively to ``INDETERMINATE`` because package presence alone does not
    prove credentials or provider setup.

    Probe exceptions and timeouts are converted to secret-safe indeterminate
    diagnostics. Task cancellation still propagates normally.
    """

    _validate_timeout(timeout)
    probe = getattr(runtime, "check_readiness", None)
    if not callable(probe):
        return _legacy_readiness(runtime)

    try:
        readiness = await asyncio.wait_for(probe(), timeout=float(timeout))
        if not isinstance(readiness, RuntimeReadiness):
            raise TypeError("readiness provider returned an invalid result")
        if runtime_kind_value(readiness.kind) != runtime_kind_value(runtime.kind):
            raise ValueError("readiness provider returned a different runtime kind")
        return readiness
    except asyncio.TimeoutError:
        return _probe_failure(
            runtime,
            message=f"Readiness probe timed out after {float(timeout):g} seconds.",
            failure="timeout",
        )
    except Exception as exc:
        return _probe_failure(
            runtime,
            message="Readiness probe failed before it could reach a conclusion.",
            failure="error",
            error_type=type(exc).__name__,
        )


def _validate_timeout(timeout: float) -> None:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive finite number")


def _legacy_readiness(runtime: AgentRuntime) -> RuntimeReadiness:
    try:
        availability = runtime.availability()
    except Exception as exc:
        return RuntimeReadiness.indeterminate(
            runtime.kind,
            reason=AvailabilityReason.SETUP_FAILED,
            message="Package availability failed before readiness could be determined.",
            metadata={"failure": "availability", "error_type": type(exc).__name__},
        )
    if not availability.available:
        return RuntimeReadiness.from_availability(
            availability,
            status=ReadinessStatus.NOT_READY,
        )
    return RuntimeReadiness.from_availability(
        availability,
        status=ReadinessStatus.INDETERMINATE,
        message=(
            "Package is available, but this runtime does not implement an "
            "execution-readiness probe."
        ),
        metadata={"probe": "unsupported"},
    )


def _probe_failure(
    runtime: AgentRuntime,
    *,
    message: str,
    failure: str,
    error_type: str | None = None,
) -> RuntimeReadiness:
    availability = _safe_availability(runtime)
    metadata = {"failure": failure}
    if error_type is not None:
        metadata["error_type"] = error_type
    return RuntimeReadiness.indeterminate(
        runtime.kind,
        reason=AvailabilityReason.SETUP_FAILED,
        message=message,
        package=availability.package if availability is not None else None,
        version=availability.version if availability is not None else None,
        metadata=metadata,
    )


def _safe_availability(runtime: AgentRuntime) -> RuntimeAvailability | None:
    try:
        return runtime.availability()
    except Exception:
        return None


__all__ = ["DEFAULT_READINESS_TIMEOUT", "check_readiness"]
