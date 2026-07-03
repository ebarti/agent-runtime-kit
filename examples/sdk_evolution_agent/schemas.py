"""Output schemas and validation for runtime-backed AI stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Literal, cast

from agent_runtime_kit import OutputTypeError, json_schema_for, parse_as

JsonSchema = dict[str, Any]


class SchemaValidationError(ValueError):
    """Raised when a runtime output does not satisfy the required shape."""


@dataclass(frozen=True)
class DirectionPackage:
    name: str
    direction: str
    evidence: list[str]


@dataclass(frozen=True)
class DirectionTheme:
    name: str
    summary: str


@dataclass(frozen=True)
class DirectionAnalysis:
    packages: list[DirectionPackage]
    themes: list[DirectionTheme]
    uncertainty: list[str]


@dataclass(frozen=True)
class ArchitectureFinding:
    classification: str
    summary: str
    evidence: list[str]


@dataclass(frozen=True)
class ArchitectureDecision:
    findings: list[ArchitectureFinding]
    safe_to_implement: bool
    manual_design_required: bool
    recursive_self_adaptation_impact: bool
    self_adaptation_plan: list[str]
    verification_commands: list[str]
    uncertainty: list[str]
    docs_test_changes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewerOutput:
    status: Literal["pass", "reject"]
    reasons: list[str]
    required_changes: list[str]


@dataclass(frozen=True)
class ImplementationSummary:
    applied: bool
    changes: list[str]
    blocked_reason: str


DIRECTION_ANALYSIS_SCHEMA: JsonSchema = json_schema_for(DirectionAnalysis)
ARCHITECTURE_DECISION_SCHEMA: JsonSchema = json_schema_for(ArchitectureDecision)
IMPLEMENTATION_SUMMARY_SCHEMA: JsonSchema = json_schema_for(ImplementationSummary)
REVIEWER_OUTPUT_SCHEMA: JsonSchema = json_schema_for(ReviewerOutput)

_STAGE_OUTPUT_TYPES: dict[str, Any] = {
    "direction-analysis": DirectionAnalysis,
    "architecture-decision": ArchitectureDecision,
    "review": ReviewerOutput,
    "implementation": ImplementationSummary,
    "review-implementation": ReviewerOutput,
}
_SCHEMA_OUTPUT_TYPES: tuple[tuple[JsonSchema, Any], ...] = (
    (DIRECTION_ANALYSIS_SCHEMA, DirectionAnalysis),
    (ARCHITECTURE_DECISION_SCHEMA, ArchitectureDecision),
    (REVIEWER_OUTPUT_SCHEMA, ReviewerOutput),
    (IMPLEMENTATION_SUMMARY_SCHEMA, ImplementationSummary),
)


def parse_stage_output(stage: str, data: Any) -> dict[str, Any]:
    """Validate a stage payload through the kit's typed bridge."""

    output_type = _STAGE_OUTPUT_TYPES.get(stage)
    if output_type is None:
        if not isinstance(data, dict):
            raise SchemaValidationError(f"{stage} must be an object")
        return dict(data)
    try:
        parsed = parse_as(output_type, data)
    except OutputTypeError as exc:
        raise SchemaValidationError(str(exc)) from exc
    return _dataclass_to_dict(parsed)


def validate_mapping(data: Any, schema: JsonSchema, *, name: str) -> dict[str, Any]:
    """Validate the small schema subset used by this example.

    The runtime stages use :func:`parse_stage_output`; this wrapper remains for
    direct tests and for any future caller that only has a JSON schema object.
    """

    for known_schema, output_type in _SCHEMA_OUTPUT_TYPES:
        if schema == known_schema:
            try:
                parsed = parse_as(output_type, data)
            except OutputTypeError as exc:
                raise SchemaValidationError(str(exc)) from exc
            return _dataclass_to_dict(parsed)
    _validate_schema_value(data, schema, path=name)
    if not isinstance(data, dict):
        raise SchemaValidationError(f"{name} must be an object")
    return dict(data)


def _dataclass_to_dict(value: Any) -> dict[str, Any]:
    if not is_dataclass(value):
        raise SchemaValidationError("validated stage output did not produce a dataclass")
    return asdict(cast(Any, value))


def _validate_schema_value(value: Any, schema: JsonSchema, *, path: str) -> None:
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path} must be one of {schema['enum']!r}")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise SchemaValidationError(f"{path} must be an object")
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        for field in required:
            if field not in value:
                raise SchemaValidationError(f"{path} missing required field {field!r}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise SchemaValidationError(f"{path} has unknown field(s): {', '.join(extra)}")
        for field, item in value.items():
            if field in properties:
                _validate_schema_value(item, properties[field], path=f"{path}.{field}")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(f"{path} must be an array")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, path=f"{path}[{index}]")
        return
    if expected == "boolean" and not isinstance(value, bool):
        raise SchemaValidationError(f"{path} must be a boolean")
    if expected == "string" and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string")
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise SchemaValidationError(f"{path} must be an integer")
    if expected == "number" and (
        not isinstance(value, int | float) or isinstance(value, bool)
    ):
        raise SchemaValidationError(f"{path} must be a number")
