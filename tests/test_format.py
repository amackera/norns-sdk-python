"""Tests for neutral <-> Anthropic format translation."""

from norns.client import _to_anthropic_messages, _to_anthropic_tools, _from_anthropic_response


def test_simple_user_message():
    msgs = _to_anthropic_messages([{"role": "user", "content": "Hello"}])
    assert msgs == [{"role": "user", "content": "Hello"}]


def test_simple_assistant_message():
    msgs = _to_anthropic_messages([{"role": "assistant", "content": "Hi there"}])
    assert msgs == [{"role": "assistant", "content": "Hi there"}]


def test_assistant_with_tool_calls():
    msgs = _to_anthropic_messages([{
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "tc_1", "name": "search", "arguments": {"query": "weather"}},
        ],
    }])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    blocks = msgs[0]["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["id"] == "tc_1"
    assert blocks[0]["name"] == "search"
    assert blocks[0]["input"] == {"query": "weather"}


def test_assistant_with_text_and_tool_calls():
    msgs = _to_anthropic_messages([{
        "role": "assistant",
        "content": "Let me search for that.",
        "tool_calls": [
            {"id": "tc_1", "name": "search", "arguments": {"query": "weather"}},
        ],
    }])
    blocks = msgs[0]["content"]
    assert len(blocks) == 2
    assert blocks[0] == {"type": "text", "text": "Let me search for that."}
    assert blocks[1]["type"] == "tool_use"


def test_tool_result_message():
    msgs = _to_anthropic_messages([{
        "role": "tool",
        "tool_call_id": "tc_1",
        "name": "search",
        "content": "22°C, sunny",
    }])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    blocks = msgs[0]["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "tc_1"
    assert blocks[0]["content"] == "22°C, sunny"


def test_consecutive_tool_results_merged():
    msgs = _to_anthropic_messages([
        {"role": "tool", "tool_call_id": "tc_1", "name": "a", "content": "result1"},
        {"role": "tool", "tool_call_id": "tc_2", "name": "b", "content": "result2"},
    ])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    blocks = msgs[0]["content"]
    assert len(blocks) == 2


def test_tool_result_with_error():
    msgs = _to_anthropic_messages([{
        "role": "tool",
        "tool_call_id": "tc_1",
        "name": "search",
        "content": "not found",
        "is_error": True,
    }])
    block = msgs[0]["content"][0]
    assert block["is_error"] is True


def test_full_conversation():
    neutral = [
        {"role": "user", "content": "What's the weather?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "tc_1", "name": "get_weather", "arguments": {"city": "Toronto"}},
        ]},
        {"role": "tool", "tool_call_id": "tc_1", "name": "get_weather", "content": "22°C"},
        {"role": "assistant", "content": "It's 22°C in Toronto."},
    ]
    result = _to_anthropic_messages(neutral)
    assert len(result) == 4
    assert result[0] == {"role": "user", "content": "What's the weather?"}
    assert result[1]["role"] == "assistant"
    assert result[1]["content"][0]["type"] == "tool_use"
    assert result[2]["role"] == "user"
    assert result[2]["content"][0]["type"] == "tool_result"
    assert result[3] == {"role": "assistant", "content": "It's 22°C in Toronto."}


def test_to_anthropic_tools():
    tools = _to_anthropic_tools([{
        "name": "search",
        "description": "Search the web",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }])
    assert len(tools) == 1
    assert tools[0]["name"] == "search"
    assert tools[0]["input_schema"] == {"type": "object", "properties": {"q": {"type": "string"}}}
    assert "parameters" not in tools[0]


class FakeBlock:
    def __init__(self, type, **kwargs):
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, content, stop_reason, usage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


def test_from_anthropic_text_response():
    resp = FakeResponse(
        content=[FakeBlock("text", text="Hello!")],
        stop_reason="end_turn",
        usage=FakeUsage(100, 20),
    )
    result = _from_anthropic_response(resp)
    assert result["status"] == "ok"
    assert result["content"] == "Hello!"
    assert result["finish_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert "tool_calls" not in result


def test_from_anthropic_tool_call_response():
    resp = FakeResponse(
        content=[
            FakeBlock("text", text="Let me check."),
            FakeBlock("tool_use", id="tc_1", name="search", input={"q": "weather"}),
        ],
        stop_reason="tool_use",
        usage=FakeUsage(150, 30),
    )
    result = _from_anthropic_response(resp)
    assert result["content"] == "Let me check."
    assert result["finish_reason"] == "tool_call"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0] == {
        "id": "tc_1",
        "name": "search",
        "arguments": {"q": "weather"},
    }


def test_from_anthropic_max_tokens():
    resp = FakeResponse(
        content=[FakeBlock("text", text="Truncated...")],
        stop_reason="max_tokens",
        usage=FakeUsage(100, 4096),
    )
    result = _from_anthropic_response(resp)
    assert result["finish_reason"] == "length"
