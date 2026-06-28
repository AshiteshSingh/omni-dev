"""
Tool input validation for the Agent Loop.

Mirrors the reference TypeScript implementation's ``zod`` ``safeParse`` →
``validateInput`` flow (see ``scratch_repo`` ``query.ts`` /
``checkPermissionsAndCallTool``). Before a model-requested tool is executed,
the model-generated arguments are validated against the tool's declared JSON
input schema, and then against any per-tool value-level check the tool defines.

Design references:
- design.md "Agent Loop" / "Input Validation (agent/validation.py)"
- Requirements 7.1 (schema validation before execution), 7.2 (Input_Validation_Error
  result on failure), 7.3 (per-tool value-level check after schema validation).

This module is intentionally pure and import-safe: ``python -c "import
src.agent.validation"`` must succeed with no side effects. It prefers the
``jsonschema`` package when available (commonly present transitively via
``litellm``) and otherwise falls back to a minimal self-contained validator so
the project gains no hard new dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Prefer the real jsonschema validator when present; otherwise fall back to a
# lightweight built-in validator. Import is wrapped so the module stays
# import-safe in any environment.
try:  # pragma: no cover - exercised indirectly depending on environment
    import jsonschema as _jsonschema  # type: ignore

    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover - fallback path
    _jsonschema = None  # type: ignore
    _HAS_JSONSCHEMA = False


#: Prefix used for the structured error result fed back to the model when
#: validation fails (mirrors the reference Input_Validation_Error content).
INPUT_VALIDATION_ERROR_PREFIX = "InputValidationError"


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating tool arguments.

    Attributes:
        ok: ``True`` when the arguments satisfy the schema and any value-level
            check; ``False`` otherwise.
        message: Empty when ``ok`` is ``True``. On failure, a descriptive
            message beginning with ``"InputValidationError: "``.
    """

    ok: bool
    message: str = ""

    def __bool__(self) -> bool:  # convenience: ``if result:``
        return self.ok


# Map a JSON Schema "type" name to the accepted Python runtime types.
# ``bool`` is intentionally excluded from the numeric types because in Python
# ``bool`` is a subclass of ``int`` but is semantically distinct here.
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def _extract_schema(tool: Any) -> Dict[str, Any]:
    """Return a JSON-schema ``object`` definition for a tool's parameters.

    Defensive about the tool interface: prefers ``to_schema()`` (the litellm
    function shape) and falls back to assembling a schema from ``parameters``
    and ``required_params``. Always returns a dict with at least ``type`` and
    ``properties`` keys so downstream validation is uniform.
    """
    # Preferred: the litellm function-schema shape exposes the parameters block.
    to_schema = getattr(tool, "to_schema", None)
    if callable(to_schema):
        try:
            schema = to_schema()
            params = (
                schema.get("function", {}).get("parameters")
                if isinstance(schema, dict)
                else None
            )
            if isinstance(params, dict):
                return params
        except Exception:
            # Fall through to manual assembly below.
            pass

    # Fallback: assemble from the raw parameter properties + required list.
    properties = getattr(tool, "parameters", None)
    if not isinstance(properties, dict):
        properties = {}

    required = getattr(tool, "required_params", None)
    if not isinstance(required, (list, tuple)):
        required = list(properties.keys())

    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
    }


def _type_matches(value: Any, expected: Any) -> bool:
    """Check a value against a JSON-schema ``type`` (string or list of strings)."""
    if expected is None:
        return True
    types = expected if isinstance(expected, list) else [expected]
    for t in types:
        check = _TYPE_CHECKS.get(t)
        # Unknown type names are treated permissively (graceful degradation).
        if check is None or check(value):
            return True
    return False


def _minimal_validate(schema: Mapping[str, Any], args: Mapping[str, Any]) -> List[str]:
    """A small self-contained validator used when ``jsonschema`` is absent.

    Performs the checks called out in the design: required-property presence
    and basic per-property type checks. Unknown or unsupported schema keywords
    are ignored rather than rejected, so the validator degrades gracefully.

    Returns a list of human-readable error strings (empty when valid).
    """
    errors: List[str] = []

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    # Required properties must be present.
    required = schema.get("required", [])
    if isinstance(required, (list, tuple)):
        for key in required:
            if key not in args:
                errors.append(f"missing required property '{key}'")

    # Basic type checks for any provided properties that declare a type.
    for key, value in args.items():
        prop_schema = properties.get(key)
        if not isinstance(prop_schema, dict):
            # Unknown property or non-object schema: nothing to check.
            continue
        expected = prop_schema.get("type")
        if expected is not None and not _type_matches(value, expected):
            got = type(value).__name__
            want = expected if isinstance(expected, str) else "/".join(expected)
            errors.append(
                f"property '{key}' expected type {want} but got {got}"
            )
            continue

        # Lightweight enum support: a useful, common value-level constraint.
        enum = prop_schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(
                f"property '{key}' value {value!r} is not one of the allowed values"
            )

    return errors


def _schema_validate(schema: Mapping[str, Any], args: Mapping[str, Any]) -> List[str]:
    """Validate ``args`` against ``schema`` returning a list of error strings."""
    if _HAS_JSONSCHEMA:
        try:
            validator_cls = _jsonschema.validators.validator_for(schema)  # type: ignore[attr-defined]
            validator_cls.check_schema(schema)
            validator = validator_cls(schema)
            errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
            return [_format_jsonschema_error(e) for e in errors]
        except _jsonschema.exceptions.SchemaError:  # type: ignore[union-attr]
            # The tool's declared schema is itself invalid; fall back to the
            # minimal validator rather than crashing the loop.
            return _minimal_validate(schema, args)
        except Exception:
            # Any unexpected jsonschema failure degrades to the minimal path.
            return _minimal_validate(schema, args)
    return _minimal_validate(schema, args)


