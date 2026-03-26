"""Norns client — connects to the runtime as a worker."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import anthropic
import websockets

from norns.agent import Agent, ToolDef

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

        This blocks — like a Temporal worker.
        """
        llm_key = llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        wid = worker_id or f"python-worker-{uuid.uuid4().hex[:8]}"

        asyncio.run(self._run_loop(agent, llm_key, wid))

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
