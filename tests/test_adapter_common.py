from __future__ import annotations

import inspect

import pytest

from agent_runtime_kit import (
    AgentRuntimeKind,
    AgentTask,
    OutputSchemaError,
    UnsupportedTaskInputError,
)
from agent_runtime_kit.adapters._common import (
    filter_supported_kwargs,
    optional_int,
    output_schema_from,
)


def test_filter_supported_kwargs_rejects_required_key_hidden_by_var_kwargs() -> None:
    def opaque(**_kwargs: object) -> None:
        return None

    with pytest.raises(UnsupportedTaskInputError, match="explicit keyword parameters"):
        filter_supported_kwargs(
            opaque,
            {"sandbox": "workspace-write", "label": "kept"},
            required=("sandbox",),
            kind=AgentRuntimeKind.CODEX_AGENT_SDK,
        )


def test_filter_supported_kwargs_allows_explicit_required_key_with_var_kwargs() -> None:
    def explicit(*, sandbox: str, **_kwargs: object) -> None:
        del sandbox

    supported, dropped = filter_supported_kwargs(
        explicit,
        {"sandbox": "workspace-write", "future_option": True},
        required=("sandbox",),
        kind=AgentRuntimeKind.CODEX_AGENT_SDK,
    )

    assert supported == {"sandbox": "workspace-write", "future_option": True}
    assert dropped == []


def test_filter_supported_kwargs_rejects_uninspectable_required_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def vendor_callable(**_kwargs: object) -> None:
        return None

    def fail_signature(_factory: object) -> inspect.Signature:
        raise ValueError("opaque extension callable")

    monkeypatch.setattr(inspect, "signature", fail_signature)

    with pytest.raises(UnsupportedTaskInputError, match="cannot be inspected"):
        filter_supported_kwargs(
            vendor_callable,
            {"permission_mode": "default"},
            required=("permission_mode",),
            kind=AgentRuntimeKind.CLAUDE_AGENT_SDK,
        )


def test_filter_supported_kwargs_keeps_non_security_options_best_effort_when_opaque(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def vendor_callable(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        inspect,
        "signature",
        lambda _factory: (_ for _ in ()).throw(ValueError("opaque extension callable")),
    )

    supported, dropped = filter_supported_kwargs(vendor_callable, {"label": "kept"})

    assert supported == {"label": "kept"}
    assert dropped == []


def test_filter_supported_kwargs_rejects_positional_only_required_key() -> None:
    def positional_only(sandbox: str, /) -> None:
        del sandbox

    with pytest.raises(UnsupportedTaskInputError, match="explicit keyword parameters"):
        filter_supported_kwargs(
            positional_only,
            {"sandbox": "workspace-write"},
            required=("sandbox",),
            kind=AgentRuntimeKind.CODEX_AGENT_SDK,
        )


def test_filter_supported_kwargs_requires_kind_for_any_required_contract() -> None:
    with pytest.raises(TypeError, match="also requires kind"):
        filter_supported_kwargs(lambda: None, {}, required=("sandbox",))


def test_filter_supported_kwargs_maps_required_key_to_public_field() -> None:
    def opaque(*, permission_mode: str, **_kwargs: object) -> None:
        del permission_mode

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        filter_supported_kwargs(
            opaque,
            {"permission_mode": "default", "max_budget_usd": 1.0},
            required={
                "permission_mode": "permissions",
                "max_budget_usd": "budget_usd",
            },
            kind=AgentRuntimeKind.CLAUDE_AGENT_SDK,
        )

    assert exc_info.value.field == "budget_usd"


def test_output_schema_from_validates_legacy_aliases_and_nested_mutation() -> None:
    with pytest.raises(OutputSchemaError, match="invalid output_schema"):
        output_schema_from(None, {"output_schema": {"required": "not-an-array"}})

    source = {"type": "object", "required": ["ok"]}
    task = AgentTask(goal="x", output_schema=source)
    source["required"][0] = 1

    with pytest.raises(OutputSchemaError, match="invalid output_schema"):
        output_schema_from(task.output_schema, task.metadata)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (1.0, 1),
        ("2", 2),
        (1.9, None),
        (float("inf"), None),
        (-1, None),
        (True, None),
        ("not-a-number", None),
    ],
)
def test_optional_int_never_truncates_or_invents_vendor_counts(
    value: object, expected: int | None
) -> None:
    assert optional_int(value) == expected
