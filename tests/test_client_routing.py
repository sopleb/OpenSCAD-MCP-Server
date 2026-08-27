"""Model work and user decisions both travel to the connected client."""

import os

import pytest
from conftest import unwrap

from mcp import Client
from mcp.types import ElicitResult

from src.mcp_bridge.delegation import DelegationStore, PendingNotFound


def accept_all(recorder):
    async def handler(context, params):
        recorder.append(params.message)
        return ElicitResult(action="accept", content={"approved": True, "reason": ""})

    return handler


def accept_first(recorder):
    async def handler(context, params):
        recorder.append(params.message)
        approved = len(recorder) == 1
        return ElicitResult(action="accept", content={"approved": approved, "reason": ""})

    return handler


async def decline(context, params):
    return ElicitResult(action="decline")


async def cancel(context, params):
    return ElicitResult(action="cancel")


async def test_approval_keeps_what_the_user_accepts(server, images):
    asked = []
    async with Client(server.mcp, mode="legacy", elicitation_callback=accept_all(asked)) as client:
        result = unwrap(await client.call_tool("approve_images", {"image_paths": images}))

    assert len(asked) == len(images)
    assert result["status"] == "ok"
    assert len(result["approved"]) == 3
    assert result["rejected"] == []


async def test_approval_records_rejections(server, images):
    asked = []
    async with Client(server.mcp, mode="legacy", elicitation_callback=accept_first(asked)) as client:
        result = unwrap(await client.call_tool("approve_images", {"image_paths": images}))

    assert len(result["approved"]) == 1
    assert len(result["rejected"]) == 2
    # One approval is under the minimum, and the caller is told so.
    assert result["status"] == "too_few_approved"
    assert result["minimum_required"] == 3


async def test_declining_approves_nothing(server, images):
    async with Client(server.mcp, mode="legacy", elicitation_callback=decline) as client:
        result = unwrap(await client.call_tool("approve_images", {"image_paths": images}))

    assert result["approved"] == []
    assert len(result["rejected"]) == 3


async def test_cancelling_stops_the_review(server, images):
    async with Client(server.mcp, mode="legacy", elicitation_callback=cancel) as client:
        result = unwrap(await client.call_tool("approve_images", {"image_paths": images}))

    assert result["status"] == "cancelled"
    assert result["reviewed"] == 0


async def test_approval_degrades_without_a_back_channel(server, images):
    """A transport with no server-initiated requests must not fail the tool."""
    async with Client(server.mcp) as client:
        result = await client.call_tool("approve_images", {"image_paths": images})

    assert result.is_error is False
    payload = unwrap(result)
    assert payload["status"] == "elicitation_unavailable"
    assert payload["images"] == images
    assert payload["hint"]


async def test_unknown_delegation_id_is_reported(server):
    async with Client(server.mcp) as client:
        result = unwrap(
            await client.call_tool(
                "submit_model_input", {"request_id": "req_missing", "response": {}}
            )
        )
    assert result["status"] == "unknown_request"


def test_delegation_envelope_addresses_the_caller():
    store = DelegationStore()
    envelope = store.request(
        prompt="Name this shape.",
        response_schema={"type": "object", "properties": {"name": {"type": "string"}}},
    )

    assert envelope["status"] == "needs_model_input"
    assert envelope["resume_with"] == "submit_model_input"
    assert envelope["request_id"] in envelope["instructions"]


def test_delegation_resumes_with_the_callers_answer():
    store = DelegationStore()
    envelope = store.request(
        prompt="Double it.",
        response_schema={"type": "object"},
        resume=lambda response: response["value"] * 2,
    )

    assert store.resolve(envelope["request_id"], {"value": 21}) == 42
    # One answer settles it; a replay finds nothing pending.
    with pytest.raises(PendingNotFound):
        store.resolve(envelope["request_id"], {"value": 21})


def test_delegation_discards_expired_requests():
    store = DelegationStore(ttl_seconds=0)
    envelope = store.request(prompt="Anyone there?", response_schema={})
    with pytest.raises(PendingNotFound):
        store.resolve(envelope["request_id"], {})
