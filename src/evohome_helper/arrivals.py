import asyncio
import logging

from collections.abc import Callable

import aiohttp

from settings import Settings

logger = logging.getLogger(__name__)

_SUBSCRIPTION_ID = 1
# connecting, authenticating and subscribing together
_HANDSHAKE_TIMEOUT = 10
# a connection that died silently (Home Assistant restarted, the network dropped) is
# only noticed by pinging it
_HEARTBEAT = 30
_RECONNECT_DELAY_MIN = 5
_RECONNECT_DELAY_MAX = 300


def _is_arrival(message: dict) -> bool:
    if message.get("type") != "event":
        return False

    # the trigger should not fire while the state stays "home" (person entities update
    # their attributes with every location fix), but each wake costs a vendor API call,
    # so do not depend on it
    trigger = message.get("event", {}).get("variables", {}).get("trigger", {})
    from_state = trigger.get("from_state") or {}
    return from_state.get("state") != "home"


class ArrivalListener:
    """Tells the control loop the moment someone arrives home, which polling notices up
    to an interval later.

    Only a signal: the loop still reads presence itself, so a missed or spurious event
    costs nothing but time, and polling carries on while the connection is down."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._subscribed = False

    async def run(self, on_arrival: Callable[[], None]) -> None:
        """Listen until cancelled, reconnecting whenever the connection is lost."""
        if not self._settings.homeassistant_presence_entities:
            return

        delay = _RECONNECT_DELAY_MIN
        warned = False

        while True:
            self._subscribed = False
            try:
                await self._listen(on_arrival)
            except Exception as error:
                reason = f"{type(error).__name__}: {error}"

            if self._subscribed:
                # it worked for a while: a new outage, so start over
                delay = _RECONNECT_DELAY_MIN
                warned = False

            # warn once per outage, not on every reconnect attempt
            if not warned:
                logger.warning("not listening for arrivals, falling back to polling until reconnected: %s", reason)
                warned = True

            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_DELAY_MAX)

    async def _listen(self, on_arrival: Callable[[], None]) -> None:
        async with aiohttp.ClientSession() as session:
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                websocket = await session.ws_connect(self._settings.homeassistant_websocket_url, heartbeat=_HEARTBEAT)

            async with websocket:
                async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                    await self._authenticate(websocket)
                    await self._subscribe(websocket)

                self._subscribed = True
                logger.info("listening for arrivals")

                async for message in websocket:
                    if message.type == aiohttp.WSMsgType.TEXT and _is_arrival(message.json()):
                        on_arrival()

        raise ConnectionError("the connection was closed")

    async def _authenticate(self, websocket: aiohttp.ClientWebSocketResponse) -> None:
        # Home Assistant opens with auth_required and answers the token with auth_ok or auth_invalid
        await websocket.receive_json()
        await websocket.send_json({"type": "auth", "access_token": self._settings.homeassistant_token})

        response = await websocket.receive_json()
        if response.get("type") != "auth_ok":
            raise ConnectionError(f"authentication failed: {response.get('message')}")

    async def _subscribe(self, websocket: aiohttp.ClientWebSocketResponse) -> None:
        # a trigger instead of the state_changed event: Home Assistant then only sends
        # what is asked for, not every state change of every entity
        await websocket.send_json(
            {
                "id": _SUBSCRIPTION_ID,
                "type": "subscribe_trigger",
                "trigger": {
                    "platform": "state",
                    "entity_id": self._settings.homeassistant_presence_entities,
                    "to": "home",
                },
            }
        )

        response = await websocket.receive_json()
        if not response.get("success"):
            raise ConnectionError(f"subscribing failed: {response.get('error')}")
