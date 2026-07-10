from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

import pytest

from agent_runtime_kit import (
    AgentCapabilities,
    AgentKit,
    AgentResult,
    AgentRuntime,
    AgentRuntimeKind,
    AgentTask,
    AvailabilityReason,
    FakeAgentRuntime,
    ReadinessStatus,
    RuntimeAvailability,
    RuntimeReadiness,
    RuntimeReadinessProvider,
    RuntimeRegistry,
    check_readiness,
)
from agent_runtime_kit.adapters import (
    AntigravityAgentRuntime,
    ClaudeAgentRuntime,
    CodexAgentRuntime,
)
from agent_runtime_kit.adapters import antigravity as antigravity_adapter
from agent_runtime_kit.adapters import claude as claude_adapter
from agent_runtime_kit.adapters import codex as codex_adapter
from agent_runtime_kit.adapters import diagnostics as provider_diagnostics
from agent_runtime_kit.adapters.antigravity import _GoogleADCProbe


class LegacyRuntime:
    """Protocol-complete third-party runtime predating readiness probes."""

    kind = "x-third-party"
    capabilities = AgentCapabilities()

    def __init__(self, availability: RuntimeAvailability) -> None:
        self._availability = availability
        self.closed = False

    def availability(self) -> RuntimeAvailability:
        return self._availability

    async def run(self, task: AgentTask) -> AgentResult:
        return AgentResult(output=task.goal)

    async def cancel(self, task_id: str) -> None:
        del task_id

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> LegacyRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()


class FailingReadinessRuntime(LegacyRuntime):
    async def check_readiness(self) -> RuntimeReadiness:
        raise RuntimeError("secret-token-value")


class SlowReadinessRuntime(LegacyRuntime):
    async def check_readiness(self) -> RuntimeReadiness:
        await asyncio.sleep(60)
        return RuntimeReadiness.ready_to_attempt(self.kind)


class InvalidReadinessRuntime(LegacyRuntime):
    async def check_readiness(self) -> Any:
        return {"status": "ready-to-attempt"}


class MismatchedReadinessRuntime(LegacyRuntime):
    async def check_readiness(self) -> RuntimeReadiness:
        return RuntimeReadiness.ready_to_attempt("x-different-runtime")


class BrokenAvailabilityRuntime(LegacyRuntime):
    def availability(self) -> RuntimeAvailability:
        raise RuntimeError("secret-availability-detail")


class BrokenProbeAndAvailabilityRuntime(FailingReadinessRuntime):
    def availability(self) -> RuntimeAvailability:
        raise RuntimeError("secret-availability-detail")


class CleanupFailingRuntime(LegacyRuntime):
    async def aclose(self) -> None:
        raise RuntimeError("secret-cleanup-detail")


def _available_third_party() -> RuntimeAvailability:
    return RuntimeAvailability.ok(
        "x-third-party",
        package="third-party-sdk",
        version="1.2.3",
        metadata={"source": "package"},
    )


def test_readiness_value_coerces_enums_and_freezes_metadata() -> None:
    readiness = RuntimeReadiness(
        kind="fake",
        status=ReadinessStatus.READY_TO_ATTEMPT,
        reason=AvailabilityReason.AVAILABLE,
        message="ready",
        metadata={"source": "test"},
    )

    assert readiness.kind is AgentRuntimeKind.FAKE
    assert readiness.is_ready_to_attempt is True
    with pytest.raises(TypeError, match="read-only"):
        readiness.metadata["source"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="message"):
        RuntimeReadiness.ready_to_attempt("fake", message=" ")
    with pytest.raises(ValueError, match="unavailable package"):
        RuntimeReadiness.from_availability(
            RuntimeAvailability.unavailable(
                "fake",
                reason=AvailabilityReason.MISSING_PACKAGE,
                message="missing",
            ),
            status=ReadinessStatus.READY_TO_ATTEMPT,
        )


@pytest.mark.asyncio
async def test_fake_runtime_is_ready_to_attempt() -> None:
    readiness = await check_readiness(FakeAgentRuntime())

    assert readiness.status is ReadinessStatus.READY_TO_ATTEMPT
    assert readiness.is_ready_to_attempt is True
    assert readiness.package == "agent-runtime-kit"


