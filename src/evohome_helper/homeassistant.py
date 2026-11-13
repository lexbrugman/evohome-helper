import asyncio
import logging

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp

from settings import Settings

logger = logging.getLogger(__name__)

_SUBSCRIPTION_ID = 1
# connecting, authenticating and subscribing together
_HANDSHAKE_TIMEOUT = 10
# a connection that died silently (Home Assistant restarted, the network dropped) is
# only noticed by pinging it
_HEARTBEAT = 30


class HomeAssistantClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def get_entity_state(self, entity_id: str) -> dict | None:
        url = f"{self._settings.homeassistant_url}/api/states/{entity_id}"

        try:
            async with self._get_session().get(url, headers=self._headers()) as response:
                response.raise_for_status()
                return await response.json()
        except Exception as error:
            # a single warning line per failure keeps the log readable during an outage;
            # never %r: the repr of a ClientResponseError embeds the request headers,
            # i.e. the bearer token
            logger.warning("failed getting the state of entity '%s': %s: %s", entity_id, type(error).__name__, error)
            return None

    @asynccontextmanager
    async def subscribe_trigger(self, trigger: dict) -> AsyncIterator[AsyncIterator[dict]]:
        """Subscribe to a trigger over the websocket API. Entered once Home Assistant has
        accepted the subscription; yields the variables of each firing, and raises when
        the connection is lost. Any failure raises: reconnecting is the caller's call."""
        # the session's request timeout only covers the connection upgrade, not the
        # lifetime of the websocket
        async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
            websocket = await self._get_session().ws_connect(self._settings.homeassistant_websocket_url, heartbeat=_HEARTBEAT)

        async with websocket:
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                await self._authenticate(websocket)
                # a trigger instead of the state_changed event: Home Assistant then only
                # sends what is asked for, not every state change of every entity
                await websocket.send_json({"id": _SUBSCRIPTION_ID, "type": "subscribe_trigger", "trigger": trigger})

                response = await websocket.receive_json()
                if not response.get("success"):
                    raise ConnectionError(f"subscribing failed: {response.get('error')}")

            yield self._firings(websocket)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _authenticate(self, websocket: aiohttp.ClientWebSocketResponse) -> None:
        # Home Assistant opens with auth_required and answers the token with auth_ok or auth_invalid
        await websocket.receive_json()
        await websocket.send_json({"type": "auth", "access_token": self._settings.homeassistant_token})

        response = await websocket.receive_json()
        if response.get("type") != "auth_ok":
            raise ConnectionError(f"authentication failed: {response.get('message')}")

    @staticmethod
    async def _firings(websocket: aiohttp.ClientWebSocketResponse) -> AsyncIterator[dict]:
        async for message in websocket:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue

            data = message.json()
            if data.get("type") == "event":
                yield data.get("event", {}).get("variables", {})

        raise ConnectionError("the connection was closed")

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))

        return self._session

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.homeassistant_token}",
            "content-type": "application/json",
        }
