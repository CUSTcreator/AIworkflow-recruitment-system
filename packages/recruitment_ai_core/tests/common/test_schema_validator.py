from __future__ import annotations

import pytest

from recruitment_ai_core.llm.schema_validator import (
    JSONSchemaValidationError,
    validate_json_schema,
)


SCHEMA = {
    "type": "object",
    "required": ["items"],
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "level"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "level": {"type": "integer", "minimum": 0, "maximum": 5},
                    "note": {"type": ["string", "null"]},
                },
            },
        }
    },
}


def test_schema_validator_accepts_valid_nested_response() -> None:
    validate_json_schema({"items": [{"id": "A", "level": 3, "note": None}]}, SCHEMA)


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [{"id": "A"}]},
        {"items": [{"id": "A", "level": 6}]},
        {"items": [{"id": "A", "level": 3, "extra": True}]},
        {"items": []},
    ],
)
def test_schema_validator_rejects_invalid_response(payload: dict) -> None:
    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(payload, SCHEMA)


def test_schema_validator_supports_any_of_and_one_of() -> None:
    validate_json_schema("A", {"anyOf": [{"type": "string"}, {"type": "null"}]})
    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(1, {"oneOf": [{"type": "number"}, {"type": "integer"}]})


def test_schema_validator_rejects_duplicate_array_items() -> None:
    with pytest.raises(JSONSchemaValidationError, match="uniqueItems"):
        validate_json_schema(
            ["B_1", "B_1"],
            {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
        )
