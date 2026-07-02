"""Dependency-free bridge between Python types and JSON schema.

``json_schema_for`` turns an ``output_type`` into the JSON schema sent to the
vendor SDK; ``parse_as`` turns the returned payload back into an instance of
that type. Both support a deliberately bounded subset of the typing system —
scalars, ``X | None``, ``list[X]``, ``dict[str, X]``, ``Literal``, ``Enum``,
dataclasses, and ``TypedDict`` — and fail closed with ``OutputTypeError`` on
anything else rather than emitting a half-true schema.

Types that expose ``model_json_schema()`` and ``model_validate()`` (Pydantic
v2 models, or anything shaped like them) are delegated to those methods via a
structural check, so users who already have Pydantic get its full type
coverage without this package importing or depending on it.
"""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from collections.abc import Mapping
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from agent_runtime_kit._errors import OutputTypeError

# Deep enough for any sane payload model; recursive dataclasses hit this bound
# and fail closed instead of overflowing.
_MAX_DEPTH = 16

_SCALARS: dict[type, str] = {bool: "boolean", int: "integer", float: "number", str: "string"}


def supports_model_protocol(tp: Any) -> bool:
    """True when ``tp`` is Pydantic-shaped (model_json_schema + model_validate)."""

    return callable(getattr(tp, "model_json_schema", None)) and callable(
        getattr(tp, "model_validate", None)
    )


def json_schema_for(tp: Any) -> dict[str, Any]:
    """Return the JSON schema for ``tp``, or raise ``OutputTypeError``."""

    if supports_model_protocol(tp):
        schema = tp.model_json_schema()
        if not isinstance(schema, Mapping):
            raise OutputTypeError(
                f"{_name(tp)}.model_json_schema() returned {type(schema).__name__}, "
                "expected a mapping"
            )
        return dict(schema)
    return _schema(tp, depth=0)


def parse_as(tp: Any, value: Any) -> Any:
    """Validate ``value`` against ``tp`` and return the typed instance.

    Strict by design: extra object keys, missing required fields, and
    cross-type coercions (``"42"`` for ``int``, ``True`` for ``int``) all raise
    ``OutputTypeError``. ``AgentKit`` narrows the return to the requested type.
    """

    if supports_model_protocol(tp):
        try:
            return tp.model_validate(value)
        except Exception as exc:
            raise OutputTypeError(
                f"{_name(tp)}.model_validate() rejected the payload: {exc}"
            ) from exc
    return _parse(tp, value, path="$", depth=0)


def _schema(tp: Any, *, depth: int) -> dict[str, Any]:
    if depth > _MAX_DEPTH:
        raise OutputTypeError(
            f"output_type nesting exceeds {_MAX_DEPTH} levels (recursive types are unsupported)"
        )
    if tp is None or tp is type(None):
        return {"type": "null"}
    optional = _optional_inner(tp)
    if optional is not None:
        return {"anyOf": [_schema(optional, depth=depth + 1), {"type": "null"}]}
    origin = get_origin(tp)
    if origin is list:
        (item,) = get_args(tp) or (None,)
        if item is None:
            raise OutputTypeError("bare list is unsupported; use list[X]")
        return {"type": "array", "items": _schema(item, depth=depth + 1)}
    if origin is dict:
        args = get_args(tp)
        if len(args) != 2 or args[0] is not str:
            raise OutputTypeError("only dict[str, X] mappings are supported")
        return {"type": "object", "additionalProperties": _schema(args[1], depth=depth + 1)}
    if origin is Literal:
        values = get_args(tp)
        if not all(isinstance(item, (str, int, bool)) for item in values):
            raise OutputTypeError("Literal values must be str, int, or bool")
        return {"enum": list(values)}
    if isinstance(tp, type):
        if tp in _SCALARS:
            return {"type": _SCALARS[tp]}
        if issubclass(tp, enum.Enum):
            values = tuple(member.value for member in tp)
            if all(isinstance(item, str) for item in values):
                return {"type": "string", "enum": list(values)}
            raise OutputTypeError(f"enum {_name(tp)} must have all-string values")
        if dataclasses.is_dataclass(tp):
            return _object_schema(
                _dataclass_hints(tp),
                required=[
                    schema_field.name
                    for schema_field in dataclasses.fields(tp)
                    if schema_field.default is dataclasses.MISSING
                    and schema_field.default_factory is dataclasses.MISSING
                ],
                depth=depth,
            )
        if typing.is_typeddict(tp):
            # getattr: mypy narrows tp to `type` here, which has no
            # __required_keys__; every TypedDict class carries it at runtime.
            required_keys: frozenset[str] = getattr(tp, "__required_keys__", frozenset())
            return _object_schema(
                get_type_hints(tp),
                required=sorted(str(key) for key in required_keys),
                depth=depth,
            )
    raise OutputTypeError(
        f"unsupported annotation for output_type: {tp!r}; supported: str/int/float/bool, "
        "X | None, list[X], dict[str, X], Literal, str-valued Enum, dataclasses, TypedDict, "
        "or a Pydantic-style model"
    )


