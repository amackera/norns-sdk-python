"""Norns client — worker and client for the Norns durable agent runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Generator
from typing import Any

import anthropic
import httpx
import websockets

from norns.agent import Agent, ToolDef
from norns.models import (
    AgentResponse,
    ConversationResponse,
    EventResponse,
    MessageResult,
    RunResponse,
    StreamEvent,
)

logger = logging.getLogger("norns")


class Norns:
    """Client for the Norns durable agent runtime.

    Usage:
        norns = Norns("http://localhost:4000", api_key="nrn_...")

        @tool
        def search(query: str) -> str:
            ...

        agent = Agent(name="bot", tools=[search], ...)
        norns.run(agent, llm_api_key="sk-ant-...")
    """

    def __init__(self, url: str, *, api_key: str | None = None):
        self.url = url.rstrip("/")
        self.api_key = api_key or os.environ.get("NORNS_API_KEY", "")
        self._ws_url = self.url.replace("http://", "ws://").replace("https://", "wss://")

    def run(self, agent: Agent, *, llm_api_key: str | None = None, worker_id: str | None = None):
        """Connect as a worker, register the agent, and handle tasks forever.

        Auto-creates the agent via REST if it doesn't exist yet.
        This blocks — like a Temporal worker.
        """
        self._ensure_agent(agent)

        llm_key = llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        wid = worker_id or f"python-worker-{uuid.uuid4().hex[:8]}"

        asyncio.run(self._run_loop(agent, llm_key, wid))

    def _ensure_agent(self, agent: Agent):
        """Create the agent via REST API if it doesn't already exist."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(base_url=self.url, headers=headers) as client:
            resp = client.get("/api/v1/agents")
            resp.raise_for_status()
            existing = resp.json().get("data", [])

            for a in existing:
                if a["name"] == agent.name:
                    logger.info(f"Agent '{agent.name}' already exists (id={a['id']})")
                    return

            body = {
                "name": agent.name,
                "system_prompt": agent.system_prompt,
                "status": "idle",
                "model": agent.model,
                "max_steps": agent.max_steps,
                "model_config": {
                    "mode": agent.mode,
                    "checkpoint_policy": agent.checkpoint_policy,
                    "context_strategy": agent.context_strategy,
                    "context_window": agent.context_window,
                    "on_failure": agent.on_failure,
                },
            }
            resp = client.post("/api/v1/agents", json=body)
            resp.raise_for_status()
            created = resp.json()["data"]
            logger.info(f"Created agent '{agent.name}' (id={created['id']})")

    async def _run_loop(self, agent: Agent, llm_api_key: str, worker_id: str):
        """Main event loop: connect, register, handle tasks, reconnect on failure."""
        tools_by_name = {t.name: t for t in agent.tools}

        while True:
            try:
                await self._connect_and_serve(agent, llm_api_key, worker_id, tools_by_name)
            except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
                logger.warning(f"Connection lost: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Unexpected error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _connect_and_serve(
        self,
        agent: Agent,
        llm_api_key: str,
        worker_id: str,
        tools_by_name: dict[str, ToolDef],
    ):
        """Single connection lifecycle: connect, join, handle messages."""
        ws_url = f"{self._ws_url}/worker/websocket?token={self.api_key}&vsn=2.0.0"

        async with websockets.connect(ws_url) as ws:
            logger.info(f"Connected to {self.url}")

            # Phoenix channel join
            join_payload = {
                "worker_id": worker_id,
                "tools": [t.to_registration() for t in agent.tools],
                "capabilities": ["llm", "tools"],
                "agents": [agent.to_registration()],
            }

            join_msg = json.dumps([None, "1", "worker:lobby", "phx_join", join_payload])
            await ws.send(join_msg)

            response = await ws.recv()
            resp_data = json.loads(response)
            logger.info(f"Joined worker:lobby as {worker_id}")

            # Create Anthropic client
            llm_client = anthropic.Anthropic(api_key=llm_api_key) if llm_api_key else None

            # Heartbeat task
            heartbeat_task = asyncio.create_task(self._heartbeat(ws))

            try:
                async for raw_msg in ws:
                    msg = json.loads(raw_msg)
                    # Phoenix message format: [join_ref, ref, topic, event, payload]
                    if not isinstance(msg, list) or len(msg) < 5:
                        continue

                    _join_ref, _ref, _topic, event, payload = msg

                    if event == "llm_task":
                        result = await self._handle_llm_task(payload, llm_client)
                        await self._send_result(ws, payload, result)

                    elif event == "tool_task":
                        result = await self._handle_tool_task(payload, tools_by_name)
                        await self._send_result(ws, payload, result)

                    elif event == "phx_error":
                        logger.error(f"Channel error: {payload}")
                        break

                    elif event == "phx_close":
                        logger.info("Channel closed by server")
                        break

            finally:
                heartbeat_task.cancel()

    async def _handle_llm_task(self, task: dict, client: anthropic.Anthropic | None) -> dict:
        """Execute an LLM call via the Anthropic API."""
        if client is None:
            return {"status": "error", "error": "No LLM API key configured"}

        try:
            model = task.get("model", "claude-sonnet-4-20250514")
            system_prompt = task.get("system_prompt", "")
            messages = task.get("messages", [])
            tools = task.get("opts", {}).get("tools") if isinstance(task.get("opts"), dict) else None

            # Handle opts as list (Elixir keyword list serialized)
            if isinstance(task.get("opts"), list):
                for item in task["opts"]:
                    if isinstance(item, dict) and "tools" in item:
                        tools = item["tools"]

            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            response = client.messages.create(**kwargs)

            content = [_content_block_to_dict(block) for block in response.content]

            return {
                "status": "ok",
                "content": content,
                "stop_reason": response.stop_reason,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }

        except anthropic.RateLimitError as e:
            logger.warning(f"Rate limited, returning error to orchestrator: {e}")
            return {"status": "error", "error": {"type": "rate_limit_error", "message": str(e)}}

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return {"status": "error", "error": str(e)}

    async def _handle_tool_task(self, task: dict, tools: dict[str, ToolDef]) -> dict:
        """Execute a tool call."""
        tool_name = task.get("tool_name", "")
        input_data = task.get("input", {})

        tool = tools.get(tool_name)
        if tool is None:
            return {"status": "error", "error": f"Unknown tool: {tool_name}"}

        try:
            # Run the handler — support both sync and async
            if asyncio.iscoroutinefunction(tool.handler):
                result = await tool.handler(**input_data)
            else:
                result = await asyncio.to_thread(tool.handler, **input_data)

            return {"status": "ok", "result": str(result)}

        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"status": "error", "error": str(e)}

    async def _send_result(self, ws, task: dict, result: dict):
        """Send a task result back to the orchestrator."""
        task_id = task.get("task_id", "")
        result["task_id"] = task_id

        msg = json.dumps([None, None, "worker:lobby", "tool_result", result])
        await ws.send(msg)

    async def _heartbeat(self, ws):
        """Send Phoenix heartbeat to keep the connection alive."""
        ref = 100
        while True:
            await asyncio.sleep(30)
            ref += 1
            msg = json.dumps([None, str(ref), "phoenix", "heartbeat", {}])
            try:
                await ws.send(msg)
            except Exception:
                break


