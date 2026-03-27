# norns-sdk

Python SDK for [Norns](https://github.com/amackera/norns) — durable agent runtime on BEAM.

Define agents and tools in Python, connect to Norns as a worker or interact with agents as a client. Norns handles orchestration, durability, and crash recovery. Your code handles LLM calls, tool execution, and application logic.

## Install

```bash
pip install norns-sdk
```

The SDK has two main classes:

- **`Norns`** — the worker. Connects via WebSocket, registers agents/tools, handles LLM and tool tasks. Blocks forever.
- **`NornsClient`** — the client. Sends messages to agents, queries run status, streams events. Used by web servers, Slack bots, CLI tools, etc.

## Quick Start — Worker

```python
import os
from norns import Norns, Agent, tool

# Connect to the Norns runtime
norns = Norns("http://localhost:4000", api_key=os.environ["NORNS_API_KEY"])

# Define tools
@tool
def search_docs(query: str) -> str:
    """Search product documentation."""
    return db.vector_search(query)

@tool(side_effect=True)
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a customer."""
    smtp.send(to=to, subject=subject, body=body)
    return f"Email sent to {to}"

# Define an agent
agent = Agent(
    name="support-bot",
    model="claude-sonnet-4-20250514",
    system_prompt="You are a customer support agent. Look up docs and help customers.",
    tools=[search_docs, send_email],
    mode="conversation",
    on_failure="retry_last_step",
)

# Run as a worker (blocks forever, like Temporal)
norns.run(agent, llm_api_key=os.environ["ANTHROPIC_API_KEY"])
```

This:
1. Connects to Norns via WebSocket
2. Registers the agent definition + tools
3. Handles `llm_task` dispatches (calls Anthropic with your API key)
4. Handles `tool_task` dispatches (calls your tool functions)
5. Reconnects automatically on disconnect

## How It Works

```
Norns Orchestrator                    Your Python Worker
  │  (pure state machine)                │  (this SDK)
  │                                      │
  │  dispatches llm_task ──────────────► │  calls Anthropic API
  │  ◄── llm_response ─────────────────  │
  │  dispatches tool_task ─────────────► │  calls search_docs()
  │  ◄── tool_result ──────────────────  │
  │  logs events, checkpoints            │
```

Norns never sees your API keys or data. Your worker makes all external calls.

## Defining Tools

The `@tool` decorator infers JSON Schema from type hints:

```python
@tool
def lookup_customer(email: str) -> str:
    """Look up a customer by email."""
    customer = db.query("SELECT * FROM customers WHERE email = ?", email)
    return f"Found: {customer['name']} ({customer['plan']})"
```

Mark side-effecting tools so Norns can enforce idempotency:

```python
@tool(side_effect=True)
def charge_card(customer_id: str, amount: float) -> str:
    """Charge a credit card."""
    result = stripe.charges.create(customer=customer_id, amount=int(amount * 100))
    return f"Charged ${amount}: {result['id']}"
```

## Agent Configuration

```python
agent = Agent(
    name="my-agent",
    model="claude-sonnet-4-20250514",     # LLM model
    system_prompt="You are helpful.",      # System prompt
    tools=[search, send_email],           # Tools the agent can call
    mode="conversation",                   # "task" or "conversation"
    checkpoint_policy="on_tool_call",      # "every_step", "on_tool_call", "manual"
    context_window=20,                     # Messages to keep in conversation mode
    max_steps=50,                          # Safety limit
    on_failure="retry_last_step",          # "stop" or "retry_last_step"
)
```

## Quick Start — Client

```python
import os
from norns import NornsClient

client = NornsClient("http://localhost:4000", api_key=os.environ["NORNS_API_KEY"])

# Fire-and-forget — returns immediately with run info
run = client.send_message("support-bot", "Where's my order?")
print(run.run_id)   # 42
print(run.status)   # "accepted"

# Block until completion
result = client.send_message("support-bot", "Where's my order?", wait=True, timeout=30)
print(result.output)  # "Your order #1234 shipped on..."
print(result.status)  # "completed"

# With conversation key (for multi-turn)
result = client.send_message("support-bot", "And the tracking number?",
                             conversation_key="slack:U01ABC", wait=True)

# Inspect runs and events
run = client.get_run(42)
events = client.get_events(42)

# Stream events in real time
for event in client.stream("support-bot", "Research quantum computing"):
    if event.type == "completed":
        print(event.data.get("output", "")[:80])
        break
```

## Async Tools

Both sync and async tool handlers are supported:

```python
@tool
async def fetch_page(url: str) -> str:
    """Fetch a web page."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()
```

## License

MIT