@pytest.mark.asyncio
async def test_legacy_runtime_with_package_is_conservatively_indeterminate() -> None:
    runtime = LegacyRuntime(_available_third_party())

    readiness = await check_readiness(runtime)

    assert isinstance(runtime, AgentRuntime)
    assert not isinstance(runtime, RuntimeReadinessProvider)
    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.reason is AvailabilityReason.AVAILABLE
    assert readiness.package == "third-party-sdk"
    assert readiness.version == "1.2.3"
    assert readiness.metadata == {"probe": "unsupported"}


@pytest.mark.asyncio
async def test_legacy_runtime_preserves_negative_availability() -> None:
    availability = RuntimeAvailability(
        kind="x-third-party",
        available=False,
        reason=AvailabilityReason.MISSING_PACKAGE,
        message="install x-third-party",
        package="third-party-sdk",
        version="1.0.0",
        metadata={"extra": "third-party"},
    )

    readiness = await check_readiness(LegacyRuntime(availability))

    assert readiness.status is ReadinessStatus.NOT_READY
    assert readiness.reason is AvailabilityReason.MISSING_PACKAGE
    assert readiness.message == "install x-third-party"
    assert readiness.package == "third-party-sdk"
    assert readiness.version == "1.0.0"
    assert readiness.metadata == {}


@pytest.mark.asyncio
async def test_readiness_failure_is_indeterminate_and_secret_safe() -> None:
    readiness = await check_readiness(FailingReadinessRuntime(_available_third_party()))

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.reason is AvailabilityReason.SETUP_FAILED
    assert readiness.metadata["failure"] == "error"
    assert readiness.metadata["error_type"] == "RuntimeError"
    assert "secret-token-value" not in repr(readiness)


@pytest.mark.asyncio
async def test_invalid_readiness_result_is_indeterminate() -> None:
    readiness = await check_readiness(InvalidReadinessRuntime(_available_third_party()))

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["error_type"] == "TypeError"


@pytest.mark.asyncio
async def test_mismatched_readiness_kind_is_indeterminate() -> None:
    readiness = await check_readiness(
        MismatchedReadinessRuntime(_available_third_party())
    )

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_legacy_availability_failure_is_secret_safe() -> None:
    readiness = await check_readiness(BrokenAvailabilityRuntime(_available_third_party()))

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata == {
        "failure": "availability",
        "error_type": "RuntimeError",
    }
    assert "secret-availability-detail" not in repr(readiness)


@pytest.mark.asyncio
async def test_probe_and_availability_failure_stays_secret_safe() -> None:
    readiness = await check_readiness(
        BrokenProbeAndAvailabilityRuntime(_available_third_party())
    )

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.package is None
    assert readiness.metadata["error_type"] == "RuntimeError"
    assert "secret" not in repr(readiness)


@pytest.mark.asyncio
async def test_readiness_timeout_is_indeterminate() -> None:
    readiness = await check_readiness(
        SlowReadinessRuntime(_available_third_party()), timeout=0.01
    )

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["failure"] == "timeout"
    assert "0.01 seconds" in readiness.message


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
async def test_readiness_rejects_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        await check_readiness(FakeAgentRuntime(), timeout=timeout)


@pytest.mark.asyncio
async def test_registry_readiness_closes_ephemeral_runtime() -> None:
    registry = RuntimeRegistry()
    runtimes: list[LegacyRuntime] = []

    def factory() -> LegacyRuntime:
        runtime = LegacyRuntime(_available_third_party())
        runtimes.append(runtime)
        return runtime

    registry.register("x-third-party", factory)

    readiness = await registry.readiness_for("x-third-party")

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert runtimes[0].closed is True


@pytest.mark.asyncio
async def test_registry_cleanup_failure_is_secret_safe_and_indeterminate() -> None:
    registry = RuntimeRegistry()
    registry.register(
        "x-third-party",
        lambda: CleanupFailingRuntime(_available_third_party()),
    )

    readiness = await registry.readiness_for("x-third-party")

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata == {"failure": "cleanup", "error_type": "RuntimeError"}
    assert "secret-cleanup-detail" not in repr(readiness)


