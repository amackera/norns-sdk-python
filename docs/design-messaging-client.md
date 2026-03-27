# Messaging Client Design

Add client-side messaging to the SDK so Python apps (e.g., Slack bots, web backends) can send messages to Norns agents and receive responses.

## Context

The SDK currently only supports the **worker** path: connect to `/worker/websocket`, register agents/tools, handle dispatched tasks. There's no way to send messages to agents or receive their responses.

Norns exposes two client-facing interfaces:

- **REST API** — `POST /api/v1/agents/:id/messages` (returns 202), `GET /api/v1/runs/:id`, `GET /api/v1/runs/:id/events`
- **WebSocket** — connect to `/socket`, join `agent:<id>` channel, push `send_message`, receive streaming events (`llm_response`, `tool_call`, `tool_result`, `completed`, `error`, `waiting`)

Clients and workers are completely separate. A Slack webhook handler would be a client, independent from any worker process.

## Proposed API

### REST (sync)

```python
norns = Norns("http://localhost:4000", api_key="nrn_...")

# Send a message — returns immediately with run info
run = norns.send_message("agent-id", "Hello!", conversation_key="slack-123")
# run.id, run.status == "accepted"

# Poll for result
result = norns.get_run(run.id)
# result.status: "running" | "completed" | "error"
# result.output (when completed)

# Get full event log
events = norns.get_run_events(run.id)
```

### WebSocket streaming (async)

```python
async with norns.stream("agent-id") as agent:
    await agent.send_message("Hello!", conversation_key="slack-123")
    async for event in agent:
        # event.type: "llm_response" | "tool_call" | "tool_result" | "completed" | "error" | "waiting"
        if event.type == "completed":
            print(event.content)
            break
```

## Design Decisions

### 1. Same `Norns` class

Both worker and client methods live on `Norns`. It already holds URL and API key. REST methods (`send_message`, `get_run`, `get_run_events`) are sync. `stream()` returns an async context manager for the WebSocket path.

### 2. Conversation lifecycle

Implicit — matches Norns behavior. Pass an optional `conversation_key` (defaults to `"default"`). Norns auto-creates conversations as needed.

### 3. HTTP client

Use `httpx` for REST calls. Lightweight, modern, well-maintained. Adds one dependency but avoids the ergonomic pain of `urllib`.

### 4. Data models

Simple dataclasses for `Run` and `Event`:

```python
@dataclass
class Run:
    id: str
    status: str  # "accepted" | "running" | "completed" | "error"
    output: str | None = None

@dataclass
class Event:
    type: str  # "llm_response" | "tool_call" | "tool_result" | "completed" | "error" | "waiting"
    data: dict
```

### 5. WebSocket streaming

The `stream()` context manager connects to `/socket`, joins the `agent:<id>` Phoenix channel, and returns an `AgentStream` object with `send_message()` and `async for` iteration over events.

## Implementation Plan

1. Add `httpx` to dependencies in `pyproject.toml`
2. Create `norns/models.py` — `Run` and `Event` dataclasses
3. Add REST methods to `Norns` in `norns/client.py`:
   - `send_message(agent_id, content, *, conversation_key="default") -> Run`
   - `get_run(run_id) -> Run`
   - `get_run_events(run_id) -> list[Event]`
4. Create `norns/stream.py` — `AgentStream` class (async context manager + async iterator)
5. Add `stream(agent_id) -> AgentStream` method to `Norns`
6. Export `Run`, `Event` from `norns/__init__.py`
7. Tests for REST methods (mock HTTP), stream protocol (mock WebSocket)
