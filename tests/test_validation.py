"""Unit tests for :mod:`src.agent.validation`.

Covers schema validation before execution (Req 7.1), the descriptive
Input_Validation_Error content on failure (Req 7.2), and the optional per-tool
value-level ``validate_input`` hook run after schema validation (Req 7.3).

These are example-based unit tests; the property-based test for the loop's
rejection behavior lives in the dedicated Property subtask.
"""

from __future__ import annotations

from typing import Any, Dict

from src.agent.validation import (
    INPUT_VALIDATION_ERROR_PREFIX,
    ValidationResult,
    format_validation_error_content,
    validate_tool_args,
)


class _FakeTool:
    """Minimal tool stub exposing the real ``to_schema`` shape."""

    def __init__(self, properties, required, name="fake_tool", value_check=None):
        self._properties = properties
        self._required = required
        self._name = name
        self._value_check = value_check

    @property
    def name(self):
        return self._name

    @property
    def parameters(self):
        return self._properties

    @property
    def required_params(self):
        return self._required

    def to_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self._name,
                "description": "",
                "parameters": {
                    "type": "object",
                    "properties": self._properties,
                    "required": self._required,
                },
            },
        }

    # Optionally exposed value-level check.
    def __getattr__(self, item):
        if item == "validate_input" and self.__dict__.get("_value_check") is not None:
            return self.__dict__["_value_check"]
        raise AttributeError(item)


def _tool(properties=None, required=None, name="fake_tool", value_check=None):
    return _FakeTool(
        properties or {},
        required if required is not None else list((properties or {}).keys()),
        name=name,
        value_check=value_check,
    )


def test_valid_args_pass():
    tool = _tool(
        {"path": {"type": "string"}, "limit": {"type": "integer"}},
        required=["path"],
    )
    result = validate_tool_args(tool, {"path": "a.txt", "limit": 5})
    assert result.ok is True
    assert result.message == ""
    assert bool(result) is True


def test_missing_required_property_fails():
    tool = _tool({"path": {"type": "string"}}, required=["path"])
    result = validate_tool_args(tool, {})
    assert result.ok is False
    assert result.message.startswith(INPUT_VALIDATION_ERROR_PREFIX)
    assert "path" in result.message


def test_wrong_type_fails():
    tool = _tool({"limit": {"type": "integer"}}, required=["limit"])
    result = validate_tool_args(tool, {"limit": "not-an-int"})
    assert result.ok is False
    assert INPUT_VALIDATION_ERROR_PREFIX in result.message


def test_non_dict_args_rejected():
    tool = _tool({"path": {"type": "string"}}, required=["path"])
    result = validate_tool_args(tool, ["not", "a", "dict"])  # type: ignore[arg-type]
    assert result.ok is False
    assert "object" in result.message or "dict" in result.message


def test_none_args_treated_as_empty():
    tool = _tool({"path": {"type": "string"}}, required=[])
    result = validate_tool_args(tool, None)
    assert result.ok is True


def test_value_level_check_rejects_after_schema_passes():
    def reject(args):
        return (False, "path must be absolute")

    tool = _tool({"path": {"type": "string"}}, required=["path"], value_check=reject)
    result = validate_tool_args(tool, {"path": "relative.txt"})
    assert result.ok is False
    assert "path must be absolute" in result.message
    assert result.message.startswith(INPUT_VALIDATION_ERROR_PREFIX)


def test_value_level_check_accepts():
    def accept(args):
        return (True, "")

    tool = _tool({"path": {"type": "string"}}, required=["path"], value_check=accept)
    result = validate_tool_args(tool, {"path": "/abs/path"})
    assert result.ok is True


def test_value_level_check_bare_bool():
    tool = _tool({"x": {"type": "string"}}, required=[], value_check=lambda a: False)
    result = validate_tool_args(tool, {"x": "y"})
    assert result.ok is False


def test_value_level_check_raises_is_rejection():
    def boom(args):
        raise RuntimeError("kaboom")

    tool = _tool({"x": {"type": "string"}}, required=[], value_check=boom)
    result = validate_tool_args(tool, {"x": "y"})
    assert result.ok is False


def test_schema_check_runs_before_value_check():
    """If schema fails, the value-level check must not be the source of truth."""
    calls = {"value_check": 0}

    def value_check(args):
        calls["value_check"] += 1
        return (True, "")

    tool = _tool({"n": {"type": "integer"}}, required=["n"], value_check=value_check)
    # Schema fails (missing required 'n'); value check should not flip it to ok.
    result = validate_tool_args(tool, {})
    assert result.ok is False
    assert calls["value_check"] == 0


def test_format_validation_error_content_for_failure():
    result = ValidationResult(ok=False, message="InputValidationError: bad")
    content = format_validation_error_content(result)
    assert content == "InputValidationError: bad"


def test_format_validation_error_content_adds_prefix():
    result = ValidationResult(ok=False, message="something went wrong")
    content = format_validation_error_content(result)
    assert content.startswith(INPUT_VALIDATION_ERROR_PREFIX)


def test_format_validation_error_content_empty_on_success():
    assert format_validation_error_content(ValidationResult(ok=True)) == ""


def test_tool_without_value_check_passes_on_valid_schema():
    """A tool that does not define validate_input is handled defensively."""
    tool = _tool({"q": {"type": "string"}}, required=["q"])
    assert validate_tool_args(tool, {"q": "hello"}).ok is True


def test_enum_constraint_minimal_path():
    tool = _tool(
        {"mode": {"type": "string", "enum": ["r", "w"]}},
        required=["mode"],
    )
    assert validate_tool_args(tool, {"mode": "r"}).ok is True
    assert validate_tool_args(tool, {"mode": "x"}).ok is False