@pytest.mark.asyncio
async def test_agent_kit_readiness_helpers_use_owned_cached_runtimes() -> None:
    kit = AgentKit(register_default_adapters=False)

    one = await kit.readiness_for("fake")
    all_results = await kit.readiness()

    assert one.status is ReadinessStatus.READY_TO_ATTEMPT
    assert all_results == (one,)
    await kit.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("env", "source"),
    [
        ({"ANTHROPIC_API_KEY": "secret-anthropic-key"}, "anthropic-api-key"),
        ({"CLAUDE_CODE_OAUTH_TOKEN": "secret-oauth-token"}, "claude-code-oauth-token"),
        (
            {
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "AWS_BEARER_TOKEN_BEDROCK": "secret-bedrock-token",
            },
            "amazon-bedrock",
        ),
    ],
)
async def test_claude_direct_credential_signal_is_ready_without_exposing_it(
    env: dict[str, str], source: str
) -> None:
    runtime = ClaudeAgentRuntime(
        env=env,
        query_func=object(),
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.READY_TO_ATTEMPT
    assert readiness.metadata["auth_source"] == source
    assert not any(secret in repr(readiness) for secret in env.values())


@pytest.mark.asyncio
async def test_claude_provider_chain_is_indeterminate() -> None:
    runtime = ClaudeAgentRuntime(
        env={
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_PROFILE": "agent-runtime-kit",
            "AWS_REGION": "us-east-1",
        },
        query_func=object(),
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["auth_source"] == "amazon-bedrock"
    assert readiness.metadata["credential_chain"] == "aws-sdk"
    assert readiness.metadata["aws_profile_configured"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "factory", "package"),
    [
        (claude_adapter, ClaudeAgentRuntime, "claude-agent-sdk"),
        (codex_adapter, CodexAgentRuntime, "openai-codex"),
        (antigravity_adapter, AntigravityAgentRuntime, "google-antigravity"),
    ],
)
async def test_provider_missing_package_is_not_ready_without_probe(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    factory: Any,
    package: str,
) -> None:
    def missing(kind: AgentRuntimeKind) -> RuntimeAvailability:
        return RuntimeAvailability.unavailable(
            kind,
            reason=AvailabilityReason.MISSING_PACKAGE,
            message=f"install {package}",
            package=package,
        )

    monkeypatch.setattr(module, "package_availability", missing)

    readiness = await factory().check_readiness()

    assert readiness.status is ReadinessStatus.NOT_READY
    assert readiness.reason is AvailabilityReason.MISSING_PACKAGE
    assert readiness.package == package


@dataclass
class ProbeCodexConfig:
    config_overrides: tuple[str, ...] = ()
    env: dict[str, str] | None = None


def _codex_probe_runtime(
    *,
    response: Any = None,
    error: Exception | None = None,
    close_error: Exception | None = None,
    env: dict[str, str] | None = None,
) -> tuple[CodexAgentRuntime, list[Any]]:
    instances: list[Any] = []

    class ProbeCodex:
        def __init__(self, config: ProbeCodexConfig) -> None:
            self.config = config
            self.closed = False
            self.refresh_token: bool | None = None
            instances.append(self)

        async def __aenter__(self) -> ProbeCodex:
            return self

        async def __aexit__(self, *_args: object) -> None:
            self.closed = True
            if close_error is not None:
                raise close_error

        async def account(self, *, refresh_token: bool = True) -> Any:
            self.refresh_token = refresh_token
            if error is not None:
                raise error
            return response

    return (
        CodexAgentRuntime(
            env=env,
            codex_cls=ProbeCodex,
            config_cls=ProbeCodexConfig,
        ),
        instances,
    )


@pytest.mark.asyncio
async def test_codex_account_probe_is_ready_and_always_cleans_up() -> None:
    runtime, instances = _codex_probe_runtime(
        response={
            "account": {
                "root": {
                    "type": "chatgpt",
                    "email": "private@example.invalid",
                }
            },
            "requires_openai_auth": True,
        },
        env={"OPENAI_API_KEY": "secret-openai-key"},
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.READY_TO_ATTEMPT
    assert readiness.metadata["auth_source"] == "openai-api-key"
    assert readiness.metadata["account_type"] == "chatgpt"
    assert "private@example.invalid" not in repr(readiness)
    assert "secret-openai-key" not in repr(readiness)
    assert instances[0].refresh_token is False
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_codex_absent_account_is_not_ready_and_cleans_up() -> None:
    runtime, instances = _codex_probe_runtime(
        response={"account": None, "requires_openai_auth": True}
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.NOT_READY
    assert readiness.reason is AvailabilityReason.MISSING_CREDENTIALS
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_codex_probe_failure_is_indeterminate_and_cleans_up() -> None:
    runtime, instances = _codex_probe_runtime(
        error=RuntimeError("secret-codex-token")
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["error_type"] == "RuntimeError"
    assert "secret-codex-token" not in repr(readiness)
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_codex_cleanup_failure_makes_probe_indeterminate() -> None:
    runtime, instances = _codex_probe_runtime(
        response={"account": {"root": {"type": "apiKey"}}},
        close_error=RuntimeError("secret-cleanup-detail"),
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["error_type"] == "RuntimeError"
    assert "secret-cleanup-detail" not in repr(readiness)
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_antigravity_api_key_is_ready_without_adc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity._probe_google_adc",
        lambda: (_ for _ in ()).throw(AssertionError("API key must bypass ADC")),
    )
    runtime = AntigravityAgentRuntime(api_key="secret-google-key", agent_cls=object())

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.READY_TO_ATTEMPT
    assert readiness.metadata == {"auth_source": "api-key"}
    assert "secret-google-key" not in repr(readiness)


@pytest.mark.asyncio
async def test_antigravity_missing_credentials_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    runtime = AntigravityAgentRuntime(
        api_key=None,
        vertex=False,
        agent_cls=object(),
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.NOT_READY
    assert readiness.reason is AvailabilityReason.MISSING_CREDENTIALS


@pytest.mark.asyncio
async def test_antigravity_adc_probe_runs_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    release_probe = threading.Event()
    probe_started = threading.Event()
    heartbeat_ran = asyncio.Event()

    def probe() -> _GoogleADCProbe:
        probe_started.set()
        assert release_probe.wait(timeout=1)
        return _GoogleADCProbe(credentials_available=True, project="private-project-id")

    async def heartbeat() -> None:
        while not probe_started.is_set():
            await asyncio.sleep(0)
        heartbeat_ran.set()
        release_probe.set()

    monkeypatch.setattr("agent_runtime_kit.adapters.antigravity._probe_google_adc", probe)
    runtime = AntigravityAgentRuntime(
        api_key=None,
        vertex=True,
        agent_cls=object(),
    )

    readiness, _ = await asyncio.gather(runtime.check_readiness(), heartbeat())

    assert heartbeat_ran.is_set()
    assert readiness.status is ReadinessStatus.READY_TO_ATTEMPT
    assert readiness.metadata["project_configured"] is True
    assert "private-project-id" not in repr(readiness)


@pytest.mark.asyncio
async def test_antigravity_adc_error_is_indeterminate_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fail_probe() -> _GoogleADCProbe:
        raise RuntimeError("secret-google-error")

    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity._probe_google_adc", fail_probe
    )
    runtime = AntigravityAgentRuntime(
        api_key=None,
        vertex=True,
        agent_cls=object(),
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["error_type"] == "RuntimeError"
    assert "secret-google-error" not in repr(readiness)


@pytest.mark.asyncio
async def test_antigravity_missing_adc_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    class DefaultCredentialsError(Exception):
        pass

    DefaultCredentialsError.__module__ = "google.auth.exceptions"

    def missing_probe() -> _GoogleADCProbe:
        raise DefaultCredentialsError("credentials were not found")

    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity._probe_google_adc", missing_probe
    )
    runtime = AntigravityAgentRuntime(
        api_key=None,
        vertex=True,
        agent_cls=object(),
    )

    readiness = await runtime.check_readiness()

    assert readiness.status is ReadinessStatus.NOT_READY
    assert readiness.reason is AvailabilityReason.MISSING_CREDENTIALS


@pytest.mark.asyncio
async def test_antigravity_adc_timeout_is_indeterminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    release_probe = threading.Event()

    def blocked_probe() -> _GoogleADCProbe:
        release_probe.wait(timeout=1)
        return _GoogleADCProbe(credentials_available=True, project="project")

    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity._probe_google_adc", blocked_probe
    )
    runtime = AntigravityAgentRuntime(
        api_key=None,
        vertex=True,
        agent_cls=object(),
    )
    try:
        readiness = await check_readiness(runtime, timeout=0.01)
    finally:
        release_probe.set()

    assert readiness.status is ReadinessStatus.INDETERMINATE
    assert readiness.metadata["failure"] == "timeout"


@pytest.mark.asyncio
async def test_async_provider_diagnostics_are_separate_from_package_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_diagnostics, "ClaudeAgentRuntime", FakeAgentRuntime)
    monkeypatch.setattr(provider_diagnostics, "CodexAgentRuntime", FakeAgentRuntime)
    monkeypatch.setattr(provider_diagnostics, "AntigravityAgentRuntime", FakeAgentRuntime)

    readiness = await provider_diagnostics.collect_provider_readiness(timeout=0.1)

    assert len(readiness) == 3
    assert all(item.status is ReadinessStatus.READY_TO_ATTEMPT for item in readiness)
