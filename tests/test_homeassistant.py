import asyncio

from dataclasses import replace

import pytest

from aioresponses import aioresponses
from conftest import FakeHomeAssistantWebsocket, trigger_event

from evohome_helper.homeassistant import HomeAssistantClient


def test_headers_contains_bearer_token(settings):
    client = HomeAssistantClient(settings)
    headers = client._headers()

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["content-type"] == "application/json"


async def test_get_entity_state_returns_payload(settings):
    client = HomeAssistantClient(settings)
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", payload={
            "state": "home",
            "attributes": {"seconds_since_last_seen": 12},
        })
        result = await client.get_entity_state("person.a")

    await client.close()
    assert result == {"state": "home", "attributes": {"seconds_since_last_seen": 12}}


async def test_get_entity_state_returns_none_on_failure(settings):
    client = HomeAssistantClient(settings)
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", status=500)
        result = await client.get_entity_state("person.a")

    await client.close()
    assert result is None


async def test_get_entity_state_failure_log_does_not_leak_the_token(settings, caplog):
    # the repr of a ClientResponseError embeds the request headers, i.e. the bearer token
    client = HomeAssistantClient(replace(settings, homeassistant_token="s3cret-supervisor-token"))
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", status=404)
        await client.get_entity_state("person.a")

    await client.close()
    assert "failed getting the state of entity 'person.a': ClientResponseError: 404" in caplog.text
    assert "s3cret-supervisor-token" not in caplog.text


async def test_session_is_reused_until_closed(settings):
    client = HomeAssistantClient(settings)

    first = client._get_session()
    second = client._get_session()
    assert first is second

    await client.close()
    third = client._get_session()
    assert third is not first

    await client.close()


_TRIGGER = {"platform": "state", "entity_id": ["person.a"], "to": "home"}


@pytest.fixture
async def websocket_client(settings, serve_websocket):
    """Build a HomeAssistantClient whose websocket reaches the given fake."""
    clients = []

    async def _build(homeassistant):
        client = HomeAssistantClient(replace(settings, homeassistant_websocket_url=await serve_websocket(homeassistant)))
        clients.append(client)
        return client

    yield _build

    for client in clients:
        await client.close()


async def test_subscribe_trigger_authenticates_and_yields_the_variables_of_each_firing(websocket_client):
    homeassistant = FakeHomeAssistantWebsocket([trigger_event("not_home"), {"id": 2, "type": "pong"}, trigger_event("Work")])
    client = await websocket_client(homeassistant)
    received = []

    async with asyncio.timeout(2), client.subscribe_trigger(_TRIGGER) as firings:
        async for variables in firings:
            received.append(variables["trigger"]["from_state"]["state"])
            if len(received) == 2:
                break

    # only events are firings
    assert received == ["not_home", "Work"]
    assert homeassistant.subscriptions == [{"id": 1, "type": "subscribe_trigger", "trigger": _TRIGGER}]


async def test_subscribe_trigger_raises_when_the_connection_is_lost(websocket_client):
    homeassistant = FakeHomeAssistantWebsocket([trigger_event("not_home")], [])
    client = await websocket_client(homeassistant)
    received = []

    with pytest.raises(ConnectionError, match="closed"):
        async with asyncio.timeout(2), client.subscribe_trigger(_TRIGGER) as firings:
            async for variables in firings:
                received.append(variables)

    assert len(received) == 1


async def test_subscribe_trigger_raises_on_rejected_credentials(websocket_client):
    client = await websocket_client(FakeHomeAssistantWebsocket(token="another-token"))

    with pytest.raises(ConnectionError, match="authentication failed: Invalid access token"):
        async with asyncio.timeout(2), client.subscribe_trigger(_TRIGGER):
            pytest.fail("entered without a subscription")


async def test_subscribe_trigger_raises_on_a_rejected_subscription(websocket_client):
    client = await websocket_client(FakeHomeAssistantWebsocket(accept_subscriptions=False))

    with pytest.raises(ConnectionError, match="subscribing failed.*invalid_format"):
        async with asyncio.timeout(2), client.subscribe_trigger(_TRIGGER):
            pytest.fail("entered without a subscription")
