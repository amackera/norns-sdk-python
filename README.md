# norns-sdk

[![CI](https://github.com/amackera/norns-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/amackera/norns-sdk-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/norns-sdk)](https://pypi.org/project/norns-sdk/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Python SDK for [Norns](https://github.com/amackera/norns) — durable agent runtime on BEAM.

Two classes, two roles:

- **`Norns`** — worker. Registers agents and tools, handles dispatched LLM/tool tasks. Blocks forever, like a Temporal worker.
- **`NornsClient`** — client. Sends messages, polls for results, streams events. For your Slack bot, web backend, CLI, etc.

```bash
pip install norns-sdk
```

## Worker

```python
import os
from norns import Norns, Agent, tool

@tool
def search_docs(query: str) -> str:
    """Search product documentation."""
    return db.vector_search(query)

@tool(side_effect=True)
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a customer."""
    smtp.send(to=to, subject=subject, body=body)
    return f"Email sent to {to}"

agent = Agent(
    name="support-bot",
    model="claude-sonnet-4-20250514",
    system_prompt="You are a customer support agent. Look up docs and help customers.",
    tools=[search_docs, send_email],
    mode="conversation",
    on_failure="retry_last_step",
)

norns = Norns("http://localhost:4000", api_key=os.environ["NORNS_API_KEY"])
norns.run(agent)  # LLM API keys read from env (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
```

This connects to Norns via WebSocket, registers the agent, and sits in a loop handling `llm_task` and `tool_task` dispatches. LLM calls are routed through [LiteLLM](https://github.com/BerriAI/litellm), so any supported provider works. Norns never sees your API keys — your worker makes all external calls.

```
Norns Orchestrator                    Your Python Worker
  │  (pure state machine)                │  (this SDK)
  │                                      │
  │  dispatches llm_task ──────────────► │  calls LLM (via LiteLLM)
  │  ◄── llm_response ─────────────────  │
  │  dispatches tool_task ─────────────► │  calls search_docs()
  │  ◄── tool_result ──────────────────  │
  │  logs events, checkpoints            │
```

## Client

```python
import os
from norns import NornsClient

client = NornsClient("http://localhost:4000", api_key=os.environ["NORNS_API_KEY"])

# Fire-and-forget
run = client.send_message("support-bot", "Where's my order?")
# run.run_id, run.status == "accepted"

# Wait for completion
result = client.send_message("support-bot", "Where's my order?", wait=True, timeout=30)
print(result.output)

# Multi-turn with a conversation key
result = client.send_message("support-bot", "And the tracking number?",
                             conversation_key="slack:U01ABC", wait=True)

# Inspect a run
run = client.get_run(42)
events = client.get_events(42)

# Stream events as they happen
for event in client.stream("support-bot", "Research quantum computing"):
    if event.type == "completed":
        print(event.data.get("output", "")[:80])
        break
```

## Tools

The `@tool` decorator infers JSON Schema from type hints. The docstring becomes the tool description the LLM sees.

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

Async handlers work too:

```python
@tool
async def fetch_page(url: str) -> str:
    """Fetch a web page."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()
```

## Agent options

```python
agent = Agent(
    name="my-agent",
    model="claude-sonnet-4-20250514",
    system_prompt="You are helpful.",
    tools=[search, send_email],
    mode="conversation",             # "task" or "conversation"
    checkpoint_policy="on_tool_call",  # "every_step", "on_tool_call", "manual"
    context_window=20,
    max_steps=50,
    on_failure="retry_last_step",    # "stop" or "retry_last_step"
)
```

## Docs

- [Messaging client design](docs/messaging-client-design.md)
- [Remaining work](docs/remaining-work.md)

## License

MIT
