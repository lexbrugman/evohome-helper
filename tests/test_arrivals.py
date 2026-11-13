import asyncio
import logging

import pytest

from conftest import FakeHomeAssistantWebsocket, make_settings, trigger_event

from evohome_helper import arrivals
from evohome_helper.arrivals import ArrivalListener
from evohome_helper.homeassistant import HomeAssistantClient


@pytest.fixture(autouse=True)
def fast_reconnects(monkeypatch):
    monkeypatch.setattr(arrivals, "_RECONNECT_DELAY_MIN", 0.01)
    monkeypatch.setattr(arrivals, "_RECONNECT_DELAY_MAX", 0.02)


@pytest.fixture
async def listen(serve_websocket):
    """Run an ArrivalListener against the given fake until the condition holds; returns
    the number of arrivals it reported."""

    async def _listen(homeassistant, until):
        settings = make_settings(homeassistant_websocket_url=await serve_websocket(homeassistant))
        client = HomeAssistantClient(settings)
        reported = []
        task = asyncio.create_task(ArrivalListener(client, settings).run(lambda: reported.append(1)))
        try:
            async with asyncio.timeout(2):
                while not until(reported):
                    await asyncio.sleep(0.005)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.close()
        return len(reported)

    return _listen


async def test_subscribes_to_the_presence_entities_arriving_home(listen):
    homeassistant = FakeHomeAssistantWebsocket([trigger_event("not_home")])

    assert await listen(homeassistant, until=lambda reported: reported) == 1

    assert homeassistant.subscriptions == [
        {
            "id": 1,
            "type": "subscribe_trigger",
            "trigger": {"platform": "state", "entity_id": ["person.a", "person.b"], "to": "home"},
        }
    ]


async def test_only_a_move_into_home_is_an_arrival(listen):
    homeassistant = FakeHomeAssistantWebsocket(
        [
            trigger_event("home"),  # an attribute update while home
            {"id": 2, "type": "pong"},
            trigger_event("unavailable"),
            trigger_event("Work"),
        ]
    )

    # the messages arrive in order, so the ignored ones were seen before the second arrival
    assert await listen(homeassistant, until=lambda reported: len(reported) >= 2) == 2


async def test_reconnects_when_the_connection_is_dropped(listen, caplog):
    homeassistant = FakeHomeAssistantWebsocket([trigger_event("not_home")], [], [trigger_event("not_home")])

    assert await listen(homeassistant, until=lambda reported: len(reported) >= 2) == 2

    assert homeassistant.connections == 3
    assert len(homeassistant.subscriptions) == 3
    # each drop followed a working connection, so each is an outage of its own
    assert sum(record.levelno == logging.WARNING for record in caplog.records) == 2


async def test_keeps_retrying_rejected_credentials_but_warns_once(listen, caplog):
    homeassistant = FakeHomeAssistantWebsocket(token="another-token")

    assert await listen(homeassistant, until=lambda reported: homeassistant.connections >= 3) == 0

    assert homeassistant.subscriptions == []
    warnings = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "authentication failed" in warnings[0]


async def test_does_not_listen_without_presence_entities():
    # returns instead of connecting (to a host that does not even exist here)
    settings = make_settings(homeassistant_presence_entities=[])
    listener = ArrivalListener(HomeAssistantClient(settings), settings)

    await asyncio.wait_for(listener.run(lambda: None), timeout=1)
