from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

import pytest

from agent_runtime_kit import OutputTypeError
from agent_runtime_kit._schema import json_schema_for, parse_as


class Color(str, enum.Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class Point:
    x: int
    y: int
    label: str = "origin"


@dataclass
class Shape:
    name: Literal["circle", "square"]
    points: list[Point]
    color: Color | None = None
    tags: dict[str, str] = field(default_factory=dict)


class Movie(TypedDict):
    title: str
    year: int


def test_schema_scalars_and_containers() -> None:
    assert json_schema_for(str) == {"type": "string"}
    assert json_schema_for(bool) == {"type": "boolean"}
    assert json_schema_for(int) == {"type": "integer"}
    assert json_schema_for(float) == {"type": "number"}
    assert json_schema_for(list[int]) == {"type": "array", "items": {"type": "integer"}}
    assert json_schema_for(dict[str, bool]) == {
        "type": "object",
        "additionalProperties": {"type": "boolean"},
    }
    assert json_schema_for(str | None) == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert json_schema_for(Literal["a", "b"]) == {"enum": ["a", "b"]}
    assert json_schema_for(Color) == {"type": "string", "enum": ["red", "blue"]}


def test_schema_dataclass_shape() -> None:
    schema = json_schema_for(Shape)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    # Only fields without defaults are required.
    assert schema["required"] == ["name", "points"]
    assert schema["properties"]["name"] == {"enum": ["circle", "square"]}
    assert schema["properties"]["points"] == {
        "type": "array",
        "items": json_schema_for(Point),
    }
    assert schema["properties"]["color"] == {
        "anyOf": [{"type": "string", "enum": ["red", "blue"]}, {"type": "null"}]
    }
    assert json_schema_for(Point)["required"] == ["x", "y"]


def test_schema_typeddict_shape() -> None:
    schema = json_schema_for(Movie)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "year"]
    assert schema["properties"]["year"] == {"type": "integer"}


@pytest.mark.parametrize(
    "annotation",
    [
        set[str],
        tuple[str, ...],
        str | int,
        list,
        dict[int, str],
        bytes,
        Any,
    ],
    ids=["set", "tuple", "union", "bare-list", "int-keyed-dict", "bytes", "any"],
)
def test_schema_unsupported_annotations_fail_closed(annotation: Any) -> None:
    with pytest.raises(OutputTypeError):
        json_schema_for(annotation)


def test_schema_recursive_dataclass_fails_closed() -> None:
    @dataclass
    class Node:
        children: list[Node]

    # get_type_hints resolves the local forward reference via localns lookup
    # failing — either way the bounded walk must raise, never overflow.
    with pytest.raises((OutputTypeError, NameError)):
        json_schema_for(Node)


def test_parse_as_nested_dataclass_happy_path() -> None:
    shape = parse_as(
        Shape,
        {
            "name": "circle",
            "points": [{"x": 1, "y": 2}, {"x": 3, "y": 4, "label": "corner"}],
            "color": "red",
            "tags": {"kind": "demo"},
        },
    )

    assert isinstance(shape, Shape)
    assert shape.points[0] == Point(x=1, y=2)  # default label filled
    assert shape.points[1].label == "corner"
    assert shape.color is Color.RED
    assert shape.tags == {"kind": "demo"}
    # Optional accepts absent and null alike.
    assert parse_as(Shape, {"name": "square", "points": [], "color": None}).color is None


def test_parse_as_rejects_extra_and_missing_keys() -> None:
    with pytest.raises(OutputTypeError, match="unexpected keys"):
        parse_as(Point, {"x": 1, "y": 2, "z": 3})
    with pytest.raises(OutputTypeError, match="missing required key"):
        parse_as(Point, {"x": 1})
    with pytest.raises(OutputTypeError, match="missing required keys"):
        parse_as(Movie, {"title": "Alien"})


def test_parse_as_is_strict_about_scalars() -> None:
    # No cross-type coercion: strings stay strings, bool never satisfies int.
    with pytest.raises(OutputTypeError, match=r"\$\.x: expected int"):
        parse_as(Point, {"x": "1", "y": 2})
    with pytest.raises(OutputTypeError, match="expected int"):
        parse_as(int, True)
    # float accepts an integral JSON number (JSON has one number type).
    assert parse_as(float, 3) == 3.0
    assert parse_as(bool, True) is True


def test_parse_as_error_paths_point_into_the_payload() -> None:
    with pytest.raises(OutputTypeError, match=r"\$\.points\[1\]\.y"):
        parse_as(Shape, {"name": "circle", "points": [{"x": 1, "y": 2}, {"x": 1, "y": "no"}]})
    with pytest.raises(OutputTypeError, match="not one of"):
        parse_as(Shape, {"name": "triangle", "points": []})
    with pytest.raises(OutputTypeError, match="not one of"):
        parse_as(Color, "green")


class DuckModel:
    """Pydantic-shaped without Pydantic: structural protocol must suffice."""

    @classmethod
    def model_json_schema(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {"n": {"type": "integer"}}}

    @classmethod
    def model_validate(cls, value: Any) -> DuckModel:
        if not isinstance(value, dict) or "n" not in value:
            raise ValueError("n is required")
        instance = cls()
        instance.n = value["n"]  # type: ignore[attr-defined]
        return instance


def test_model_protocol_duck_typing_covers_both_directions() -> None:
    assert json_schema_for(DuckModel) == {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
    }

    parsed = parse_as(DuckModel, {"n": 7})
    assert isinstance(parsed, DuckModel)
    assert parsed.n == 7  # type: ignore[attr-defined]

    # Validation failures wrap into the typed error, whatever the model raised.
    with pytest.raises(OutputTypeError, match="rejected the payload"):
        parse_as(DuckModel, {"wrong": 1})


def test_model_protocol_bad_schema_shape_fails_closed() -> None:
    class BadDuck:
        @classmethod
        def model_json_schema(cls) -> list[int]:
            return [1]

        @classmethod
        def model_validate(cls, value: Any) -> BadDuck:
            return cls()

    with pytest.raises(OutputTypeError, match="expected a mapping"):
        json_schema_for(BadDuck)
