"""Comprehensive tests for xbot/tools/base.py — the Tool base class."""

import pytest

from xbot.tools.base import Tool


# ---------------------------------------------------------------------------
# Concrete test subclass
# ---------------------------------------------------------------------------

class ConcreteTool(Tool):
    """Minimal concrete Tool for testing base-class methods."""

    def __init__(self, name="test_tool", description="A test tool", parameters=None):
        self._name = name
        self._description = description
        self._parameters = parameters if parameters is not None else {}

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._description

    @property
    def parameters(self):
        return self._parameters

    async def execute(self, **kwargs):
        return "ok"


# ===================================================================
# cast_params — basic type casting
# ===================================================================

class TestCastParamsStringToInt:
    def test_string_to_integer(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        })
        assert tool.cast_params({"count": "42"}) == {"count": 42}

    def test_string_to_integer_negative(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        })
        assert tool.cast_params({"count": "-5"}) == {"count": -5}

    def test_string_to_integer_invalid_returns_original(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        })
        assert tool.cast_params({"count": "abc"}) == {"count": "abc"}

    def test_integer_passthrough(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        })
        assert tool.cast_params({"count": 42}) == {"count": 42}

    def test_bool_not_cast_to_integer(self):
        """bool is subclass of int — must NOT be passed through as int."""
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        })
        # True is bool; the int check excludes bools, so falls through
        result = tool.cast_params({"count": True})
        # True is not a str, so int() path not taken; returns as-is
        assert result == {"count": True}


class TestCastParamsStringToFloat:
    def test_string_to_number_float(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"price": {"type": "number"}},
        })
        assert tool.cast_params({"price": "3.14"}) == {"price": 3.14}

    def test_string_to_number_int_string(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"price": {"type": "number"}},
        })
        assert tool.cast_params({"price": "42"}) == {"price": 42.0}

    def test_string_to_number_invalid(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"price": {"type": "number"}},
        })
        assert tool.cast_params({"price": "xyz"}) == {"price": "xyz"}

    def test_float_passthrough(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"price": {"type": "number"}},
        })
        assert tool.cast_params({"price": 3.14}) == {"price": 3.14}

    def test_int_passthrough_as_number(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"price": {"type": "number"}},
        })
        assert tool.cast_params({"price": 42}) == {"price": 42}


class TestCastParamsStringToBool:
    def test_true_strings(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
        })
        for v in ("true", "True", "TRUE", "1", "yes", "Yes", "YES"):
            assert tool.cast_params({"flag": v}) == {"flag": True}, f"failed for {v!r}"

    def test_false_strings(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
        })
        for v in ("false", "False", "FALSE", "0", "no", "No", "NO"):
            assert tool.cast_params({"flag": v}) == {"flag": False}, f"failed for {v!r}"

    def test_unrecognized_string_returns_original(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
        })
        assert tool.cast_params({"flag": "maybe"}) == {"flag": "maybe"}

    def test_bool_passthrough(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
        })
        assert tool.cast_params({"flag": True}) == {"flag": True}
        assert tool.cast_params({"flag": False}) == {"flag": False}


class TestCastParamsStringPassthrough:
    def test_string_already_string(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
        })
        assert tool.cast_params({"name": "hello"}) == {"name": "hello"}

    def test_int_to_string(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
        })
        assert tool.cast_params({"name": 42}) == {"name": "42"}

    def test_none_to_string_stays_none(self):
        """None is special-cased: returns None, not 'None'."""
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
        })
        assert tool.cast_params({"name": None}) == {"name": None}

    def test_bool_to_string(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
        })
        assert tool.cast_params({"name": True}) == {"name": "True"}


class TestCastParamsArrayItems:
    def test_array_of_integers(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "nums": {"type": "array", "items": {"type": "integer"}},
            },
        })
        assert tool.cast_params({"nums": ["1", "2", "3"]}) == {"nums": [1, 2, 3]}

    def test_array_of_strings(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        })
        assert tool.cast_params({"tags": [1, 2, 3]}) == {"tags": ["1", "2", "3"]}

    def test_array_no_items_schema(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "items": {"type": "array"},
            },
        })
        assert tool.cast_params({"items": ["a", "b"]}) == {"items": ["a", "b"]}

    def test_array_already_correct_type(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "nums": {"type": "array", "items": {"type": "integer"}},
            },
        })
        assert tool.cast_params({"nums": [1, 2, 3]}) == {"nums": [1, 2, 3]}