class NornsClient:
    """Client for interacting with Norns agents.

    This is the client — it sends messages and queries results.
    For running a worker, use the Norns class instead.

    Usage:
        client = NornsClient("http://localhost:4000", api_key="nrn_...")
        run = client.send_message("support-bot", "Hello!")
        result = client.send_message("support-bot", "Hello!", wait=True)
    """

    def __init__(self, url: str, *, api_key: str | None = None):
        self.base_url = url.rstrip("/")
        self.api_key = api_key or os.environ.get("NORNS_API_KEY", "")
        self._ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    def close(self):
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated HTTP request."""
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    # --- Agent Management ---

    def list_agents(self) -> list[AgentResponse]:
        """List all agents."""
        resp = self._request("GET", "/api/v1/agents")
        return [_parse_agent(a) for a in resp.json()["data"]]

    def get_agent(self, id_or_name: int | str) -> AgentResponse:
        """Get an agent by ID or name.

        If a string is passed, resolves by listing agents and filtering by name.
        """
        if isinstance(id_or_name, int):
            resp = self._request("GET", f"/api/v1/agents/{id_or_name}")
            return _parse_agent(resp.json()["data"])

        agents = self.list_agents()
        for agent in agents:
            if agent.name == id_or_name:
                return agent
        raise ValueError(f"Agent not found: {id_or_name}")

    def _resolve_agent_id(self, agent: int | str) -> int:
        """Resolve an agent identifier to an integer ID."""
        if isinstance(agent, int):
            return agent
        return self.get_agent(agent).id

    # --- Sending Messages ---

    def send_message(
        self,
        agent: int | str,
        content: str,
        *,
        conversation_key: str | None = None,
        wait: bool = False,
        timeout: float = 30,
    ) -> MessageResult:
        """Send a message to an agent.

        Args:
            agent: Agent ID (int) or name (str).
            content: The message content.
            conversation_key: Optional key for multi-turn conversations.
            wait: If True, poll until the run completes or times out.
            timeout: Seconds to wait when wait=True.

        Returns:
            MessageResult with run_id, status, and output (if wait=True and completed).
        """
        agent_id = self._resolve_agent_id(agent)
        body: dict[str, Any] = {"content": content}
        if conversation_key is not None:
            body["conversation_key"] = conversation_key

        resp = self._request("POST", f"/api/v1/agents/{agent_id}/messages", json=body)
        data = resp.json()
        run_id = data["run_id"]
        status = data.get("status", "accepted")

        if not wait:
            return MessageResult(
                run_id=run_id,
                status=status,
                output=None,
                conversation_key=conversation_key,
            )

        # Poll until completion or timeout
        deadline = time.monotonic() + timeout
        poll_interval = 0.5
        while time.monotonic() < deadline:
            run = self.get_run(run_id)
            if run.status in ("completed", "failed", "error"):
                return MessageResult(
                    run_id=run_id,
                    status=run.status,
                    output=run.output,
                    conversation_key=conversation_key,
                )
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 3.0)

        raise TimeoutError(f"Run {run_id} did not complete within {timeout}s")

    # --- Run Inspection ---

    def get_run(self, run_id: int) -> RunResponse:
        """Get details of a run."""
        resp = self._request("GET", f"/api/v1/runs/{run_id}")
        data = resp.json()["data"]
        return RunResponse(
            run_id=data["id"],
            status=data["status"],
            output=data.get("output"),
            agent_id=data["agent_id"],
            conversation_id=data.get("conversation_id"),
            trigger_type=data.get("trigger_type", "message"),
            inserted_at=data["inserted_at"],
        )

    def get_events(self, run_id: int) -> list[EventResponse]:
        """Get the event log for a run."""
        resp = self._request("GET", f"/api/v1/runs/{run_id}/events")
        return [
            EventResponse(
                id=e["id"],
                sequence=e["sequence"],
                event_type=e["event_type"],
                payload=e.get("payload", {}),
                source=e.get("source", ""),
                inserted_at=e["inserted_at"],
            )
            for e in resp.json()["data"]
        ]

    # --- Conversations ---

    def list_conversations(self, agent: int | str) -> list[ConversationResponse]:
        """List conversations for an agent."""
        agent_id = self._resolve_agent_id(agent)
        resp = self._request("GET", f"/api/v1/agents/{agent_id}/conversations")
        return [
            ConversationResponse(
                id=c["id"],
                agent_id=c["agent_id"],
                key=c["key"],
                message_count=c.get("message_count", 0),
                token_estimate=c.get("token_estimate", 0),
            )
            for c in resp.json()["data"]
        ]

    def get_conversation(self, agent: int | str, key: str) -> ConversationResponse:
        """Get a specific conversation by key."""
        agent_id = self._resolve_agent_id(agent)
        resp = self._request("GET", f"/api/v1/agents/{agent_id}/conversations/{key}")
        c = resp.json()["data"]
        return ConversationResponse(
            id=c["id"],
            agent_id=c["agent_id"],
            key=c["key"],
            message_count=c.get("message_count", 0),
            token_estimate=c.get("token_estimate", 0),
        )

    def delete_conversation(self, agent: int | str, key: str) -> None:
        """Delete a conversation (reset)."""
        agent_id = self._resolve_agent_id(agent)
        self._request("DELETE", f"/api/v1/agents/{agent_id}/conversations/{key}")

    # --- Streaming ---

    def stream(
        self,
        agent: int | str,
        content: str,
        *,
        conversation_key: str | None = None,
        timeout: float = 120,
    ) -> Generator[StreamEvent, None, None]:
        """Send a message and stream events as they happen.

        Yields StreamEvent objects until the run completes or errors.
        """
        agent_id = self._resolve_agent_id(agent)

        # Send the message first (fire-and-forget)
        body: dict[str, Any] = {"content": content}
        if conversation_key is not None:
            body["conversation_key"] = conversation_key
        resp = self._request("POST", f"/api/v1/agents/{agent_id}/messages", json=body)
        run_id = resp.json()["run_id"]

        # Stream via WebSocket
        yield from _stream_events(self._ws_url, self.api_key, agent_id, run_id, timeout)


def _stream_events(
    ws_url: str,
    api_key: str,
    agent_id: int,
    run_id: int,
    timeout: float,
) -> Generator[StreamEvent, None, None]:
    """Connect to Phoenix WebSocket and yield events for a run."""
    url = f"{ws_url}/socket/websocket?token={api_key}&vsn=2.0.0"
    topic = f"agent:{agent_id}"
    ref_counter = 0

    def next_ref() -> str:
        nonlocal ref_counter
        ref_counter += 1
        return str(ref_counter)

    with websockets.sync.client.connect(url) as ws:
        ws.settimeout(timeout)

        # Join the agent channel
        join_ref = next_ref()
        join_msg = json.dumps([join_ref, next_ref(), topic, "phx_join", {"run_id": run_id}])
        ws.send(join_msg)

        # Wait for join reply
        reply = json.loads(ws.recv())
        if isinstance(reply, list) and len(reply) >= 5 and reply[3] == "phx_reply":
            status = reply[4].get("status")
            if status != "ok":
                raise ConnectionError(f"Failed to join channel {topic}: {reply[4]}")

        # Read events
        while True:
            try:
                raw = ws.recv()
            except TimeoutError:
                raise TimeoutError(f"Stream timed out after {timeout}s")

            msg = json.loads(raw)
            if not isinstance(msg, list) or len(msg) < 5:
                continue

            _join_ref, _ref, _topic, event, payload = msg

            if event in ("phx_reply", "phx_close", "phx_error", "heartbeat"):
                if event == "phx_error":
                    yield StreamEvent(type="error", data=payload)
                    return
                if event == "phx_close":
                    return
                continue

            stream_event = StreamEvent(type=event, data=payload)
            yield stream_event

            if event in ("completed", "error"):
                return


def _parse_agent(data: dict) -> AgentResponse:
    """Parse an agent dict from the API into an AgentResponse."""
    return AgentResponse(
        id=data["id"],
        name=data["name"],
        status=data.get("status", "active"),
        model=data.get("model", ""),
        mode=data.get("mode", "task"),
        system_prompt=data.get("system_prompt", ""),
        max_steps=data.get("max_steps", 50),
    )


def _content_block_to_dict(block) -> dict:
    """Convert an Anthropic content block to a plain dict."""
    if hasattr(block, "type"):
        if block.type == "text":
            return {"type": "text", "text": block.text}
        elif block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
    return {"type": "unknown"}
