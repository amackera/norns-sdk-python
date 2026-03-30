"""Tests for neutral <-> LiteLLM format translation."""

import json
from unittest.mock import MagicMock

from norns.client import _to_litellm_tools, _from_litellm_response


def test_to_litellm_tools():
    tools = _to_litellm_tools([{
        "name": "search",
        "description": "Search the web",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }])
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "search"
    assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {"q": {"type": "string"}}}


def test_to_litellm_tools_multiple():
    tools = _to_litellm_tools([
        {"name": "a", "description": "Tool A", "parameters": {}},
        {"name": "b", "description": "Tool B", "parameters": {}},
    ])
    assert len(tools) == 2
    assert tools[0]["function"]["name"] == "a"
    assert tools[1]["function"]["name"] == "b"


def _make_response(content="Hello!", finish_reason="stop", tool_calls=None,
                   prompt_tokens=100, completion_tokens=20):
    """Build a mock LiteLLM response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def test_from_litellm_text_response():
    resp = _make_response(content="Hello!", finish_reason="stop")
    result = _from_litellm_response(resp)
    assert result["status"] == "ok"
    assert result["content"] == "Hello!"
    assert result["finish_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert "tool_calls" not in result


def test_from_litellm_tool_call_response():
    tc = MagicMock()
    tc.id = "tc_1"
    tc.function.name = "search"
    tc.function.arguments = json.dumps({"q": "weather"})

    resp = _make_response(content="Let me check.", finish_reason="tool_calls", tool_calls=[tc])
    result = _from_litellm_response(resp)
    assert result["content"] == "Let me check."
    assert result["finish_reason"] == "tool_call"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0] == {
        "id": "tc_1",
        "name": "search",
        "arguments": {"q": "weather"},
    }


def test_from_litellm_tool_call_dict_arguments():
    """LiteLLM sometimes returns arguments as dict instead of JSON string."""
    tc = MagicMock()
    tc.id = "tc_1"
    tc.function.name = "search"
    tc.function.arguments = {"q": "weather"}

    resp = _make_response(content="", finish_reason="tool_calls", tool_calls=[tc])
    result = _from_litellm_response(resp)
    assert result["tool_calls"][0]["arguments"] == {"q": "weather"}


def test_from_litellm_length_finish():
    resp = _make_response(content="Truncated...", finish_reason="length")
    result = _from_litellm_response(resp)
    assert result["finish_reason"] == "length"


def test_from_litellm_none_content():
    resp = _make_response(content=None, finish_reason="stop")
    result = _from_litellm_response(resp)
    assert result["content"] == ""


def test_from_litellm_multiple_tool_calls():
    tc1 = MagicMock()
    tc1.id = "tc_1"
    tc1.function.name = "search"
    tc1.function.arguments = json.dumps({"q": "a"})

    tc2 = MagicMock()
    tc2.id = "tc_2"
    tc2.function.name = "lookup"
    tc2.function.arguments = json.dumps({"id": "123"})

    resp = _make_response(content="", finish_reason="tool_calls", tool_calls=[tc1, tc2])
    result = _from_litellm_response(resp)
    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][0]["name"] == "search"
    assert result["tool_calls"][1]["name"] == "lookup"