class TestCastParamsNestedObject:
    def test_nested_object_casting(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "timeout": {"type": "integer"},
                        "verbose": {"type": "boolean"},
                    },
                },
            },
        })
        result = tool.cast_params({"config": {"timeout": "30", "verbose": "yes"}})
        assert result == {"config": {"timeout": 30, "verbose": True}}

    def test_deeply_nested(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {"c": {"type": "integer"}},
                        },
                    },
                },
            },
        })
        result = tool.cast_params({"a": {"b": {"c": "99"}}})
        assert result == {"a": {"b": {"c": 99}}}


# ===================================================================
# cast_params — edge cases
# ===================================================================

class TestCastParamsEdgeCases:
    def test_non_object_schema_returns_params_unchanged(self):
        tool = ConcreteTool(parameters={"type": "string"})
        assert tool.cast_params({"anything": "value"}) == {"anything": "value"}

    def test_empty_schema(self):
        tool = ConcreteTool(parameters={})
        assert tool.cast_params({"x": "1"}) == {"x": "1"}

    def test_none_parameters(self):
        tool = ConcreteTool(parameters=None)
        assert tool.cast_params({"x": "1"}) == {"x": "1"}

    def test_missing_properties_in_schema(self):
        """Properties in params but not in schema pass through unchanged."""
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"known": {"type": "integer"}},
        })
        result = tool.cast_params({"known": "5", "unknown": "hello"})
        assert result == {"known": 5, "unknown": "hello"}

    def test_unknown_property_not_cast(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {},
        })
        result = tool.cast_params({"x": "123"})
        assert result == {"x": "123"}

    def test_cast_object_non_dict_returns_as_is(self):
        """_cast_object called with non-dict returns it unchanged."""
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "obj": {"type": "object", "properties": {"x": {"type": "integer"}}},
            },
        })
        # Pass a string where object expected — not a dict, so no casting
        result = tool.cast_params({"obj": "not_a_dict"})
        assert result == {"obj": "not_a_dict"}

    def test_empty_params(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        })
        assert tool.cast_params({}) == {}

    def test_no_type_in_property_schema(self):
        """Property schema without 'type' — value passes through."""
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"x": {}},
        })
        assert tool.cast_params({"x": "hello"}) == {"x": "hello"}


# ===================================================================
# validate_params — all JSON schema types
# ===================================================================

class TestValidateParamsTypes:
    def _tool(self, prop_schema):
        return ConcreteTool(parameters={
            "type": "object",
            "properties": {"val": prop_schema},
        })

    def test_valid_string(self):
        errors = self._tool({"type": "string"}).validate_params({"val": "hello"})
        assert errors == []

    def test_invalid_string(self):
        errors = self._tool({"type": "string"}).validate_params({"val": 42})
        assert len(errors) == 1
        assert "should be string" in errors[0]

    def test_valid_integer(self):
        errors = self._tool({"type": "integer"}).validate_params({"val": 42})
        assert errors == []

    def test_invalid_integer_string(self):
        errors = self._tool({"type": "integer"}).validate_params({"val": "42"})
        assert len(errors) == 1
        assert "should be integer" in errors[0]

    def test_bool_is_not_integer(self):
        errors = self._tool({"type": "integer"}).validate_params({"val": True})
        assert len(errors) == 1
        assert "should be integer" in errors[0]

    def test_valid_number_int(self):
        errors = self._tool({"type": "number"}).validate_params({"val": 42})
        assert errors == []

    def test_valid_number_float(self):
        errors = self._tool({"type": "number"}).validate_params({"val": 3.14})
        assert errors == []

    def test_invalid_number_string(self):
        errors = self._tool({"type": "number"}).validate_params({"val": "3.14"})
        assert len(errors) == 1
        assert "should be number" in errors[0]

    def test_bool_is_not_number(self):
        errors = self._tool({"type": "number"}).validate_params({"val": True})
        assert len(errors) == 1
        assert "should be number" in errors[0]

    def test_valid_boolean(self):
        errors = self._tool({"type": "boolean"}).validate_params({"val": True})
        assert errors == []
        errors = self._tool({"type": "boolean"}).validate_params({"val": False})
        assert errors == []

    def test_invalid_boolean(self):
        errors = self._tool({"type": "boolean"}).validate_params({"val": 1})
        assert len(errors) == 1
        assert "should be boolean" in errors[0]

    def test_valid_array(self):
        errors = self._tool({"type": "array"}).validate_params({"val": [1, 2]})
        assert errors == []

    def test_invalid_array(self):
        errors = self._tool({"type": "array"}).validate_params({"val": "not_array"})
        assert len(errors) == 1
        assert "should be array" in errors[0]

    def test_valid_object(self):
        errors = self._tool({"type": "object"}).validate_params({"val": {"a": 1}})
        assert errors == []

    def test_invalid_object(self):
        errors = self._tool({"type": "object"}).validate_params({"val": "not_object"})
        assert len(errors) == 1
        assert "should be object" in errors[0]


