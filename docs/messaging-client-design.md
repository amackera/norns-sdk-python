# Messaging Client Design (NornsClient)

`NornsClient` is the non-worker SDK surface for application code (web backends, Slack bots, CLIs).

## Responsibilities

- Resolve agent by id or name
- Send messages to `/api/v1/agents/:id/messages`
- Return `run_id` + accepted status immediately
- Optionally wait for completion by polling `/api/v1/runs/:id`
- Fetch run events via `/api/v1/runs/:id/events`
- Manage conversations via `/api/v1/agents/:id/conversations`
- Stream live events through Phoenix WebSocket topic `agent:<id>`

## Message lifecycle

1. `send_message(...)` posts content (+ optional `conversation_key`)
2. API returns `{status: "accepted", run_id: <id>}`
3. Client either:
   - returns immediately (fire-and-forget), or
   - polls run status until terminal state (`completed|failed|error`)
4. Optional streaming path subscribes to `agent:<id>` while run is active

## Design choices

- Keep worker and client as separate concerns (`Norns` vs `NornsClient`)
- Prefer simple polling for `wait=True` in v0.1
- Preserve strong typed response models for SDK users

## Known limits (v0.1)

- No server push completion callback in REST path (polling only)
- Name-based lookup is currently client-side list-and-filter
- Streaming tests should be expanded with an integration suite