def _object_schema(
    hints: Mapping[str, Any], *, required: list[str], depth: int
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            name: _schema(annotation, depth=depth + 1) for name, annotation in hints.items()
        },
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _parse(tp: Any, value: Any, *, path: str, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise OutputTypeError(f"{path}: payload nesting exceeds {_MAX_DEPTH} levels")
    if tp is None or tp is type(None):
        if value is not None:
            raise OutputTypeError(f"{path}: expected null, got {type(value).__name__}")
        return None
    optional = _optional_inner(tp)
    if optional is not None:
        if value is None:
            return None
        return _parse(optional, value, path=path, depth=depth + 1)
    origin = get_origin(tp)
    if origin is list:
        (item,) = get_args(tp)
        if not isinstance(value, list):
            raise OutputTypeError(f"{path}: expected array, got {type(value).__name__}")
        return [
            _parse(item, entry, path=f"{path}[{index}]", depth=depth + 1)
            for index, entry in enumerate(value)
        ]
    if origin is dict:
        _, item = get_args(tp)
        if not isinstance(value, Mapping):
            raise OutputTypeError(f"{path}: expected object, got {type(value).__name__}")
        return {
            str(key): _parse(item, entry, path=f"{path}.{key}", depth=depth + 1)
            for key, entry in value.items()
        }
    if origin is Literal:
        if value not in get_args(tp):
            raise OutputTypeError(f"{path}: {value!r} is not one of {list(get_args(tp))}")
        return value
    if isinstance(tp, type):
        if tp in _SCALARS:
            return _parse_scalar(tp, value, path=path)
        if issubclass(tp, enum.Enum):
            try:
                return tp(value)
            except ValueError:
                valid = ", ".join(repr(member.value) for member in tp)
                raise OutputTypeError(f"{path}: {value!r} is not one of {valid}") from None
        if dataclasses.is_dataclass(tp):
            return _parse_dataclass(tp, value, path=path, depth=depth)
        if typing.is_typeddict(tp):
            return _parse_typeddict(tp, value, path=path, depth=depth)
    raise OutputTypeError(f"{path}: unsupported annotation {tp!r}")


def _parse_scalar(tp: type, value: Any, *, path: str) -> Any:
    # bool is an int subclass: check it first and never let it satisfy int/float.
    if tp is bool:
        if isinstance(value, bool):
            return value
    elif tp is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif tp is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif tp is str:
        if isinstance(value, str):
            return value
    raise OutputTypeError(f"{path}: expected {tp.__name__}, got {type(value).__name__}")


def _parse_dataclass(tp: type, value: Any, *, path: str, depth: int) -> Any:
    if not isinstance(value, Mapping):
        raise OutputTypeError(f"{path}: expected object, got {type(value).__name__}")
    hints = _dataclass_hints(tp)
    extra = sorted(set(value) - set(hints))
    if extra:
        raise OutputTypeError(f"{path}: unexpected keys {extra} for {_name(tp)}")
    kwargs: dict[str, Any] = {}
    for schema_field in dataclasses.fields(tp):
        name = schema_field.name
        if name in value:
            kwargs[name] = _parse(hints[name], value[name], path=f"{path}.{name}", depth=depth + 1)
        elif (
            schema_field.default is dataclasses.MISSING
            and schema_field.default_factory is dataclasses.MISSING
        ):
            raise OutputTypeError(f"{path}: missing required key {name!r} for {_name(tp)}")
    return tp(**kwargs)


def _parse_typeddict(tp: Any, value: Any, *, path: str, depth: int) -> Any:
    if not isinstance(value, Mapping):
        raise OutputTypeError(f"{path}: expected object, got {type(value).__name__}")
    hints = get_type_hints(tp)
    extra = sorted(set(value) - set(hints))
    if extra:
        raise OutputTypeError(f"{path}: unexpected keys {extra} for {_name(tp)}")
    missing = sorted(key for key in tp.__required_keys__ if key not in value)
    if missing:
        raise OutputTypeError(f"{path}: missing required keys {missing} for {_name(tp)}")
    return {
        key: _parse(hints[key], entry, path=f"{path}.{key}", depth=depth + 1)
        for key, entry in value.items()
    }


def _optional_inner(tp: Any) -> Any | None:
    """For ``X | None`` return ``X``; otherwise ``None``. Wider unions fail closed."""

    origin = get_origin(tp)
    if origin is not Union and origin is not types.UnionType:
        return None
    args = [arg for arg in get_args(tp) if arg is not type(None)]
    if len(args) == len(get_args(tp)):
        raise OutputTypeError("plain unions are unsupported; only X | None is")
    if len(args) != 1:
        raise OutputTypeError("only two-member optionals (X | None) are supported")
    return args[0]


def _dataclass_hints(tp: type) -> dict[str, Any]:
    # Resolves string annotations (`from __future__ import annotations`) to types.
    return get_type_hints(tp)


def _name(tp: Any) -> str:
    return str(getattr(tp, "__name__", tp))
