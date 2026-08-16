"""Worker-side gard support: join payload, claim failures, register_port."""

import pytest

from norns import Agent, Norns
from norns.client import GardDestroyed, JoinError, JoinRetryable


@pytest.fixture
def norns():
    return Norns("http://localhost:4000", api_key="nrn_test")


@pytest.fixture
def agent():
    return Agent(name="test-agent", system_prompt="test")


def test_join_payload_without_gard(norns, agent):
    payload = norns._join_payload(agent, "w1")

    assert payload["worker_id"] == "w1"
    assert "gard" not in payload
    assert "claim_token" not in payload


def test_join_payload_with_gard(norns, agent):
    norns._gard = 42
    norns._claim_token = "tok_abc"

    payload = norns._join_payload(agent, "w1")

    assert payload["gard"] == 42
    assert payload["claim_token"] == "tok_abc"


def _error_reply(reason):
    return [None, "1", "worker:lobby", "phx_reply", {"status": "error", "response": {"reason": reason}}]


def test_join_reply_ok_passes(norns):
    ok = [None, "1", "worker:lobby", "phx_reply", {"status": "ok", "response": {}}]
    norns._check_join_reply(ok)  # no raise


def test_fatal_claim_failures_raise_join_error(norns):
    for reason in ("invalid_claim_token", "gard_destroyed", "not_found"):
        with pytest.raises(JoinError, match=reason):
            norns._check_join_reply(_error_reply(reason))


def test_already_claimed_is_retryable(norns):
    with pytest.raises(JoinRetryable, match="already_claimed"):
        norns._check_join_reply(_error_reply("already_claimed"))


def test_register_port_requires_a_gard(norns):
    with pytest.raises(RuntimeError, match="gard"):
        norns.register_port(3000)


def test_register_port_requires_a_connection(norns):
    norns._gard = 42
    with pytest.raises(RuntimeError, match="connected"):
        norns.register_port(3000)


def test_gard_destroyed_is_exported():
    from norns import GardDestroyed as exported

    assert exported is GardDestroyed


def test_run_reads_gard_from_environment(norns, agent, monkeypatch):
    """A provisioner hands the worker its gard via env (volund sets these)."""
    monkeypatch.setenv("NORNS_GARD", "7")
    monkeypatch.setenv("NORNS_GARD_CLAIM_TOKEN", "tok_env")
    monkeypatch.setattr(Norns, "_ensure_agent", lambda self, a: None)

    async def noop_loop(self, a, wid):
        pass

    monkeypatch.setattr(Norns, "_run_loop", noop_loop)

    norns.run(agent)

    assert norns._gard == "7"
    assert norns._claim_token == "tok_env"


def test_run_explicit_gard_beats_environment(norns, agent, monkeypatch):
    monkeypatch.setenv("NORNS_GARD", "7")
    monkeypatch.setenv("NORNS_GARD_CLAIM_TOKEN", "tok_env")
    monkeypatch.setattr(Norns, "_ensure_agent", lambda self, a: None)

    async def noop_loop(self, a, wid):
        pass

    monkeypatch.setattr(Norns, "_run_loop", noop_loop)

    norns.run(agent, gard=9, claim_token="tok_param")

    assert norns._gard == 9
    assert norns._claim_token == "tok_param"