# ===================================================================
# validate_params — constraints
# ===================================================================

class TestValidateParamsConstraints:
    def _tool(self, prop_schema):
        return ConcreteTool(parameters={
            "type": "object",
            "properties": {"val": prop_schema},
        })

    def test_enum_valid(self):
        errors = self._tool({"type": "string", "enum": ["a", "b"]}).validate_params({"val": "a"})
        assert errors == []

    def test_enum_invalid(self):
        errors = self._tool({"type": "string", "enum": ["a", "b"]}).validate_params({"val": "c"})
        assert len(errors) == 1
        assert "must be one of" in errors[0]

    def test_minimum_valid(self):
        errors = self._tool({"type": "integer", "minimum": 0}).validate_params({"val": 0})
        assert errors == []

    def test_minimum_invalid(self):
        errors = self._tool({"type": "integer", "minimum": 0}).validate_params({"val": -1})
        assert any("must be >=" in e for e in errors)

    def test_maximum_valid(self):
        errors = self._tool({"type": "integer", "maximum": 100}).validate_params({"val": 100})
        assert errors == []

    def test_maximum_invalid(self):
        errors = self._tool({"type": "integer", "maximum": 100}).validate_params({"val": 101})
        assert any("must be <=" in e for e in errors)

    def test_min_and_max_combined(self):
        schema = {"type": "number", "minimum": 0, "maximum": 10}
        errors = self._tool(schema).validate_params({"val": 11})
        assert any("must be <=" in e for e in errors)
        errors = self._tool(schema).validate_params({"val": -1})
        assert any("must be >=" in e for e in errors)

    def test_minLength_valid(self):
        errors = self._tool({"type": "string", "minLength": 3}).validate_params({"val": "abc"})
        assert errors == []

    def test_minLength_invalid(self):
        errors = self._tool({"type": "string", "minLength": 3}).validate_params({"val": "ab"})
        assert any("at least" in e and "chars" in e for e in errors)

    def test_maxLength_valid(self):
        errors = self._tool({"type": "string", "maxLength": 5}).validate_params({"val": "abc"})
        assert errors == []

    def test_maxLength_invalid(self):
        errors = self._tool({"type": "string", "maxLength": 2}).validate_params({"val": "abc"})
        assert any("at most" in e and "chars" in e for e in errors)

    def test_required_present(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        })
        errors = tool.validate_params({"name": "Alice"})
        assert errors == []

    def test_required_missing(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        })
        errors = tool.validate_params({})
        assert len(errors) == 1
        assert "missing required" in errors[0]
        assert "name" in errors[0]


# ===================================================================
# validate_params — nested objects and arrays
# ===================================================================

