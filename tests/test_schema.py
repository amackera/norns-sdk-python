"""Tests for schema inference edge cases."""

from norns.agent import _infer_schema


def test_no_type_hints():
    def fn(x, y):
        pass

    schema = _infer_schema(fn)
    assert schema["properties"]["x"]["type"] == "string"  # default
    assert schema["properties"]["y"]["type"] == "string"
    assert set(schema["required"]) == {"x", "y"}


def test_empty_function():
    def fn():
        pass

    schema = _infer_schema(fn)
    assert schema["properties"] == {}
    assert schema["required"] == []


def test_list_type():
    def fn(items: list) -> str:
        pass

    schema = _infer_schema(fn)
    assert schema["properties"]["items"]["type"] == "array"


def test_dict_type():
    def fn(data: dict) -> str:
        pass

    schema = _infer_schema(fn)
    assert schema["properties"]["data"]["type"] == "object"
