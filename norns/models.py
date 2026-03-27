"""Response models for the Norns client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunResponse:
    run_id: int
    status: str
    output: str | None
    agent_id: int
    conversation_id: int | None
    trigger_type: str
    inserted_at: str


@dataclass
class EventResponse:
    id: int
    sequence: int
    event_type: str
    payload: dict
    source: str
    inserted_at: str


@dataclass
class AgentResponse:
    id: int
    name: str
    status: str
    model: str
    mode: str
    system_prompt: str
    max_steps: int


@dataclass
class ConversationResponse:
    id: int
    agent_id: int
    key: str
    message_count: int
    token_estimate: int


@dataclass
class MessageResult:
    run_id: int
    status: str
    output: str | None
    conversation_key: str | None


@dataclass
class StreamEvent:
    type: str
    data: dict
