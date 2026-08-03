"""Response models for the Norns client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WaitingFor:
    """The question a run is parked on, when its status is "waiting".

    Answer it with ``client.reply()``, or by sending the agent a normal
    message — a message to a parked agent is treated as the answer.
    """

    question: str
    tool_call_id: str
    asked_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> WaitingFor | None:
        """Build from a run payload's ``waiting_for`` object. ``None`` passes through."""
        if not data:
            return None
        return cls(
            question=data["question"],
            tool_call_id=data["tool_call_id"],
            asked_at=data.get("asked_at"),
        )


@dataclass
class RunResponse:
    run_id: int
    status: str
    output: str | None
    agent_id: int
    conversation_id: int | None
    trigger_type: str
    inserted_at: str
    waiting_for: WaitingFor | None = None

    @property
    def is_waiting(self) -> bool:
        """Whether the run is parked on an ``ask_human`` question."""
        return self.status == "waiting"


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
    waiting_for: WaitingFor | None = None

    @property
    def is_waiting(self) -> bool:
        """Whether the agent is parked on a question rather than finished."""
        return self.status == "waiting"


@dataclass
class StreamEvent:
    type: str
    data: dict
