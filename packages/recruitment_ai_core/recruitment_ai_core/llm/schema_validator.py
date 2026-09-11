from __future__ import annotations

from typing import Any


class JSONSchemaValidationError(ValueError):
    """Raised when a model response does not satisfy the local JSON Schema."""


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    path: str = "$",
    *,
    _root_schema: dict[str, Any] | None = None,
) -> None:
    """Validate the JSON Schema subset used by this project.

    The project schemas use local ``$ref`` entries to share nested contracts.
    Keep the validator small, but resolve those local references so nested
    required fields and type constraints are actually enforced.
    """
    root_schema = _root_schema or schema
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise JSONSchemaValidationError(f"{path}:unsupported_ref")
        validate_json_schema(
            value,
            _resolve_local_ref(root_schema, reference),
            path,
            _root_schema=root_schema,
        )
        return
    if "anyOf" in schema:
        if not any(
            _matches(value, branch, path, root_schema)
            for branch in schema["anyOf"]
        ):
            raise JSONSchemaValidationError(f"{path}:anyOf")
    if "oneOf" in schema:
        matched = sum(
            _matches(value, branch, path, root_schema)
            for branch in schema["oneOf"]
        )
        if matched != 1:
            raise JSONSchemaValidationError(f"{path}:oneOf")
    if "const" in schema and value != schema["const"]:
        raise JSONSchemaValidationError(f"{path}:const")
    if "enum" in schema and value not in schema["enum"]:
        raise JSONSchemaValidationError(f"{path}:enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_is_type(value, type_name) for type_name in allowed):
            raise JSONSchemaValidationError(f"{path}:type")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise JSONSchemaValidationError(f"{path}:required:{','.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = [key for key in value if key not in properties]
            if extras:
                raise JSONSchemaValidationError(f"{path}:additionalProperties:{','.join(extras)}")
        for key, child in properties.items():
            if key in value:
                validate_json_schema(
                    value[key], child, f"{path}.{key}", _root_schema=root_schema
                )

    if isinstance(value, list):
        if schema.get("uniqueItems"):
            for index, item in enumerate(value):
                if any(item == previous for previous in value[:index]):
                    raise JSONSchemaValidationError(f"{path}:uniqueItems")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise JSONSchemaValidationError(f"{path}:minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise JSONSchemaValidationError(f"{path}:maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(
                    item, item_schema, f"{path}[{index}]", _root_schema=root_schema
                )

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise JSONSchemaValidationError(f"{path}:minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise JSONSchemaValidationError(f"{path}:maxLength")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise JSONSchemaValidationError(f"{path}:minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise JSONSchemaValidationError(f"{path}:maximum")


def _matches(
    value: Any,
    schema: dict[str, Any],
    path: str,
    root_schema: dict[str, Any],
) -> bool:
    try:
        validate_json_schema(value, schema, path, _root_schema=root_schema)
        return True
    except JSONSchemaValidationError:
        return False


def _is_type(value: Any, type_name: str) -> bool:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    check = checks.get(type_name)
    if check is None:
        raise JSONSchemaValidationError(f"unsupported_schema_type:{type_name}")
    return check(value)


def _resolve_local_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    """Resolve a JSON Pointer reference within the root schema."""
    current: Any = root_schema
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise JSONSchemaValidationError(f"unsupported_ref:{reference}")
        current = current[token]
    if not isinstance(current, dict):
        raise JSONSchemaValidationError(f"unsupported_ref:{reference}")
    return current
