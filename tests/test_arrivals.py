import asyncio
import logging

import pytest

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import make_settings

from evohome_helper import arrivals
from evohome_helper.arrivals import ArrivalListener


def _trigger_event(from_state, to_state="home"):
    # shaped like the event Home Assistant sends when a subscribed state trigger fires
    trigger = {
        "platform": "state",
        "entity_id": "person.a",
        "from_state": {"entity_id": "person.a", "state": from_state, "attributes": {}},
        "to_state": {"entity_id": "person.a", "state": to_state, "attributes": {}},
    }
    return {"id": 1, "type": "event", "event": {"variables": {"trigger": trigger}}}


class FakeHomeAssistantWebsocket:
    """Speaks the server side of Home Assistant's websocket API. Each connection gets the
    next list of scripted messages; it is then dropped if more are scripted, and kept
    open (as Home Assistant does) if it was the last."""

    def __init__(self, *scripts, token="token"):
        self._scripts = list(scripts)
        self._token = token
        self.connections = 0
        self.subscriptions = []

    async def handle(self, request):
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        self.connections += 1

        await websocket.send_json({"type": "auth_required", "ha_version": "2026.11.0"})
        auth = await websocket.receive_json()
        if auth != {"type": "auth", "access_token": self._token}:
            await websocket.send_json({"type": "auth_invalid", "message": "Invalid access token or password"})
            await websocket.close()
            return websocket
        await websocket.send_json({"type": "auth_ok", "ha_version": "2026.11.0"})

        subscription = await websocket.receive_json()
        self.subscriptions.append(subscription)
        await websocket.send_json({"id": subscription["id"], "type": "result", "success": True, "result": None})

        for message in self._scripts.pop(0) if self._scripts else []:
            await websocket.send_json(message)

        if self._scripts:
            await websocket.close()
        else:
            await websocket.receive()  # until the client goes away
        return websocket


@pytest.fixture(autouse=True)
def fast_reconnects(monkeypatch):
    monkeypatch.setattr(arrivals, "_RECONNECT_DELAY_MIN", 0.01)
    monkeypatch.setattr(arrivals, "_RECONNECT_DELAY_MAX", 0.02)


@pytest.fixture
async def listen():
    """Run an ArrivalListener against the given fake until the condition holds; returns
    the number of arrivals it reported."""
    servers = []

    async def _listen(homeassistant, until, **settings_overrides):
        app = web.Application()
        app.router.add_get("/websocket", homeassistant.handle)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)

        settings = make_settings(homeassistant_websocket_url=str(server.make_url("/websocket")), **settings_overrides)
        reported = []
        task = asyncio.create_task(ArrivalListener(settings).run(lambda: reported.append(1)))
        try:
            async with asyncio.timeout(2):
                while not until(reported):
                    await asyncio.sleep(0.005)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return len(reported)

    yield _listen

    for server in servers:
        await server.close()


async def test_subscribes_to_the_presence_entities_arriving_home(listen):
    homeassistant = FakeHomeAssistantWebsocket([_trigger_event("not_home")])

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
            _trigger_event("home"),  # an attribute update while home
            {"id": 2, "type": "pong"},
            _trigger_event("unavailable"),
            _trigger_event("Work"),
        ]
    )

    # the messages arrive in order, so the ignored ones were seen before the second arrival
    assert await listen(homeassistant, until=lambda reported: len(reported) >= 2) == 2


async def test_reconnects_when_the_connection_is_dropped(listen, caplog):
    homeassistant = FakeHomeAssistantWebsocket([_trigger_event("not_home")], [], [_trigger_event("not_home")])

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
    listener = ArrivalListener(make_settings(homeassistant_presence_entities=[]))

    await asyncio.wait_for(listener.run(lambda: None), timeout=1)