def _format_jsonschema_error(error: Any) -> str:
    """Render a ``jsonschema`` ValidationError into a concise string."""
    path = "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in error.path)
    location = f" at {path}" if path else ""
    return f"{error.message}{location}"


def _call_value_check(tool: Any, args: Mapping[str, Any]) -> Optional[Tuple[bool, str]]:
    """Invoke a tool's optional value-level input check, if it defines one.

    Looks for a ``validate_input`` method (the reference name); also accepts a
    ``validate_args`` alias. Defensive: tools are not required to define one,
    and a check that raises is treated as a rejection with a descriptive
    message rather than propagating the exception.

    Returns:
        ``None`` if the tool defines no value-level check.
        Otherwise a ``(ok, message)`` tuple where ``ok`` indicates acceptance.
    """
    hook = getattr(tool, "validate_input", None)
    if not callable(hook):
        hook = getattr(tool, "validate_args", None)
    if not callable(hook):
        return None

    try:
        result = hook(args)
    except Exception as exc:  # be defensive: a raising hook rejects the call
        return False, f"value-level validation raised: {exc}"

    return _coerce_check_result(result)


def _coerce_check_result(result: Any) -> Tuple[bool, str]:
    """Normalize a variety of value-check return shapes into ``(ok, message)``.

    Accepts:
      - ``(ok: bool, message: str)`` tuple/list
      - a bare ``bool`` (message defaults to empty / generic)
      - ``None`` (treated as acceptance, mirroring "no objection")
      - an object exposing ``.ok``/``.result`` and ``.message`` attributes
    """
    if result is None:
        return True, ""
    if isinstance(result, bool):
        return result, "" if result else "value-level validation failed"
    if isinstance(result, (tuple, list)) and len(result) >= 1:
        ok = bool(result[0])
        message = str(result[1]) if len(result) >= 2 and result[1] is not None else ""
        return ok, message
    # Duck-typed result object.
    ok_attr = getattr(result, "ok", getattr(result, "result", None))
    if ok_attr is not None:
        message = getattr(result, "message", "") or ""
        return bool(ok_attr), str(message)
    # Unknown truthy/falsey object.
    return bool(result), "" if result else "value-level validation failed"


def validate_tool_args(tool: Any, args: Optional[Dict[str, Any]]) -> ValidationResult:
    """Validate model-generated tool arguments before execution.

    Performs, in order (Requirements 7.1 → 7.3):
      1. Schema validation of ``args`` against the tool's declared JSON input
         schema (via ``jsonschema`` when available, else the minimal validator).
      2. If the schema passes, an optional per-tool value-level check
         (``validate_input``) when the tool defines one.

    Args:
        tool: A tool instance exposing ``to_schema()`` or ``parameters`` /
            ``required_params``, and optionally a ``validate_input`` hook.
        args: The model-generated arguments. ``None`` is treated as ``{}``.

    Returns:
        A :class:`ValidationResult`. On failure ``message`` is a descriptive
        string beginning with ``"InputValidationError: "``.
    """
    if args is None:
        args = {}

    # Guard: arguments must be a mapping of named parameters.
    if not isinstance(args, dict):
        return ValidationResult(
            ok=False,
            message=(
                f"{INPUT_VALIDATION_ERROR_PREFIX}: arguments must be an object/dict, "
                f"got {type(args).__name__}"
            ),
        )

    schema = _extract_schema(tool)

    # 1. Schema-level validation (Req 7.1, 7.2).
    schema_errors = _schema_validate(schema, args)
    if schema_errors:
        tool_name = getattr(tool, "name", tool.__class__.__name__)
        details = "; ".join(schema_errors)
        return ValidationResult(
            ok=False,
            message=(
                f"{INPUT_VALIDATION_ERROR_PREFIX}: arguments for tool "
                f"'{tool_name}' failed schema validation: {details}"
            ),
        )

    # 2. Per-tool value-level check, only after schema validation passes (Req 7.3).
    check = _call_value_check(tool, args)
    if check is not None:
        ok, message = check
        if not ok:
            tool_name = getattr(tool, "name", tool.__class__.__name__)
            detail = message or "value-level validation failed"
            return ValidationResult(
                ok=False,
                message=(
                    f"{INPUT_VALIDATION_ERROR_PREFIX}: arguments for tool "
                    f"'{tool_name}' were rejected: {detail}"
                ),
            )

    return ValidationResult(ok=True, message="")


def format_validation_error_content(result: ValidationResult) -> str:
    """Return the tool-result content string for a failed validation.

    Used by the Agent Loop to build the Input_Validation_Error ``tool`` message
    appended to the conversation history (flagged as an error) instead of
    executing the tool. For a successful result this returns an empty string.

    The returned string always begins with the ``InputValidationError`` prefix
    so the model can recognize it as an error result (mirroring the reference
    ``is_error`` content marker).
    """
    if result.ok:
        return ""
    message = result.message or f"{INPUT_VALIDATION_ERROR_PREFIX}: invalid tool arguments"
    if not message.startswith(INPUT_VALIDATION_ERROR_PREFIX):
        message = f"{INPUT_VALIDATION_ERROR_PREFIX}: {message}"
    return message