class TestValidateParamsNested:
    def test_nested_object_valid(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"timeout": {"type": "integer"}},
                },
            },
        })
        errors = tool.validate_params({"config": {"timeout": 30}})
        assert errors == []

    def test_nested_object_invalid_type(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"timeout": {"type": "integer"}},
                },
            },
        })
        errors = tool.validate_params({"config": {"timeout": "not_int"}})
        assert len(errors) == 1
        assert "config.timeout" in errors[0]

    def test_nested_required(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"timeout": {"type": "integer"}},
                    "required": ["timeout"],
                },
            },
        })
        errors = tool.validate_params({"config": {}})
        assert any("config.timeout" in e for e in errors)

    def test_array_items_valid(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "nums": {"type": "array", "items": {"type": "integer"}},
            },
        })
        errors = tool.validate_params({"nums": [1, 2, 3]})
        assert errors == []

    def test_array_items_invalid(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "nums": {"type": "array", "items": {"type": "integer"}},
            },
        })
        errors = tool.validate_params({"nums": [1, "two", 3]})
        assert len(errors) == 1
        assert "nums[1]" in errors[0]

    def test_array_multiple_errors(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "nums": {"type": "array", "items": {"type": "integer"}},
            },
        })
        errors = tool.validate_params({"nums": ["a", "b"]})
        assert len(errors) == 2
        assert "nums[0]" in errors[0]
        assert "nums[1]" in errors[1]


# ===================================================================
# validate_params — edge cases
# ===================================================================

class TestValidateParamsEdgeCases:
    def test_non_dict_params(self):
        tool = ConcreteTool(parameters={"type": "object", "properties": {}})
        errors = tool.validate_params("not_a_dict")
        assert len(errors) == 1
        assert "must be an object" in errors[0]
        assert "str" in errors[0]

    def test_list_params(self):
        tool = ConcreteTool(parameters={"type": "object", "properties": {}})
        errors = tool.validate_params([1, 2, 3])
        assert len(errors) == 1
        assert "must be an object" in errors[0]
        assert "list" in errors[0]

    def test_non_object_schema_raises_value_error(self):
        tool = ConcreteTool(parameters={"type": "string"})
        with pytest.raises(ValueError, match="Schema must be object type"):
            tool.validate_params({"val": "x"})

    def test_non_object_schema_array_raises(self):
        tool = ConcreteTool(parameters={"type": "array"})
        with pytest.raises(ValueError, match="Schema must be object type"):
            tool.validate_params({})

    def test_empty_schema_is_valid(self):
        """Empty schema defaults to object type — anything goes."""
        tool = ConcreteTool(parameters={})
        errors = tool.validate_params({"anything": "goes"})
        assert errors == []

    def test_none_parameters_is_valid(self):
        tool = ConcreteTool(parameters=None)
        errors = tool.validate_params({"anything": "goes"})
        assert errors == []

    def test_no_properties_schema_extra_params_ok(self):
        tool = ConcreteTool(parameters={"type": "object"})
        errors = tool.validate_params({"x": 1, "y": 2})
        assert errors == []

    def test_multiple_errors_accumulate(self):
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "boolean"},
            },
        })
        errors = tool.validate_params({"a": "x", "b": "y"})
        assert len(errors) == 2

    def test_top_level_required_with_path(self):
        """Required at top-level object reports clean field name."""
        tool = ConcreteTool(parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        })
        errors = tool.validate_params({})
        assert errors == ["missing required x"]


# ===================================================================
# to_schema
# ===================================================================

class TestToSchema:
    def test_basic_schema(self):
        tool = ConcreteTool(
            name="my_tool",
            description="Does stuff",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
        )
        result = tool.to_schema()
        assert result == {
            "type": "function",
            "function": {
                "name": "my_tool",
                "description": "Does stuff",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                },
            },
        }

    def test_schema_with_no_parameters(self):
        tool = ConcreteTool(name="noop", description="No params", parameters={})
        result = tool.to_schema()
        assert result["function"]["parameters"] == {}

    def test_schema_preserves_all_fields(self):
        params = {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        }
        tool = ConcreteTool(name="search", description="Search", parameters=params)
        result = tool.to_schema()
        assert result["function"]["parameters"]["required"] == ["q"]


# ===================================================================
# Abstract interface — cannot instantiate Tool directly
# ===================================================================

class TestAbstractInterface:
    def test_cannot_instantiate_tool_directly(self):
        with pytest.raises(TypeError):
            Tool()

    def test_subclass_must_implement_abstract_methods(self):
        class Incomplete(Tool):
            pass

        with pytest.raises(TypeError):
            Incomplete()
