"""Output schemas and validation for runtime-backed AI stages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

JsonSchema = dict[str, Any]


class SchemaValidationError(ValueError):
    """Raised when a runtime output does not satisfy the required shape."""


DIRECTION_ANALYSIS_SCHEMA: JsonSchema = {
    "type": "object",
    "required": ["packages", "themes", "uncertainty"],
    "additionalProperties": False,
    "properties": {
        "packages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "direction", "evidence"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "direction": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "summary"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
        "uncertainty": {"type": "array", "items": {"type": "string"}},
    },
}

ARCHITECTURE_DECISION_SCHEMA: JsonSchema = {
    "type": "object",
    "required": [
        "findings",
        "safe_to_implement",
        "manual_design_required",
        "recursive_self_adaptation_impact",
        "self_adaptation_plan",
        "verification_commands",
        "uncertainty",
    ],
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["classification", "summary", "evidence"],
                "additionalProperties": False,
                "properties": {
                    "classification": {"type": "string"},
                    "summary": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "safe_to_implement": {"type": "boolean"},
        "manual_design_required": {"type": "boolean"},
        "recursive_self_adaptation_impact": {"type": "boolean"},
        "self_adaptation_plan": {"type": "array", "items": {"type": "string"}},
        "verification_commands": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "array", "items": {"type": "string"}},
    },
}

IMPLEMENTATION_SUMMARY_SCHEMA: JsonSchema = {
    "type": "object",
    "required": ["applied", "changes", "verification_results", "blocked_reason"],
    "additionalProperties": False,
    "properties": {
        "applied": {"type": "boolean"},
        "changes": {"type": "array", "items": {"type": "string"}},
        "verification_results": {"type": "array", "items": {"type": "string"}},
        "blocked_reason": {"type": "string"},
    },
}

REVIEWER_OUTPUT_SCHEMA: JsonSchema = {
    "type": "object",
    "required": ["status", "reasons", "required_changes"],
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["pass", "reject"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "required_changes": {"type": "array", "items": {"type": "string"}},
    },
}


def validate_mapping(data: Any, schema: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    """Validate the small schema subset used by this example."""

    if not isinstance(data, dict):
        raise SchemaValidationError(f"{name} must be an object")
    for field in schema.get("required", ()):
        if field not in data:
            raise SchemaValidationError(f"{name} missing required field {field!r}")
    properties = schema.get("properties", {})
    for field, field_schema in properties.items():
        if field not in data:
            continue
        expected = field_schema.get("type")
        if expected == "array" and not isinstance(data[field], list):
            raise SchemaValidationError(f"{name}.{field} must be an array")
        if expected == "boolean" and not isinstance(data[field], bool):
            raise SchemaValidationError(f"{name}.{field} must be a boolean")
        if expected == "string" and not isinstance(data[field], str):
            raise SchemaValidationError(f"{name}.{field} must be a string")
    return data
